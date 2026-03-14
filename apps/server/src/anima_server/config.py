from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5433/anima"
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[4] / ".anima" / "dev"


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
    agent_base_url: str = ""
    agent_api_key: str = ""
    agent_max_tokens: int = 4096
    agent_stream_chunk_size: int = 48

    model_config = SettingsConfigDict(
        env_prefix="ANIMA_",
        env_file=".env",
        extra="ignore",
    )


settings = Settings()
