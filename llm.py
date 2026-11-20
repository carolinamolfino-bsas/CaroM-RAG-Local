"""Creación del chat model de LangChain a partir de la configuración del Módulo 1.

Misma lógica que la Pre-entrega 2 (CaroM-Entity-Pipeline): ChatOpenAI,
ChatAnthropic, o un servidor compatible con OpenAI (Ollama) vía OPENAI_BASE_URL.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.base import LanguageModelInput
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from llm_client import ManagerSettings, Provider, anthropic_supports_sampling

logger = logging.getLogger("rag")


class ChatOpenAICompatible(ChatOpenAI):
    """ChatOpenAI para servidores compatibles (Ollama, LM Studio, vLLM...).

    LangChain renombra max_tokens a max_completion_tokens (exclusivo de la API
    oficial de OpenAI); los servidores compatibles documentan max_tokens.
    """

    def _get_request_payload(
        self, input_: LanguageModelInput, *, stop: list[str] | None = None, **kwargs: Any
    ) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        return payload


def build_chat_model(settings: ManagerSettings) -> BaseChatModel:
    api_key = settings.require_api_key()  # MissingAPIKeyError con mensaje claro
    cfg = settings.model

    if settings.provider is Provider.OPENAI:
        clase = ChatOpenAICompatible if settings.base_url else ChatOpenAI
        if settings.base_url:
            logger.info("LLM en servidor compatible con OpenAI: %s", settings.base_url)
        return clase(
            model=cfg.model,
            base_url=settings.base_url,
            api_key=api_key,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,  # type: ignore[call-arg]
            timeout=cfg.timeout,
            max_retries=cfg.max_retries,
        )

    from langchain_anthropic import ChatAnthropic

    kwargs: dict = {}
    if anthropic_supports_sampling():
        kwargs["temperature"] = min(cfg.temperature, 1.0)
    elif "temperature" in cfg.model_fields_set:
        logger.warning("LLM_TEMPERATURE ignorada para Anthropic (la API ya no la acepta).")
    return ChatAnthropic(  # type: ignore[call-arg]
        model=cfg.model,
        api_key=api_key,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout,
        max_retries=cfg.max_retries,
        **kwargs,
    )
