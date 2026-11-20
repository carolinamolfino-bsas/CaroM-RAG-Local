"""Dobles de prueba: embeddings y LLM que funcionan sin red ni API keys."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_STOP = set("el la los las un una unos unas de del al a en y o que se con por para es son "
            "su sus como mas más cuanto cuánto cual cuál qué que tiene tienen".split())


def _palabras(texto: str) -> list[str]:
    texto = unicodedata.normalize("NFKD", texto.lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return [p for p in re.findall(r"[a-z0-9]+", texto) if p not in _STOP and len(p) > 1]


class HashingEmbeddings(Embeddings):
    """Bolsa de palabras "hasheada" y normalizada: similitud = palabras compartidas.

    No es semántica como un modelo real, pero es determinística y alcanza para
    probar que la ingesta, ChromaDB y la recuperación funcionan de punta a punta.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    def _embed(self, texto: str) -> list[float]:
        v = [0.0] * self.dim
        for p in _palabras(texto):
            v[int(hashlib.md5(p.encode()).hexdigest(), 16) % self.dim] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class FakeLLM(BaseChatModel):
    """Devuelve `respuestas` en orden (texto o Exception) y guarda los mensajes recibidos."""

    respuestas: list[Any]
    registro: dict = Field(default_factory=lambda: {"llamadas": 0, "mensajes": []})

    @property
    def _llm_type(self) -> str:
        return "fake-llm"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kw) -> ChatResult:
        i = self.registro["llamadas"]
        self.registro["llamadas"] += 1
        self.registro["mensajes"].append(messages)
        r = self.respuestas[min(i, len(self.respuestas) - 1)]
        if isinstance(r, Exception):
            raise r
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=r))])
