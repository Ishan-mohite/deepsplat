from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    roboflow_api_key: str = ""
    # "roboflow_sam3" (default) or "fake" (offline development / tests)
    detector: str = "roboflow_sam3"
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/app.db"
    # Base URL the frontend / splat team uses to reach this API (for file URLs in responses)
    public_base_url: str = "http://localhost:8000"
    # Optional: POSTed with the job JSON whenever a reconstruction job is created
    splat_webhook_url: str | None = None


settings = Settings()
