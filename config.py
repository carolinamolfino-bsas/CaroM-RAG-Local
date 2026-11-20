"""Configuración del sistema RAG (validada con Pydantic, leída del .env).

- El LLM se configura con `llm_client` (CaroM-LLM-Client, Pre-entrega 1):
  proveedor, API key, modelo, temperatura y OPENAI_BASE_URL para Ollama.
- Los embeddings se configuran aparte, porque Anthropic no ofrece embeddings
  y porque el MISMO modelo debe usarse para indexar y para consultar.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path

from langchain_core.embeddings import Embeddings
from llm_client import ManagerSettings, MissingAPIKeyError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class EmbeddingsProvider(StrEnum):
    OLLAMA = "ollama"   # local y gratis (default)
    OPENAI = "openai"   # requiere OPENAI_API_KEY


DEFAULT_EMBEDDING_MODELS = {
    EmbeddingsProvider.OLLAMA: "bge-m3",                  # multilingüe, 8192 tokens de contexto
    EmbeddingsProvider.OPENAI: "text-embedding-3-small",
}


class EmbeddingsSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: EmbeddingsProvider = EmbeddingsProvider.OLLAMA
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: SecretStr | None = None

    @property
    def embedding_id(self) -> str:
        """Identificador que se guarda en la colección para detectar mezclas."""
        return f"{self.provider.value}:{self.model}"

    @classmethod
    def from_env(cls) -> EmbeddingsSettings:
        provider = EmbeddingsProvider((os.getenv("EMBEDDINGS_PROVIDER") or "ollama").lower())
        default_url = "http://localhost:11434" if provider is EmbeddingsProvider.OLLAMA else None
        key = os.getenv("OPENAI_API_KEY") if provider is EmbeddingsProvider.OPENAI else None
        return cls(
            provider=provider,
            model=os.getenv("EMBEDDINGS_MODEL") or DEFAULT_EMBEDDING_MODELS[provider],
            base_url=os.getenv("EMBEDDINGS_BASE_URL") or default_url,
            api_key=SecretStr(key) if key else None,
        )


class RAGSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: Path = Path("data")
    persist_dir: Path = Path("vectorstore")
    collection: str = Field(default="orion_docs", pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,510}[a-zA-Z0-9]$")
    # Consigna: chunks de mínimo 500 tokens con 50 de overlap.
    chunk_size: int = Field(default=500, ge=500, le=4000, description="Tokens por fragmento")
    chunk_overlap: int = Field(default=50, ge=0, description="Tokens compartidos entre fragmentos")
    # Consigna: top_k entre 3 y 5 (evita el "contexto infinito" / lost in the middle).
    top_k: int = Field(default=4, ge=3, le=5)
    output_retries: int = Field(default=3, ge=1, le=10)
    embeddings: EmbeddingsSettings
    llm: ManagerSettings

    @model_validator(mode="after")
    def _overlap_menor_que_chunk(self) -> RAGSettings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap debe ser menor que chunk_size")
        return self

    @classmethod
    def from_env(cls) -> RAGSettings:
        data: dict = {
            "embeddings": EmbeddingsSettings.from_env(),
            "llm": ManagerSettings.from_env(),
        }
        for env, field in (
            ("RAG_DATA_DIR", "data_dir"),
            ("RAG_PERSIST_DIR", "persist_dir"),
            ("RAG_COLLECTION", "collection"),
            ("RAG_CHUNK_SIZE", "chunk_size"),
            ("RAG_CHUNK_OVERLAP", "chunk_overlap"),
            ("RAG_TOP_K", "top_k"),
            ("LLM_OUTPUT_RETRIES", "output_retries"),
        ):
            if (value := os.getenv(env)) not in (None, ""):
                data[field] = value
        return cls(**data)


def build_embeddings(settings: EmbeddingsSettings) -> Embeddings:
    """Crea el modelo de embeddings. Se usa el MISMO en ingesta y en consulta."""
    if settings.provider is EmbeddingsProvider.OLLAMA:
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=settings.model, base_url=settings.base_url)

    if settings.api_key is None:
        raise MissingAPIKeyError(
            "Falta OPENAI_API_KEY para EMBEDDINGS_PROVIDER=openai. "
            "Configurala en .env o usá EMBEDDINGS_PROVIDER=ollama (local y gratis)."
        )
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
    )
