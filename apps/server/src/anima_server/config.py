import json
import logging
import os
import sys
import tempfile
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
    runtime_app_data_dir: str = ""
    runtime_instance_data_dir: str = ""
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
    # Sleep-agent auto-reparse: once the Docling parsing pack finishes
    # downloading, automatically re-parse preview/legacy-quality documents
    # that were ingested before it was ready, closing the loop without
    # requiring a manual reparse click.
    document_auto_reparse: Literal["off", "on"] = "on"
    # Documents re-parsed per sleep cycle. Docling parsing runs
    # synchronously and can take minutes on large PDFs, so this keeps a
    # single cycle bounded.
    document_auto_reparse_budget: int = 2
    # After Docling fails on a specific document (parse_degraded), the
    # auto-reparse loop records the failure and excludes that document from
    # candidacy for this many hours — so one persistently-unparseable file
    # can't monopolize the per-cycle budget and starve valid documents behind
    # it, while still allowing a periodic retry in case the failure was
    # transient. Set to 0 to disable the cooldown (retry every cycle).
    document_auto_reparse_failure_cooldown_hours: int = 24
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
    # IL3 drive accumulators + push initiative (see
    # services/agent/inner_life/drives.py, inner_life/initiative.py). The
    # feature itself is off by default at PresenceConfig.initiative_enabled
    # (a per-user opt-in column, not a setting) — these values only take
    # effect once a user turns it on.
    initiative_cooldown_base_hours: float = Field(default=24.0, ge=1.0)
    initiative_cooldown_min_hours: float = Field(default=8.0, ge=0.0)
    initiative_cooldown_backoff_factor: float = Field(default=1.5, ge=1.0)
    initiative_cooldown_max_hours: float = Field(default=168.0, ge=1.0)
    initiative_max_per_day: int = Field(default=1, ge=0)
    initiative_max_per_week: int = Field(default=3, ge=0)
    initiative_pressure_leak_tau_hours: float = Field(default=240.0, ge=1.0)
    initiative_theta_unresolved_thread: float = Field(default=0.7, ge=0.0, le=1.0)
    initiative_theta_pattern_insight: float = Field(default=0.7, ge=0.0, le=1.0)
    initiative_theta_relational: float = Field(default=0.7, ge=0.0, le=1.0)
    initiative_theta_novelty: float = Field(default=0.7, ge=0.0, le=1.0)
    initiative_theta_dream_residue: float = Field(default=0.7, ge=0.0, le=1.0)
    initiative_growth_unresolved_thread: float = Field(default=0.10, ge=0.0)
    initiative_growth_pattern_insight: float = Field(default=0.08, ge=0.0)
    initiative_growth_relational: float = Field(default=0.05, ge=0.0)
    initiative_growth_novelty: float = Field(default=0.05, ge=0.0)
    initiative_growth_dream_residue: float = Field(default=0.05, ge=0.0)
    # IL-013 starvation carryover: each selection loss by an above-theta
    # drive adds this much to its future ranking, up to the cap. The boost
    # affects ranking only, never theta qualification.
    initiative_starvation_boost_per_loss: float = Field(default=0.03, ge=0.0)
    initiative_starvation_boost_cap: float = Field(default=0.15, ge=0.0)
    # IL-011 reconnect texture: bounded energy dip applied by offline
    # catch-up after a long absence (energy only — never valence).
    presence_reconnect_dip_min_gap_hours: float = Field(default=48.0, ge=0.0)
    presence_reconnect_dip_per_day: float = Field(default=0.01, ge=0.0)
    presence_reconnect_dip_cap: float = Field(default=0.06, ge=0.0, le=1.0)
    # IL-011 held thought: greeting-context gate values (see proactive.py).
    greeting_held_thought_min_gap_hours: float = Field(default=8.0, ge=0.0)
    greeting_held_thought_min_pressure: float = Field(default=0.3, ge=0.0, le=1.0)
    # Contact-cadence proxy: no learned per-relationship cadence model
    # exists yet, so "relational" grows once days-since-contact exceeds this
    # fixed baseline (documented proxy — see initiative.py).
    initiative_relational_cadence_days: float = Field(default=3.0, ge=0.1)
    initiative_unresolved_thread_horizon_days: float = Field(default=3.0, ge=0.0)
    # Closeness proxy: relationship age (days since first contact) saturates
    # linearly to full closeness (1.0) at this many days — see
    # initiative.resolve_closeness_signal for why no structured closeness
    # scalar is available yet.
    initiative_closeness_full_days: float = Field(default=120.0, ge=1.0)
    initiative_novelty_episode_window: int = Field(default=6, ge=1)
    initiative_novelty_repetition_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    initiative_novelty_energy_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    # IL5 forgetting-as-distillation (see services/agent/distillation.py).
    # Pure DB work with no LLM calls, but capped per sleep run to bound
    # work — ge=1 so a misconfigured cap can't silently disable the sweep.
    distill_max_per_run: int = Field(default=20, ge=1)
    # IL6 recall reconsolidation (see services/agent/reconsolidation.py).
    # eta is the PRD-default nudge step; dream_eta is IL-007's
    # reduced-strength touch on the same edge. The lifetime drift cap
    # bounds cumulative |Δemotional_salience| from reconsolidation only,
    # per item, forever.
    reconsolidation_eta: float = Field(default=0.05, ge=0.0, le=1.0)
    reconsolidation_dream_eta: float = Field(default=0.02, ge=0.0, le=1.0)
    reconsolidation_lifetime_drift_cap: float = Field(default=0.3, ge=0.0, le=1.0)
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
    if settings.runtime_instance_data_dir:
        return (
            Path(settings.runtime_instance_data_dir)
            / "config"
            / RUNTIME_SETTINGS_FILENAME
        )
    app_data_root = (
        Path(settings.runtime_app_data_dir)
        if settings.runtime_app_data_dir
        else default_runtime_app_data_root()
    )
    return app_data_root / "unbound" / "config" / RUNTIME_SETTINGS_FILENAME


def default_runtime_app_data_root() -> Path:
    """Return the machine-local application-data root without touching CoreFS."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        configured = os.environ.get("XDG_DATA_HOME")
        root = Path(configured) if configured else Path.home() / ".local" / "share"
    return (root / "anima").expanduser().resolve()


def resolve_runtime_path_outside_core(path: Path, *, setting_name: str) -> Path:
    """Resolve a machine-local Runtime path and reject portable-Core overlap."""
    resolved = path.expanduser().resolve()
    portable_core = settings.data_dir.expanduser().resolve()
    if resolved.is_relative_to(portable_core) or portable_core.is_relative_to(
        resolved
    ):
        raise RuntimeError(f"{setting_name} must not overlap the portable Core")
    return resolved


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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
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


def resolve_embedding_dim() -> int:
    """Return the embedding dimension for the active model.

    Priority: detected at runtime > known lookup > config fallback.

    Provider/model resolution (including the fastembed-skips-extraction-
    model rule) is delegated to
    ``services.agent.embedding_resolution`` — the single copy shared with
    ``services.agent.embeddings``, imported lazily here (module scope would
    create an import cycle: ``embedding_resolution`` is imported by this
    module and must not import ``config`` back at module scope).
    """
    if _detected_embedding_dim is not None:
        return _detected_embedding_dim

    from anima_server.services.agent.embedding_resolution import (
        resolve_embedding_model,
        resolve_embedding_provider,
    )

    embed_provider = resolve_embedding_provider()
    model = resolve_embedding_model(embed_provider)
    if model in KNOWN_EMBEDDING_DIMS:
        return KNOWN_EMBEDDING_DIMS[model]
    normalized_model = _normalize_embedding_model_name(model)
    if normalized_model in KNOWN_EMBEDDING_DIMS:
        return KNOWN_EMBEDDING_DIMS[normalized_model]
    return settings.agent_embedding_dim
