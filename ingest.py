"""Módulo de ingesta: /data -> chunks de ~500 tokens -> ChromaDB persistente.

    python ingest.py             # indexa sólo si los documentos o la configuración cambiaron
    python ingest.py --rebuild   # fuerza la reindexación completa

Persistencia: la colección guarda en sus metadatos un "manifiesto" (hash de
cada documento + modelo de embeddings + parámetros de chunking). Si al volver
a correr nada cambió, se omite la indexación: no se recalculan embeddings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.config import Settings as ChromaSettings
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import RAGSettings, build_embeddings

logger = logging.getLogger("rag")

EXTENSIONES = {".md", ".txt"}

# Separadores (regex) de mayor a menor: primero se intenta cortar antes de un
# encabezado de Markdown, después por párrafos, líneas, fin de oración y, como
# último recurso, palabras. Los lookahead/lookbehind cortan sin perder el
# "## " del título ni el punto final de la oración.
SEPARADORES = [r"\n(?=## )", r"\n(?=### )", r"\n(?=#### )", r"\n\n", r"\n",
               r"(?<=[.!?:]) ", r" ", r""]

_HEADER = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


# --------------------------------------------------------------------------- tokens

def _aproximado(text: str) -> int:
    return max(1, len(text) // 4)


@lru_cache(maxsize=1)
def _tokenizer() -> Callable[[str], int]:
    """Cuenta tokens con tiktoken (cl100k_base).

    tiktoken descarga su tabla la primera vez; sin conexión (o con
    RAG_TOKENIZER=aproximado) se aproxima 1 token ≈ 4 caracteres.
    """
    if os.getenv("RAG_TOKENIZER", "tiktoken").lower() == "aproximado":
        return _aproximado
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(encoding.encode(text))
    except Exception as exc:  # noqa: BLE001 - sin red la primera vez, tiktoken no puede bajar su tabla
        logger.warning("tiktoken no disponible (%s): se aproxima 1 token ≈ 4 caracteres.", exc)
        return _aproximado


def token_length(text: str) -> int:
    return _tokenizer()(text)


# --------------------------------------------------------------------------- carga y chunking

def load_documents(data_dir: Path) -> list[Document]:
    """Lee los .md y .txt de la carpeta (no recursivo), en orden alfabético."""
    if not data_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de documentos: {data_dir}")
    docs = []
    for path in sorted(data_dir.iterdir()):
        if path.suffix.lower() not in EXTENSIONES or not path.is_file():
            continue
        texto = path.read_text(encoding="utf-8")
        if not texto.strip():
            logger.warning("Se omite %s: está vacío.", path.name)
            continue
        titulo = next((m.group(2) for m in _HEADER.finditer(texto) if m.group(1) == "#"), path.stem)
        docs.append(Document(
            page_content=texto,
            metadata={
                "fuente": path.name,
                "titulo": titulo,
                "sha256": hashlib.sha256(texto.encode("utf-8")).hexdigest(),
            },
        ))
    if not docs:
        raise FileNotFoundError(f"No hay archivos .md o .txt en {data_dir}")
    return docs


def _seccion_en(texto: str, posicion: int) -> str | None:
    """Último encabezado de Markdown que aparece antes de `posicion`."""
    seccion = None
    for m in _HEADER.finditer(texto):
        if m.start() > posicion:
            break
        seccion = m.group(2)
    return seccion


def split_documents(
    docs: list[Document],
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    length_function: Callable[[str], int] = token_length,
) -> list[Document]:
    """Chunking estratégico con RecursiveCharacterTextSplitter, medido en TOKENS.

    Cada fragmento conserva la fuente, la sección de Markdown a la que pertenece
    y un id estable (`archivo#n`), que después se usa en las referencias.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=length_function,
        separators=SEPARADORES,
        is_separator_regex=True,
        add_start_index=True,
        keep_separator="start",
    )
    chunks: list[Document] = []
    for doc in docs:
        partes = splitter.split_documents([doc])
        for i, parte in enumerate(partes):
            texto_original = doc.page_content
            parte.metadata.update({
                "chunk": i,
                "chunk_id": f"{doc.metadata['fuente']}#{i}",
                "seccion": _seccion_en(texto_original, parte.metadata.get("start_index", 0))
                or doc.metadata["titulo"],
                "tokens": length_function(parte.page_content),
            })
            parte.metadata.pop("sha256", None)
            chunks.append(parte)
    return chunks


# --------------------------------------------------------------------------- persistencia

def chroma_client(persist_dir: Path) -> chromadb.ClientAPI:
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(persist_dir), settings=ChromaSettings(anonymized_telemetry=False)
    )


def build_manifest(docs: list[Document], settings: RAGSettings) -> dict[str, str | int]:
    """Huella de lo indexado: si no cambia, no hace falta reindexar."""
    archivos = {d.metadata["fuente"]: d.metadata["sha256"] for d in docs}
    huella = json.dumps(
        {"archivos": archivos, "embedding_id": settings.embeddings.embedding_id,
         "chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
        sort_keys=True,
    )
    return {
        "manifest_sha256": hashlib.sha256(huella.encode()).hexdigest(),
        "embedding_id": settings.embeddings.embedding_id,
        "chunk_size": settings.chunk_size,
        "chunk_overlap": settings.chunk_overlap,
        "archivos": json.dumps(sorted(archivos)),
    }


def collection_metadata(client: chromadb.ClientAPI, name: str) -> dict | None:
    """Metadatos de la colección, o None si no existe."""
    try:
        return dict(client.get_collection(name).metadata or {})
    except Exception:  # noqa: BLE001 - chromadb lanza distintos tipos según la versión
        return None


@dataclass
class IngestResult:
    indexado: bool          # False = se reutilizó el índice existente
    documentos: int
    fragmentos: int
    motivo: str


def ingest(
    settings: RAGSettings,
    embeddings: Embeddings | None = None,
    *,
    rebuild: bool = False,
) -> IngestResult:
    """Indexa /data en ChromaDB si hace falta. Devuelve qué se hizo y por qué."""
    docs = load_documents(settings.data_dir)
    manifest = build_manifest(docs, settings)
    client = chroma_client(settings.persist_dir)
    actual = collection_metadata(client, settings.collection)

    if actual and not rebuild and actual.get("manifest_sha256") == manifest["manifest_sha256"]:
        total = client.get_collection(settings.collection).count()
        if total > 0:
            logger.info("Índice al día (%d fragmentos): se omite la indexación.", total)
            return IngestResult(False, len(docs), total, "sin cambios: se reutiliza el índice")

    if rebuild:
        motivo = "reindexación forzada (--rebuild)"
    elif actual is None:
        motivo = "no existía el índice"
    elif actual.get("embedding_id") != manifest["embedding_id"]:
        motivo = (f"cambió el modelo de embeddings ({actual.get('embedding_id')} -> "
                  f"{manifest['embedding_id']})")
    else:
        motivo = "cambiaron los documentos o los parámetros de chunking"

    chunks = split_documents(docs, settings.chunk_size, settings.chunk_overlap)
    logger.info("Indexando %d fragmentos de %d documentos (%s)...", len(chunks), len(docs), motivo)

    if actual is not None:
        client.delete_collection(settings.collection)

    vectorstore = Chroma(
        client=client,
        collection_name=settings.collection,
        embedding_function=embeddings or build_embeddings(settings.embeddings),
        collection_metadata={
            "hnsw:space": "cosine",  # relevancia = 1 - distancia coseno
            **manifest,
            "indexado_en": datetime.now().isoformat(timespec="seconds"),
        },
    )
    vectorstore.add_documents(chunks, ids=[c.metadata["chunk_id"] for c in chunks])
    return IngestResult(True, len(docs), len(chunks), motivo)


# --------------------------------------------------------------------------- CLI

def main() -> int:
    parser = argparse.ArgumentParser(description="Ingesta de documentos en ChromaDB")
    parser.add_argument("--rebuild", action="store_true", help="Reindexar todo aunque no haya cambios")
    parser.add_argument("--ver-chunks", action="store_true",
                        help="Mostrar los fragmentos generados (sin indexar)")
    args = parser.parse_args()

    settings = RAGSettings.from_env()

    if args.ver_chunks:
        chunks = split_documents(load_documents(settings.data_dir),
                                 settings.chunk_size, settings.chunk_overlap)
        for c in chunks:
            print(f"{c.metadata['chunk_id']:<36} {c.metadata['tokens']:>4} tokens · "
                  f"{c.metadata['seccion']}")
        print(f"\n{len(chunks)} fragmentos")
        return 0

    resultado = ingest(settings, rebuild=args.rebuild)
    accion = "Indexado" if resultado.indexado else "Sin cambios"
    print(f"{accion}: {resultado.documentos} documentos, {resultado.fragmentos} fragmentos "
          f"en {settings.persist_dir}/ (colección '{settings.collection}', "
          f"embeddings {settings.embeddings.embedding_id}). Motivo: {resultado.motivo}.")
    return 0


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(levelname)-7s %(name)s: %(message)s")
    sys.exit(main())
