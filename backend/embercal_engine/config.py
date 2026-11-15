from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_prefix="EMBERCAL_", extra="ignore")

    data_dir: Path = ROOT / "data" / "processed"
    manifest_path: Path = ROOT / "data" / "manifest.json"
    baseline_start: int = 2003
    baseline_end: int = 2022
    cors_origins: list[str] = ["*"]
    max_map_points: int = 5000


settings = Settings()
