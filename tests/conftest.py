"""Conteo de tokens determinístico en los tests (igual con o sin internet)."""

import pytest

import ingest


@pytest.fixture(autouse=True)
def tokenizador_aproximado(monkeypatch):
    monkeypatch.setenv("RAG_TOKENIZER", "aproximado")
    ingest._tokenizer.cache_clear()
    yield
    ingest._tokenizer.cache_clear()
