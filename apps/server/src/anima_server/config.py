import json
import logging
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
SERVER_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / ".anima" / "dev"
DEFAULT_DATABASE_URL = "sqlite:///" + str(DEFAULT_DATA_DIR / "anima.db")
RUNTIME_SETTINGS_FILENAME = "runtime-config.json"

logger = logging.getLogger(__name__)


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
    agent_api_keys_json: str = "{}"  # JSON dict: {provider: api_key}
    agent_max_steps: int = 6
    agent_strict_tool_schemas: bool = True
    agent_max_concurrent_spawns: int = 10
    agent_spawn_timeout: float = 300.0
    agent_spawn_max_steps: int = 4
    agent_max_tokens: int = 4096
    # Model context window in tokens.  When set, the prompt budget
    # (memory blocks + conversation) is derived from it (window minus
    # agent_max_tokens reserved for output); when unset, agent_max_tokens
    # doubles as the legacy context budget.
    agent_context_window_tokens: int | None = None
    # Fraction of the context budget that memory blocks may use; the
    # remainder is left for conversation history.
    agent_block_budget_ratio: float = 0.5
    agent_temperature: float | None = None
    agent_compaction_trigger_ratio: float = 0.8
    agent_compaction_keep_last_messages: int = 8
    agent_stream_chunk_size: int = 48
    agent_llm_timeout: float = 120.0
    # Streaming LLM calls fail when no data arrives for this many seconds
    # (the non-streaming branch uses agent_llm_timeout for the whole call).
    agent_llm_stream_inactivity_timeout: float = 120.0
    agent_llm_retry_limit: int = 3
    agent_llm_retry_backoff_factor: float = 0.5
    agent_llm_retry_max_delay: float = 10.0
    agent_context_overflow_retry: bool = True
    agent_tool_timeout: float = 30.0
    agent_stream_queue_max_size: int = 256
    agent_background_memory_enabled: bool = True
    chat_image_max_size_bytes: int = 10 * 1024 * 1024
    chat_image_max_count: int = 4
    # Document-grounded turns retrieve this many chunks for the context block.
    document_context_chunk_limit: int = 15
    # Raw evidence chunks pass through untruncated up to this safety cap,
    # which only bounds pathological chunks (deliberate chunk size is 1800).
    document_context_chunk_char_cap: int = 2500
    # Document tools (search/outline/read) may return at most this much
    # document text per turn; over-budget calls get a truncation notice.
    document_tool_turn_char_budget: int = 40_000
    # Single read_document_section call cap; longer sections continue via
    # the start_chunk parameter.
    document_tool_read_char_limit: int = 6_000
    # Full-document context: when every selected document's text fits the
    # budget, inject whole documents instead of retrieved chunks (matches
    # cloud-assistant file-upload behavior; retrieval covers what doesn't fit).
    document_full_context: Literal["off", "auto"] = "auto"
    # Fraction of the resolved context budget the full-doc block may use.
    document_full_context_budget_ratio: float = 0.5
    # Hard ceiling in characters regardless of window size.
    document_full_context_char_cap: int = 120_000
    # Contextual retrieval blurbs: when "on", each document chunk gets an
    # LLM-generated context line stored in chunk metadata and prepended to
    # the chunk text for embedding and lexical indexing only (never shown
    # as evidence). On by default; ingestion cost is one extra LLM call per
    # chunk, and any failure degrades to no blurb rather than blocking.
    contextual_chunks: Literal["off", "on"] = "on"
    # Skip blurb generation for documents with more chunks than this.
    contextual_chunks_max_chunks: int = 200
    # Optional cross-encoder rerank stage after RRF fusion. "local" runs a
    # bundled ONNX cross-encoder via fastembed (no extra install); any
    # unavailability (model load or scoring failure) degrades to the fused
    # order.
    retrieval_reranker: Literal["off", "local"] = "local"
    retrieval_reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    retrieval_rerank_candidates: int = 50
    # Knowledge compiler backend: "llm" uses the runtime's configured model
    # (falling back to deterministic when the model is unreachable);
    # "deterministic" forces the stub builder.
    knowledge_compiler: Literal["llm", "deterministic"] = "llm"
    # Sleep-agent auto-compile policy for sources with spans but no compiled
    # concepts: off, markdown_only (cheap, high-signal), or all kinds.
    knowledge_autocompile: Literal["off", "markdown_only", "all"] = "markdown_only"
    knowledge_autocompile_budget_per_cycle: int = 2
    # A source is not re-attempted (success or failure) within this window.
    knowledge_autocompile_cooldown_hours: float = 24.0
    # Server-side URL fetching for web captures is opt-in; the local-first
    # threat model expects clients to supply captured HTML themselves.
    web_capture_url_fetch_enabled: bool = False
    web_capture_url_fetch_max_bytes: int = 5 * 1024 * 1024
    web_capture_url_fetch_timeout: float = 10.0
    diary_attachment_max_size_bytes: int = 100 * 1024 * 1024
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
    # Automatic per-turn retrieval blends relevance with recency and heat
    # (explicit memory searches keep pure relevance ranking).
    agent_retrieval_relevance_weight: float = 0.7
    agent_retrieval_recency_weight: float = 0.2
    agent_retrieval_heat_weight: float = 0.1
    agent_retrieval_recency_tau_days: float = 14.0
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
    # IL1 affect-state relaxation time constants (hours); see
    # services/agent/inner_life/affect.py for the closed-form dynamics.
    inner_life_tau_valence_hours: float = 36.0
    inner_life_tau_arousal_hours: float = 6.0
    inner_life_tau_energy_hours: float = 18.0
    # IL2 presence tick / offline catch-up (see
    # services/agent/inner_life/presence.py and inner_life/catchup.py).
    # ge=1: a non-positive tick interval would spin the loop hot.
    presence_tick_interval_seconds: int = Field(default=60, ge=1)
    presence_active_window_seconds: int = Field(default=120, ge=1)
    presence_catchup_min_gap_seconds: int = Field(default=600, ge=1)
    # In-flight RuntimeRuns older than this no longer count as "active"
    # for the presence tick, so a crashed run stuck in status "running"
    # cannot exclude a user from presence forever.
    presence_run_stale_seconds: int = Field(default=1800, ge=1)
    # IL4 latent trace crystallization (see services/agent/inner_life/latent.py
    # for the pure scoring/fold/decay math). Importance >= 2 candidates bypass
    # the gate entirely (behavior-preservation by construction — see
    # _gate_new_memory_decision in soul_writer.py), so these thresholds only
    # govern the importance-1 weak-signal lane: score >= threshold promotes,
    # [floor_ratio * threshold, threshold) folds into a trace, below rejects.
    latent_promotion_threshold: float = Field(default=0.30, ge=0.0, le=1.0)
    latent_floor_ratio: float = Field(default=0.25, ge=0.0, le=1.0)
    latent_crystallization_threshold: float = Field(default=0.60, ge=0.0, le=1.0)
    latent_fold_rate: float = Field(default=0.5, ge=0.0, le=1.0)
    latent_weekly_decay: float = Field(default=0.98, ge=0.0, le=1.0)
    latent_max_traces_per_user: int = Field(default=500, ge=1)
    message_ttl_days: int = 30
    transcript_retention_days: int = -1
    background_task_run_retention_days: int = 30
    consolidation_health_threshold_minutes: int = 30
    sidecar_nonce: str = ""
    health_log_dir: str = ""
    health_log_retention_days: int = 7
    health_log_level: Literal["trace", "info", "warn", "error"] = "info"
    mod_url: str = "http://127.0.0.1:3034"
    eval_reset_enabled: bool = False

    model_config = SettingsConfigDict(
        env_prefix="ANIMA_",
        env_file=(
            REPO_ROOT / ".env",
            REPO_ROOT / ".env.local",
            SERVER_ROOT / ".env",
            SERVER_ROOT / ".env.local",
        ),
        extra="ignore",
    )


settings = Settings()


def _parse_api_keys() -> dict[str, str]:
    try:
        data = json.loads(settings.agent_api_keys_json or "{}")
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (json.JSONDecodeError, ValueError):
        return {}


def get_provider_api_key(provider: str) -> str:
    return _parse_api_keys().get(provider, "")


def has_provider_api_keys() -> bool:
    return bool(_parse_api_keys())


def set_provider_api_key(provider: str, key: str) -> None:
    keys = _parse_api_keys()
    if key:
        keys[provider] = key
    else:
        keys.pop(provider, None)
    settings.agent_api_keys_json = json.dumps(keys)


_PERSISTED_RUNTIME_SETTING_FIELDS: tuple[str, ...] = (
    "agent_provider",
    "agent_model",
    "agent_persona_template",
    "agent_base_url",
    "agent_api_key",
    "agent_api_keys_json",
    "agent_extraction_model",
    "agent_extraction_provider",
    "agent_embedding_provider",
    "agent_embedding_model",
    "agent_embedding_api_key",
    "agent_embedding_base_url",
)


def get_runtime_settings_path() -> Path:
    """Return the local runtime settings file path."""
    return settings.data_dir / RUNTIME_SETTINGS_FILENAME


def load_persisted_runtime_settings() -> None:
    """Load locally persisted runtime settings into the active process."""
    path = get_runtime_settings_path()
    if not path.exists():
        return

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Failed to read persisted runtime settings from %s: %s", path, exc)
        return

    if not isinstance(payload, dict):
        logger.warning(
            "Ignoring persisted runtime settings from %s: expected object", path)
        return

    for field in _PERSISTED_RUNTIME_SETTING_FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            logger.warning(
                "Ignoring persisted runtime setting %s from %s: expected string",
                field,
                path,
            )
            continue
        setattr(settings, field, value)


def persist_runtime_settings() -> Path:
    """Persist runtime settings for the next server restart."""
    path = get_runtime_settings_path()
    payload: dict[str, str] = {}

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Failed to read existing runtime settings from %s: %s", path, exc)
        else:
            if isinstance(existing, dict):
                payload.update(
                    {
                        key: value
                        for key, value in existing.items()
                        if isinstance(key, str) and isinstance(value, str)
                    }
                )

    for field in _PERSISTED_RUNTIME_SETTING_FIELDS:
        payload[field] = str(getattr(settings, field, ""))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2,
                    sort_keys=True) + "\n", encoding="utf-8")
    return path


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
    "Qwen/Qwen3-Embedding-8B": 4096,
    "BAAI/bge-small-en-v1.5": 384,
}

_DEFAULT_EMBEDDING_MODELS: dict[str, str] = {
    "ollama": "nomic-embed-text",
    "openrouter": "openai/text-embedding-3-small",
    "openai": "text-embedding-3-small",
    "vllm": "text-embedding-3-small",
    "doubleword": "Qwen/Qwen3-Embedding-8B",
    "fastembed": "BAAI/bge-small-en-v1.5",
}

_detected_embedding_dim: int | None = None


def set_detected_embedding_dim(dim: int) -> None:
    global _detected_embedding_dim
    _detected_embedding_dim = dim


def clear_detected_embedding_dim() -> None:
    global _detected_embedding_dim
    _detected_embedding_dim = None


def _normalize_embedding_model_name(model: str) -> str:
    normalized = model.strip()
    if not normalized:
        return normalized
    if ":" in normalized:
        return normalized.rsplit(":", 1)[0]
    return normalized


def _resolve_default_embedding_provider() -> str:
    """Mirror ``embeddings._resolve_embedding_provider()``'s resolution order.

    Duplicated (rather than imported) to avoid a circular import between
    ``config`` and ``services.agent.embeddings``. Keep both in sync: explicit
    ``agent_embedding_provider`` wins; otherwise the bundled ``fastembed``
    provider is the default, except when the user configured embedding
    details (model, base URL, or API key) against their chat provider
    without naming an embedding provider — that legacy piggyback is
    preserved as a real signal of intent.

    KEEP IN SYNC with ``embeddings._resolve_embedding_provider``, which
    duplicates this same piggyback-signal rule.
    """
    configured = settings.agent_embedding_provider.strip()
    if configured:
        return configured
    embedding_model = settings.agent_embedding_model.strip()
    embedding_base_url = getattr(settings, "agent_embedding_base_url", "").strip()
    embedding_api_key = getattr(settings, "agent_embedding_api_key", "").strip()
    if embedding_model or embedding_base_url or embedding_api_key:
        return settings.agent_provider.strip() or "ollama"
    return "fastembed"


def resolve_embedding_dim() -> int:
    """Return the embedding dimension for the active model.

    Priority: detected at runtime > known lookup > config fallback.

    ``agent_extraction_model`` is a CHAT model setting, not embedding intent
    — it is only consulted as a legacy fallback when the resolved provider
    is an explicitly-configured non-fastembed piggyback provider. For the
    bundled ``fastembed`` provider it must be skipped entirely: a chat model
    name (e.g. "qwen2.5:3b") isn't in ``KNOWN_EMBEDDING_DIMS``, so it would
    silently fall through to the 768 config fallback while the bundled
    default model is actually 384-dim.  (Rejected alternative: keep the old
    unconditional piggyback for fastembed too — that reads chat config as
    embedding config; the contract-migration machinery already moves such
    installs onto the bundled default uniformly.)

    KEEP IN SYNC with ``embeddings._resolve_embedding_model``, which
    duplicates this same fastembed-skips-extraction-model rule for model
    resolution.
    """
    if _detected_embedding_dim is not None:
        return _detected_embedding_dim
    embed_provider = _resolve_default_embedding_provider()
    model = settings.agent_embedding_model.strip()
    if not model and embed_provider != "fastembed":
        model = settings.agent_extraction_model.strip()
    if not model:
        model = _DEFAULT_EMBEDDING_MODELS.get(
            embed_provider, "nomic-embed-text")
    if model in KNOWN_EMBEDDING_DIMS:
        return KNOWN_EMBEDDING_DIMS[model]
    normalized_model = _normalize_embedding_model_name(model)
    if normalized_model in KNOWN_EMBEDDING_DIMS:
        return KNOWN_EMBEDDING_DIMS[normalized_model]
    return settings.agent_embedding_dim
