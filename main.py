"""Prueba End-to-End del sistema RAG.

1. Ingesta: indexa /data en ChromaDB (o reutiliza el índice si no cambió nada).
2. Prueba con respuesta en los documentos: debe responder y citar la fuente.
3. Pregunta trampa (no está en los documentos): debe decir "No lo sé", sin alucinar.

Uso:
    python main.py
    python main.py --pregunta "¿Cuándo se puede desplegar el Motor de cobros?"
    python main.py --guardar ejemplos/ejecucion.json      # registro de la corrida
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from llm_client import MissingAPIKeyError
from pydantic import ValidationError

from config import RAGSettings
from ingest import ingest
from rag import RAGError, RAGResponse, RAGSistema


@dataclass
class Prueba:
    nombre: str
    pregunta: str
    debe_encontrar: bool | None          # None = pregunta libre, sin verificación
    fuente_esperada: str | None = None
    texto_esperado: str | None = None    # dato que debe aparecer en la respuesta


PRUEBAS = [
    Prueba(
        nombre="Respuesta en los documentos",
        pregunta="¿Cuál es el tiempo máximo de respuesta de la guardia ante un incidente SEV1?",
        debe_encontrar=True,
        fuente_esperada="02_gestion_de_incidentes.md",
        texto_esperado="10",
    ),
    Prueba(
        nombre="Pregunta trampa (no está en los documentos)",
        pregunta="¿Cuál es el presupuesto anual de infraestructura de Kaleido Pagos?",
        debe_encontrar=False,
    ),
]


def mostrar(prueba: Prueba, r: RAGResponse | RAGError) -> bool:
    print(f"\n{'=' * 72}\n{prueba.nombre}\nPregunta: {prueba.pregunta}\n{'-' * 72}")
    if isinstance(r, RAGError):
        print(f"ERROR CONTROLADO [{r.tipo}]: {r}")
        return False

    print(f"Respuesta: {r.respuesta}")
    for ref in r.referencias:
        print(f"  [{ref.fragmento}] {ref.fuente} · {ref.seccion} · relevancia {ref.relevancia:.2f}")
    for aviso in r.avisos:
        print(f"  AVISO: {aviso}")

    if prueba.debe_encontrar is None:
        return True
    errores = []
    if r.encontrada != prueba.debe_encontrar:
        errores.append(f"encontrada={r.encontrada}, se esperaba {prueba.debe_encontrar}")
    if prueba.fuente_esperada and prueba.fuente_esperada not in {x.fuente for x in r.referencias}:
        errores.append(f"no cita {prueba.fuente_esperada}")
    if prueba.texto_esperado and prueba.texto_esperado not in r.respuesta:
        errores.append(f"la respuesta no menciona '{prueba.texto_esperado}'")
    if errores:
        print("✘ " + "; ".join(errores))
        return False
    print("✔ No alucinó: respondió 'No lo sé'." if not prueba.debe_encontrar
          else "✔ Respondió con el dato correcto y citó la fuente.")
    return True


def guardar(destino: Path, settings: RAGSettings, pruebas: list[Prueba],
            resultados: list[RAGResponse | RAGError], ok: list[bool]) -> None:
    registro = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "llm": {"proveedor": settings.llm.provider.value, "modelo": settings.llm.model.model,
                "base_url": settings.llm.base_url},
        "embeddings": settings.embeddings.embedding_id,
        "chunking": {"chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap},
        "top_k": settings.top_k,
        "pruebas": [
            {"nombre": p.nombre, "comportamiento_esperado": c,
             **({"error": {"tipo": r.tipo, "mensaje": str(r)}} if isinstance(r, RAGError)
                else {"resultado": r.model_dump(mode="json")})}
            for p, r, c in zip(pruebas, resultados, ok, strict=True)
        ],
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Prueba End-to-End del sistema RAG")
    parser.add_argument("--pregunta", help="Hacer sólo esta pregunta")
    parser.add_argument("--guardar", type=Path, help="Guardar la ejecución en un JSON")
    args = parser.parse_args()

    try:
        settings = RAGSettings.from_env()
        resultado = ingest(settings)
        print(f"Índice: {resultado.fragmentos} fragmentos de {resultado.documentos} documentos "
              f"({resultado.motivo}).")
        sistema = RAGSistema(settings)
    except (ValidationError, MissingAPIKeyError, RAGError, FileNotFoundError) as exc:
        print(f"No se pudo iniciar el sistema: {exc}")
        return 2
    except Exception as exc:  # ej. Ollama apagado al indexar
        print(f"No se pudo indexar: {type(exc).__name__}: {exc}\n"
              "¿Está corriendo Ollama y descargaste el modelo de embeddings? (ver README)")
        return 2

    print(f"LLM: {settings.llm.provider}/{settings.llm.model.model} · "
          f"embeddings: {settings.embeddings.embedding_id} · top_k={settings.top_k}")

    pruebas = [Prueba("Pregunta libre", args.pregunta, None)] if args.pregunta else PRUEBAS

    async def responder(p: Prueba) -> RAGResponse | RAGError:
        try:
            return await sistema.get_rag_response(p.pregunta)
        except RAGError as exc:
            return exc

    resultados = await asyncio.gather(*(responder(p) for p in pruebas))
    ok = [mostrar(p, r) for p, r in zip(pruebas, resultados, strict=True)]
    print(f"\nResumen: {sum(ok)}/{len(ok)} pruebas con el comportamiento esperado.")

    if args.guardar:
        guardar(args.guardar, settings, pruebas, list(resultados), ok)
        print(f"Ejecución guardada en {args.guardar}")
    return 0 if all(ok) else 1


if __name__ == "__main__":
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"),
                        format="%(levelname)-7s %(name)s: %(message)s")
    for ruidoso in ("httpx", "httpx2", "openai", "chromadb"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
