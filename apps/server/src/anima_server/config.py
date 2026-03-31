from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / ".anima" / "dev"
DEFAULT_DATABASE_URL = "sqlite:///" + str(DEFAULT_DATA_DIR / "anima.db")


class Settings(BaseSettings):
    app_name: str = "ANIMA Server"
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 3031
    database_url: str = DEFAULT_DATABASE_URL
    database_echo: bool = False
    data_dir: Path = DEFAULT_DATA_DIR
    runtime_database_url: str = ""
    runtime_pg_data_dir: str = ""
    runtime_pool_size: int = 5
    runtime_pool_max_overflow: int = 10
    agent_provider: str = "ollama"
    agent_model: str = "vaultbox/qwen3.5-uncensored:35b"
    agent_persona_template: str = "default"
    agent_base_url: str = ""
    agent_api_key: str = ""
    agent_max_steps: int = 6
    agent_max_concurrent_spawns: int = 10
    agent_spawn_timeout: float = 300.0
    agent_spawn_max_steps: int = 4
    agent_max_tokens: int = 4096
    agent_compaction_trigger_ratio: float = 0.8
    agent_compaction_keep_last_messages: int = 8
    agent_stream_chunk_size: int = 48
    agent_llm_timeout: float = 120.0
    agent_llm_retry_limit: int = 3
    agent_llm_retry_backoff_factor: float = 0.5
    agent_llm_retry_max_delay: float = 10.0
    agent_context_overflow_retry: bool = True
    agent_tool_timeout: float = 30.0
    agent_stream_queue_max_size: int = 256
    agent_background_memory_enabled: bool = True
    core_passphrase: str = ""
    core_require_encryption: bool = True
    agent_extraction_model: str = ""
    agent_extraction_provider: str = ""
    agent_embedding_provider: str = ""
    agent_embedding_model: str = ""
    agent_embedding_api_key: str = ""
    agent_embedding_base_url: str = ""
    agent_embedding_dim: int = 768
    agent_session_memory_max_notes: int = 20
    agent_session_memory_budget_chars: int = 1500
    agent_self_model_identity_budget: int = 1000
    agent_self_model_inner_state_budget: int = 800
    agent_self_model_working_memory_budget: int = 600
    agent_self_model_growth_log_budget: int = 600
    agent_self_model_intentions_budget: int = 1000
    agent_emotional_context_budget: int = 500
    agent_emotional_signal_buffer_size: int = 20
    agent_emotional_confidence_threshold: float = 0.4
    agent_emotional_patterns_budget: int = 400
    message_ttl_days: int = 30
    transcript_retention_days: int = -1
    consolidation_health_threshold_minutes: int = 30
    sidecar_nonce: str = ""
    health_log_dir: str = ""
    health_log_retention_days: int = 7
    health_log_level: Literal["trace", "info", "warn", "error"] = "info"

    model_config = SettingsConfigDict(
        env_prefix="ANIMA_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )


settings = Settings()


# ---------------------------------------------------------------------------
# Embedding dimension resolution (lives here to avoid circular imports
# between models/ and services/agent/)
# ---------------------------------------------------------------------------

KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
}

_DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "vllm": "text-embedding-3-small",
}

_detected_embedding_dim: int | None = None


def set_detected_embedding_dim(dim: int) -> None:
    global _detected_embedding_dim
    _detected_embedding_dim = dim


def clear_detected_embedding_dim() -> None:
    global _detected_embedding_dim
    _detected_embedding_dim = None


def resolve_embedding_dim() -> int:
    """Return the embedding dimension for the active model.

    Priority: detected at runtime > known lookup > config fallback.
    """
    if _detected_embedding_dim is not None:
        return _detected_embedding_dim
    model = settings.agent_embedding_model.strip() or settings.agent_extraction_model.strip()
    if not model:
        embed_provider = settings.agent_embedding_provider.strip() or settings.agent_provider
        model = _DEFAULT_EMBEDDING_MODELS.get(embed_provider, "nomic-embed-text")
    if model in KNOWN_EMBEDDING_DIMS:
        return KNOWN_EMBEDDING_DIMS[model]
    return settings.agent_embedding_dim
