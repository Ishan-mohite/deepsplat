import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # detection: "roboflow_sam3" or "fake" (offline development / tests)
    detector: str = "roboflow_sam3"
    roboflow_api_key: str = ""
    # visual positioning: "sift" (free, CPU), "hloc" (needs torch, GPU recommended) or "none"
    vps: str = "sift"

    data_dir: Path = Path("data")
    database_url: str = "sqlite:///data/deepsplat.db"
    # "local" (files under data_dir, served by /files) or "gcs" (bucket + signed URLs)
    storage: str = "local"
    gcs_bucket: str = ""
    public_base_url: str = "http://localhost:8000"
    # static bearer token for the app / clients; empty = no auth (local development only)
    api_token: str = ""

    # quality gates
    reject_low_quality_images: bool = False   # False: flag only
    min_pose_confidence: float = 0.5          # below: no issue is logged, capture needs review


settings = Settings()

# the inspection package reads its detector config from the environment
os.environ.setdefault("DETECTOR", settings.detector)
os.environ.setdefault("ROBOFLOW_API_KEY", settings.roboflow_api_key)
