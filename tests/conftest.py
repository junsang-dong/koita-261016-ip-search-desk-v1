import pytest
from fastapi.testclient import TestClient

from ipdesk.config import Settings
from ipdesk.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        ipdesk_mode="sample",
        ipdesk_db_path=str(tmp_path / "t.db"),
        ipdesk_export_dir=str(tmp_path / "exports"),
    )


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))
