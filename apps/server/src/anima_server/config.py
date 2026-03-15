from pathlib import Path

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
    agent_provider: str = "scaffold"
    agent_model: str = "llama3.2"
    agent_persona_template: str = "default"
    agent_base_url: str = ""
    agent_api_key: str = ""
    agent_max_steps: int = 4
    agent_max_tokens: int = 4096
    agent_compaction_trigger_ratio: float = 0.8
    agent_compaction_keep_last_messages: int = 8
    agent_stream_chunk_size: int = 48
    agent_llm_timeout: float = 120.0
    agent_stream_queue_max_size: int = 256
    agent_background_memory_enabled: bool = True
    core_passphrase: str = ""
    core_require_encryption: bool = False
    agent_extraction_model: str = ""
    agent_extraction_provider: str = ""
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
    sidecar_nonce: str = ""

    model_config = SettingsConfigDict(
        env_prefix="ANIMA_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )


settings = Settings()
