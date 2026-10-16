from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    ipdesk_mode: str = "sample"  # sample | kipris
    kipris_api_key: str = ""
    kipris_base_url: str = "https://plus.kipris.or.kr/kipo-api/kipi"
    kipris_timeout_sec: float = 10
    kipris_rate_per_sec: float = 5
    kipris_monthly_limit: int = 1000
    cache_ttl_hours: int = 24
    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    ipdesk_db_path: str = ""  # 테스트용 재정의. 비우면 data/ipdesk.db
    ipdesk_export_dir: str = ""  # 테스트용 재정의. 비우면 exports/

    @property
    def data_dir(self) -> Path:
        return PROJECT_ROOT / "data"

    @property
    def db_path(self) -> Path:
        return Path(self.ipdesk_db_path) if self.ipdesk_db_path else self.data_dir / "ipdesk.db"

    @property
    def log_dir(self) -> Path:
        return PROJECT_ROOT / "logs"

    @property
    def export_dir(self) -> Path:
        return Path(self.ipdesk_export_dir) if self.ipdesk_export_dir else PROJECT_ROOT / "exports"


class ConfigError(RuntimeError):
    pass


def load_settings() -> Settings:
    s = Settings()
    if s.ipdesk_mode not in ("sample", "kipris"):
        raise ConfigError(f"IPDESK_MODE는 sample 또는 kipris여야 합니다 (현재: {s.ipdesk_mode})")
    if s.ipdesk_mode == "kipris" and not s.kipris_api_key.strip():
        raise ConfigError(
            "키가 설정되지 않았습니다. .env에 KIPRIS_API_KEY를 입력하거나, "
            "샘플 모드로 실행하려면 IPDESK_MODE=sample 로 바꾸세요."
        )
    return s
