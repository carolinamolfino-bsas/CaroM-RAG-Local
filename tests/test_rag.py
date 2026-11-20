"""Tests offline del sistema RAG: ingesta, persistencia, recuperación y generación grounded.

Usan ChromaDB real (en una carpeta temporal) con embeddings determinísticos y un
LLM simulado, así corren sin red, sin Ollama y sin API keys.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest
from llm_client import ManagerSettings, ModelConfig
from pydantic import ValidationError

from config import EmbeddingsSettings, RAGSettings
from ingest import ingest, load_documents, split_documents, token_length
from rag import (
    NO_LO_SE,
    EmbeddingMismatchError,
    IndiceNoDisponibleError,
    RAGError,
    RAGSistema,
)
from tests.fakes import FakeLLM, HashingEmbeddings

DATA = Path(__file__).parent.parent / "data"
PREGUNTA_SEV1 = "¿Cuál es el tiempo máximo de respuesta de la guardia ante un incidente SEV1?"


def run(coro):
    return asyncio.run(coro)


def make_settings(tmp_path: Path, **kw) -> RAGSettings:
    data_dir = tmp_path / "data"
    if not data_dir.exists():
        shutil.copytree(DATA, data_dir)
    return RAGSettings(
        data_dir=data_dir,
        persist_dir=tmp_path / "vectorstore",
        embeddings=EmbeddingsSettings(model=kw.pop("embedding_model", "hashing-test")),
        llm=ManagerSettings(provider="openai", api_key="k", model=ModelConfig(model="fake")),
        **kw,
    )


def json_llm(respuesta: str, encontrada: bool, fragmentos: list[int]) -> str:
    return json.dumps({"respuesta": respuesta, "encontrada": encontrada,
                       "fragmentos_usados": fragmentos}, ensure_ascii=False)


@pytest.fixture
def indexado(tmp_path):
    settings = make_settings(tmp_path)
    ingest(settings, HashingEmbeddings())
    return settings


def sistema(settings, *respuestas) -> tuple[RAGSistema, FakeLLM]:
    llm = FakeLLM(respuestas=list(respuestas))
    return RAGSistema(settings, embeddings=HashingEmbeddings(), llm=llm), llm


# ---------------------------------------------------------------- dataset y chunking

def test_tiktoken_real_si_esta_disponible(monkeypatch):
    """En CI (con internet) se verifica el conteo real de tokens de cl100k_base."""
    import ingest as modulo

    monkeypatch.setenv("RAG_TOKENIZER", "tiktoken")
    modulo._tokenizer.cache_clear()
    try:
        import tiktoken

        tiktoken.get_encoding("cl100k_base")
    except Exception:
        pytest.skip("tiktoken no puede descargar su tabla sin conexión")
    chunks = split_documents(load_documents(DATA), 500, 50)
    assert all(token_length(c.page_content) <= 500 for c in chunks)
    modulo._tokenizer.cache_clear()


def test_dataset_tiene_entre_3_y_4_documentos_largos():
    docs = load_documents(DATA)
    assert 3 <= len(docs) <= 4
    assert all(token_length(d.page_content) > 500 for d in docs)  # cada uno genera varios chunks


def test_chunks_respetan_tamano_y_tienen_metadatos():
    chunks = split_documents(load_documents(DATA), chunk_size=500, chunk_overlap=50)
    assert len(chunks) > len(load_documents(DATA))
    for c in chunks:
        assert c.metadata["tokens"] <= 500
        assert c.metadata["chunk_id"] == f"{c.metadata['fuente']}#{c.metadata['chunk']}"
        assert c.metadata["seccion"]
    assert len({c.metadata["chunk_id"] for c in chunks}) == len(chunks)


def test_chunks_se_solapan():
    texto = " ".join(f"palabra{i}." for i in range(600))
    from langchain_core.documents import Document

    doc = Document(page_content=texto, metadata={"fuente": "x.txt", "titulo": "x", "sha256": "0"})
    a, b, *_ = split_documents([doc], 500, 50, length_function=lambda t: len(t.split()))
    assert a.page_content.split()[-1] in b.page_content.split()[:60]


@pytest.mark.parametrize("cambio", [{"top_k": 6}, {"top_k": 2}, {"chunk_size": 300},
                                    {"chunk_size": 500, "chunk_overlap": 500}])
def test_settings_validan_consigna(tmp_path, cambio):
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **cambio)


# ---------------------------------------------------------------- persistencia

def test_no_reindexa_si_nada_cambio(tmp_path):
    settings = make_settings(tmp_path)
    primero = ingest(settings, HashingEmbeddings())
    segundo = ingest(settings, HashingEmbeddings())
    assert primero.indexado and primero.motivo == "no existía el índice"
    assert not segundo.indexado and segundo.fragmentos == primero.fragmentos


def test_reindexa_si_cambia_un_documento(tmp_path):
    settings = make_settings(tmp_path)
    ingest(settings, HashingEmbeddings())
    archivo = settings.data_dir / "04_guardias.md"
    archivo.write_text(archivo.read_text(encoding="utf-8") + "\nNuevo párrafo.\n", encoding="utf-8")
    r = ingest(settings, HashingEmbeddings())
    assert r.indexado and "cambiaron los documentos" in r.motivo


def test_reindexa_si_cambia_el_modelo_de_embeddings(tmp_path):
    ingest(make_settings(tmp_path), HashingEmbeddings())
    r = ingest(make_settings(tmp_path, embedding_model="otro-modelo"), HashingEmbeddings())
    assert r.indexado and "cambió el modelo de embeddings" in r.motivo


def test_rebuild_fuerza_reindexacion(indexado):
    assert ingest(indexado, HashingEmbeddings(), rebuild=True).indexado


# ---------------------------------------------------------------- protecciones

def test_sin_indice_da_error_claro(tmp_path):
    with pytest.raises(IndiceNoDisponibleError, match="python ingest.py"):
        sistema(make_settings(tmp_path))


def test_embeddings_distintos_al_consultar(indexado, tmp_path):
    otra = indexado.model_copy(update={"embeddings": EmbeddingsSettings(model="otro-modelo")})
    with pytest.raises(EmbeddingMismatchError, match="--rebuild"):
        sistema(otra)


# ---------------------------------------------------------------- recuperación

def test_recupera_top_k_fragmentos_relevantes(indexado):
    s, _ = sistema(indexado)
    docs = run(s.vectorstore.asimilarity_search_with_relevance_scores(PREGUNTA_SEV1, k=indexado.top_k))
    assert len(docs) == indexado.top_k
    assert "02_gestion_de_incidentes.md" in {d.metadata["fuente"] for d, _ in docs}
    assert [s for _, s in docs] == sorted((s for _, s in docs), reverse=True)


# ---------------------------------------------------------------- generación grounded

def test_respuesta_con_referencias_validadas(indexado):
    s, llm = sistema(indexado, json_llm("La guardia tiene 10 minutos desde la alerta.", True, [1]))
    r = run(s.get_rag_response(PREGUNTA_SEV1))
    assert r.encontrada and "10 minutos" in r.respuesta
    system, human = llm.registro["mensajes"][0]
    fuente_1 = human.content.split("[1] fuente: ")[1].split(" ")[0]
    assert r.referencias[0].fragmento == 1 and r.referencias[0].fuente == fuente_1
    assert 0 <= r.referencias[0].relevancia <= 1
    assert "FILTRO DE VERACIDAD" in system.content and '"fragmentos_usados"' in system.content
    assert human.content.count("fuente:") == indexado.top_k  # top_k fragmentos, no más


def test_pregunta_trampa_responde_no_lo_se(indexado):
    s, _ = sistema(indexado, json_llm("No lo sé", False, []))
    r = run(s.get_rag_response("¿Cuál es el presupuesto anual de infraestructura de Kaleido Pagos?"))
    assert not r.encontrada and r.respuesta == NO_LO_SE and r.referencias == []


def test_respuesta_sin_referencias_validas_se_descarta(indexado):
    s, _ = sistema(indexado, json_llm("Usa Kubernetes 1.29", True, [9]))
    r = run(s.get_rag_response(PREGUNTA_SEV1))
    assert not r.encontrada and r.respuesta == NO_LO_SE
    assert any("inexistentes: [9]" in a for a in r.avisos)


def test_json_en_bloque_de_codigo(indexado):
    s, _ = sistema(indexado, "```json\n" + json_llm("10 minutos.", True, [1, 2]) + "\n```")
    r = run(s.get_rag_response(PREGUNTA_SEV1))
    assert r.encontrada and [x.fragmento for x in r.referencias] == [1, 2]


def test_reintenta_si_el_json_es_invalido(indexado):
    s, llm = sistema(indexado, "esto no es JSON", json_llm("10 minutos.", True, [1]))
    assert run(s.get_rag_response(PREGUNTA_SEV1)).encontrada
    assert llm.registro["llamadas"] == 2


def test_agota_reintentos_error_controlado(indexado):
    s, llm = sistema(indexado, '{"respuesta": "incompleto"')
    with pytest.raises(RAGError) as e:
        run(s.get_rag_response(PREGUNTA_SEV1))
    assert e.value.tipo == "salida_invalida" and llm.registro["llamadas"] == indexado.output_retries


def test_error_del_proveedor_controlado(indexado):
    s, _ = sistema(indexado, ConnectionError("Ollama no responde"))
    with pytest.raises(RAGError) as e:
        run(s.get_rag_response(PREGUNTA_SEV1))
    assert e.value.tipo == "proveedor"


def test_pregunta_vacia(indexado):
    s, _ = sistema(indexado, json_llm("x", True, [1]))
    with pytest.raises(RAGError):
        run(s.get_rag_response("   "))


def test_consultas_concurrentes(indexado):
    s, _ = sistema(indexado, json_llm("10 minutos.", True, [1]))

    async def varias():
        return await asyncio.gather(*(s.get_rag_response(PREGUNTA_SEV1) for _ in range(4)))

    assert all(r.encontrada for r in run(varias()))


# ---------------------------------------------------------------- end-to-end por HTTP

def test_main_end_to_end_contra_servidor_que_imita_a_ollama(tmp_path):
    """main.py completo: ingesta + 2 pruebas, con OllamaEmbeddings y ChatOpenAI reales por HTTP."""
    import os
    import subprocess
    import sys

    from tests.fake_ollama_server import FakeOllamaServer

    raiz = Path(__file__).parent.parent
    with FakeOllamaServer() as server:
        env = {
            **os.environ,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
            "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "ollama", "OPENAI_MODEL": "qwen2.5:7b",
            "OPENAI_BASE_URL": f"{server.url}/v1",
            "EMBEDDINGS_PROVIDER": "ollama", "EMBEDDINGS_MODEL": "bge-m3",
            "EMBEDDINGS_BASE_URL": server.url,
            "RAG_DATA_DIR": str(DATA), "RAG_PERSIST_DIR": str(tmp_path / "vs"),
            "LLM_MAX_RETRIES": "0", "RAG_TOKENIZER": "aproximado",
        }
        salida = tmp_path / "ejecucion.json"
        cmd = [sys.executable, "main.py", "--guardar", str(salida)]
        r1 = subprocess.run(cmd, cwd=raiz, env=env, capture_output=True, text=True, timeout=120)
        r2 = subprocess.run(cmd, cwd=raiz, env=env, capture_output=True, text=True, timeout=120)

    assert r1.returncode == 0, r1.stdout + r1.stderr
    assert "no existía el índice" in r1.stdout and "Resumen: 2/2" in r1.stdout
    assert "sin cambios" in r2.stdout and "Resumen: 2/2" in r2.stdout  # segunda vez no reindexa
    registro = json.loads(salida.read_text(encoding="utf-8"))
    sev1, trampa = (p["resultado"] for p in registro["pruebas"])
    assert sev1["encontrada"] and sev1["referencias"][0]["fuente"] == "02_gestion_de_incidentes.md"
    assert not trampa["encontrada"] and trampa["respuesta"] == NO_LO_SE
    assert "/api/embed" in server.paths
    pedido = server.chat_requests[0]
    assert pedido["model"] == "qwen2.5:7b" and "max_tokens" in pedido
