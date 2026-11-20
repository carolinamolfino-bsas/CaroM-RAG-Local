"""Cadena RAG asíncrona: pregunta -> ChromaDB (top_k) -> prompt -> LLM -> Pydantic.

Cadena LCEL (ver build_rag_chain):

    {"pregunta", "fragmentos": retriever}          # 1. recuperación (embedding + similitud)
      | assign(contexto = formatear_contexto)      # 2. transformador de documentos
      | assign(salida_llm = prompt | llm | PydanticOutputParser)   # 3. generación grounded
      | armar_respuesta                            # 4. validación de referencias

Garantías contra alucinaciones:
- El prompt exige responder SOLO con el CONTEXTO y decir "No lo sé" si no alcanza.
- Las referencias que devuelve el modelo se validan contra los fragmentos
  realmente recuperados; una respuesta sin referencias válidas se descarta.
- Si la búsqueda no devuelve fragmentos, ni siquiera se llama al LLM.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import (
    Runnable,
    RunnableBranch,
    RunnableLambda,
    RunnableParallel,
    RunnablePassthrough,
)
from pydantic import BaseModel, Field, ValidationError

from config import RAGSettings, build_embeddings
from ingest import chroma_client, collection_metadata
from llm import build_chat_model

logger = logging.getLogger("rag")

NO_LO_SE = "No lo sé. Esa información no está en los documentos disponibles."


# --------------------------------------------------------------------------- modelos Pydantic

class RespuestaLLM(BaseModel):
    """Lo que el LLM debe devolver (lo valida PydanticOutputParser)."""

    respuesta: str = Field(
        min_length=1,
        description="Respuesta en español, basada SOLO en el contexto. "
                    "Si el contexto no alcanza, exactamente: 'No lo sé'.",
    )
    encontrada: bool = Field(
        description="true si el contexto contiene la respuesta; false si no.",
    )
    fragmentos_usados: list[int] = Field(
        default_factory=list,
        description="Números de los fragmentos del contexto (ej. [1, 3]) que respaldan "
                    "la respuesta. Vacío si encontrada es false.",
    )


class Referencia(BaseModel):
    fragmento: int = Field(description="Número del fragmento en el contexto")
    fuente: str = Field(description="Archivo de origen")
    seccion: str | None = None
    relevancia: float = Field(ge=0, le=1, description="Similitud coseno con la pregunta")
    extracto: str = Field(description="Primeros caracteres del fragmento")


class RAGResponse(BaseModel):
    """Respuesta final: texto + referencias verificadas."""

    pregunta: str
    respuesta: str
    encontrada: bool
    referencias: list[Referencia] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)


class RAGError(Exception):
    """Error controlado de get_rag_response (proveedor caído, salida inválida, etc.)."""

    def __init__(self, message: str, *, tipo: str, causa: BaseException | None = None):
        super().__init__(message)
        self.tipo = tipo
        self.causa = causa


class IndiceNoDisponibleError(RAGError):
    def __init__(self, message: str):
        super().__init__(message, tipo="indice")


class EmbeddingMismatchError(RAGError):
    """Se intenta consultar con un modelo de embeddings distinto al usado al indexar."""

    def __init__(self, message: str):
        super().__init__(message, tipo="embeddings")


# --------------------------------------------------------------------------- prompt

SYSTEM_TEMPLATE = """Sos un asistente técnico de la plataforma Orion. Actuás como un \
FILTRO DE VERACIDAD.

Reglas obligatorias:
1. Respondé ÚNICAMENTE con información que esté en el CONTEXTO. No uses conocimiento propio.
2. Si el CONTEXTO no contiene la respuesta, respondé exactamente "No lo sé", con \
"encontrada": false y "fragmentos_usados": [].
3. Si el CONTEXTO responde sólo una parte, respondé esa parte y aclará qué falta.
4. Indicá en "fragmentos_usados" los números [n] de TODOS los fragmentos que usaste.
5. No inventes datos, números, nombres ni fechas.

Formato de salida:
{format_instructions}"""

HUMAN_TEMPLATE = """CONTEXTO:
{contexto}

PREGUNTA: {pregunta}"""


def build_prompt(parser: PydanticOutputParser) -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [("system", SYSTEM_TEMPLATE), ("human", HUMAN_TEMPLATE)]
    ).partial(format_instructions=parser.get_format_instructions())


# --------------------------------------------------------------------------- pasos de la cadena

def formatear_contexto(fragmentos: list[Document]) -> str:
    """Transformador de documentos: lista de fragmentos -> texto numerado para el prompt."""
    bloques = []
    for i, doc in enumerate(fragmentos, start=1):
        m = doc.metadata
        bloques.append(f"[{i}] fuente: {m.get('fuente')} · sección: {m.get('seccion')}\n"
                       f"{doc.page_content.strip()}")
    return "\n\n---\n\n".join(bloques)


def armar_respuesta(datos: dict[str, Any]) -> RAGResponse:
    """Convierte la salida del LLM en RAGResponse, validando las referencias."""
    pregunta: str = datos["pregunta"]
    fragmentos: list[Document] = datos["fragmentos"]
    salida: RespuestaLLM = datos["salida_llm"]
    avisos: list[str] = []

    if not fragmentos:
        avisos.append("La búsqueda no devolvió fragmentos: no se llamó al LLM.")
        return RAGResponse(pregunta=pregunta, respuesta=NO_LO_SE, encontrada=False, avisos=avisos)

    if not salida.encontrada:
        return RAGResponse(pregunta=pregunta, respuesta=NO_LO_SE, encontrada=False, avisos=avisos)

    validos = sorted({n for n in salida.fragmentos_usados if 1 <= n <= len(fragmentos)})
    invalidos = sorted(set(salida.fragmentos_usados) - set(validos))
    if invalidos:
        avisos.append(f"Se descartaron referencias a fragmentos inexistentes: {invalidos}")

    if not validos:
        # Una respuesta sin respaldo en el contexto no es "grounded": se descarta.
        avisos.append("El modelo respondió sin citar fragmentos válidos: respuesta descartada.")
        logger.warning("Respuesta sin referencias válidas descartada: %r", salida.respuesta[:200])
        return RAGResponse(pregunta=pregunta, respuesta=NO_LO_SE, encontrada=False, avisos=avisos)

    referencias = []
    for n in validos:
        doc = fragmentos[n - 1]
        referencias.append(Referencia(
            fragmento=n,
            fuente=doc.metadata.get("fuente", "?"),
            seccion=doc.metadata.get("seccion"),
            relevancia=round(max(0.0, min(1.0, float(doc.metadata.get("relevancia", 0.0)))), 3),
            extracto=" ".join(doc.page_content.split())[:160],
        ))
    return RAGResponse(pregunta=pregunta, respuesta=salida.respuesta.strip(), encontrada=True,
                       referencias=referencias, avisos=avisos)


def build_retriever(vectorstore: Chroma, top_k: int) -> Runnable:
    """Pregunta -> embedding -> búsqueda de similitud -> top_k fragmentos (con su relevancia)."""

    def _con_relevancia(resultados: list[tuple[Document, float]]) -> list[Document]:
        docs = []
        for doc, score in resultados:
            doc.metadata["relevancia"] = float(score)
            docs.append(doc)
        logger.info("Recuperados %d fragmentos: %s", len(docs),
                    ", ".join(f"{d.metadata.get('chunk_id')} ({d.metadata['relevancia']:.2f})"
                              for d in docs))
        return docs

    def buscar(pregunta: str) -> list[Document]:
        return _con_relevancia(vectorstore.similarity_search_with_relevance_scores(pregunta, k=top_k))

    async def abuscar(pregunta: str) -> list[Document]:
        return _con_relevancia(
            await vectorstore.asimilarity_search_with_relevance_scores(pregunta, k=top_k)
        )

    return RunnableLambda(buscar, afunc=abuscar, name="retriever_chroma")


def build_rag_chain(
    vectorstore: Chroma, llm: BaseChatModel, *, top_k: int = 4, output_retries: int = 3
) -> Runnable:
    """Ensambla la cadena RAG completa (entrada: la pregunta como str)."""
    parser = PydanticOutputParser(pydantic_object=RespuestaLLM)

    generar = (build_prompt(parser) | llm | parser).with_retry(
        # Si el modelo devuelve JSON mal formado o incompleto, se vuelve a intentar.
        retry_if_exception_type=(OutputParserException, ValidationError),
        stop_after_attempt=output_retries,
        wait_exponential_jitter=False,
    )
    sin_contexto: Runnable = RunnableLambda(
        lambda _: RespuestaLLM(respuesta="No lo sé", encontrada=False), name="sin_contexto"
    )

    return (
        RunnableParallel(pregunta=RunnablePassthrough(), fragmentos=build_retriever(vectorstore, top_k))
        | RunnablePassthrough.assign(contexto=lambda x: formatear_contexto(x["fragmentos"]))
        | RunnablePassthrough.assign(
            salida_llm=RunnableBranch((lambda x: not x["fragmentos"], sin_contexto), generar)
        )
        | RunnableLambda(armar_respuesta, name="armar_respuesta")
    ).with_config(run_name="rag_orion")


# --------------------------------------------------------------------------- sistema

class RAGSistema:
    """Abre el índice existente, verifica los embeddings y expone get_rag_response()."""

    def __init__(
        self,
        settings: RAGSettings,
        *,
        embeddings: Embeddings | None = None,
        llm: BaseChatModel | None = None,
    ) -> None:
        self.settings = settings
        client = chroma_client(settings.persist_dir)
        meta = collection_metadata(client, settings.collection)
        if not meta or client.get_collection(settings.collection).count() == 0:
            raise IndiceNoDisponibleError(
                f"No hay un índice en {settings.persist_dir}/ (colección '{settings.collection}'). "
                "Corré primero: python ingest.py"
            )
        esperado = settings.embeddings.embedding_id
        if meta.get("embedding_id") != esperado:
            # Error #1 de RAG: indexar con un modelo y consultar con otro.
            raise EmbeddingMismatchError(
                f"El índice se creó con embeddings '{meta.get('embedding_id')}' pero la "
                f"configuración actual usa '{esperado}'. Las distancias no serían comparables. "
                "Volvé a la configuración original o corré: python ingest.py --rebuild"
            )
        self.vectorstore = Chroma(
            client=client,
            collection_name=settings.collection,
            embedding_function=embeddings or build_embeddings(settings.embeddings),
        )
        self.chain = build_rag_chain(
            self.vectorstore,
            llm or build_chat_model(settings.llm),
            top_k=settings.top_k,
            output_retries=settings.output_retries,
        )

    async def get_rag_response(self, query: str) -> RAGResponse:
        """Búsqueda de similitud + prompt con fragmentos + LLM async + parseo Pydantic."""
        if not query or not query.strip():
            raise RAGError("La pregunta está vacía", tipo="entrada_invalida")
        inicio = time.perf_counter()
        try:
            respuesta: RAGResponse = await self.chain.ainvoke(query.strip())
        except (OutputParserException, ValidationError) as exc:
            raise RAGError("El modelo no devolvió un JSON válido tras los reintentos",
                           tipo="salida_invalida", causa=exc) from exc
        except Exception as exc:  # red, Ollama apagado, API key inválida...
            raise RAGError(f"Error del proveedor: {type(exc).__name__}: {exc}",
                           tipo="proveedor", causa=exc) from exc
        logger.info("Respuesta en %.0f ms (encontrada=%s)",
                    (time.perf_counter() - inicio) * 1000, respuesta.encontrada)
        return respuesta


_sistema: RAGSistema | None = None


async def get_rag_response(query: str) -> RAGResponse:
    """Función pública pedida por la consigna. Usa la configuración del .env."""
    global _sistema
    if _sistema is None:
        _sistema = RAGSistema(RAGSettings.from_env())
    return await _sistema.get_rag_response(query)
