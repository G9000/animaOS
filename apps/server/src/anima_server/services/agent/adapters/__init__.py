from __future__ import annotations

from anima_server.config import settings

from .base import BaseLLMAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .scaffold import ScaffoldAdapter


def build_adapter() -> BaseLLMAdapter:
    provider = settings.agent_provider

    if provider == "scaffold":
        return ScaffoldAdapter()

    return OpenAICompatibleAdapter.create()
