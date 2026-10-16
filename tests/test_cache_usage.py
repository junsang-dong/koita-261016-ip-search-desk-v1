import httpx
import pytest
from fastapi.testclient import TestClient

from ipdesk.config import Settings
from ipdesk.domain.models import SearchQuery
from ipdesk.main import create_app
from ipdesk.services import cache as cache_module
from ipdesk.services.clock import now_kst
from ipdesk.services.usage import TokenBucket
from tests.test_kipris_adapter import BASE, KEY, xml


def make_client(tmp_path, **overrides) -> TestClient:
    s = Settings(
        _env_file=None,
        ipdesk_mode="kipris",
        kipris_api_key=KEY,
        ipdesk_db_path=str(tmp_path / "t.db"),
        ipdesk_export_dir=str(tmp_path / "exports"),
        kipris_rate_per_sec=1000,  # 테스트에서는 속도 제한이 방해되지 않게
        **overrides,
    )
    return TestClient(create_app(s))


@pytest.fixture
def live(tmp_path) -> TestClient:
    return make_client(tmp_path)


@pytest.fixture
def kipris(respx_mock):
    return respx_mock.get(url__startswith=BASE)


def search(c, keyword="감속", **kw):
    return c.post("/api/search", json={"keyword": keyword, **kw})


def usage(c) -> int:
    return c.get("/api/health").json()["monthly_usage"]


# ---- 캐시 --------------------------------------------------------------------
def test_same_query_uses_cache_and_skips_http(live, kipris):
    kipris.mock(return_value=xml("kipris_ok.xml"))
    first = search(live).json()
    second = search(live).json()
    assert kipris.call_count == 1 and usage(live) == 1
    assert first["run"]["from_cache"] is False and second["run"]["from_cache"] is True
    assert second["records"] == first["records"]
    ui = live.post(
        "/ui/search", data={"keyword": "감속", "include_patent": "1", "include_utility": "1"}
    )
    assert "캐시 사용" in ui.text and kipris.call_count == 1


def test_refresh_bypasses_cache(live, kipris):
    kipris.mock(return_value=xml("kipris_ok.xml"))
    search(live)
    again = search(live, refresh=True).json()
    assert kipris.call_count == 2 and again["run"]["from_cache"] is False


def test_different_page_or_condition_is_a_different_cache_entry(live, kipris):
    kipris.mock(return_value=xml("kipris_ok.xml"))
    search(live)
    search(live, page=2)
    search(live, keyword="다른 조건")
    assert kipris.call_count == 3


def test_errors_are_not_cached(live, kipris):
    kipris.mock(side_effect=[xml("kipris_auth_error.xml"), xml("kipris_ok.xml")])
    assert search(live).status_code == 401
    ok = search(live)
    assert ok.status_code == 200 and ok.json()["run"]["from_cache"] is False
    assert kipris.call_count == 2


def test_cache_expires_after_ttl(live, kipris, monkeypatch):
    kipris.mock(return_value=xml("kipris_ok.xml"))
    search(live)
    monkeypatch.setattr(cache_module, "now_kst", lambda: now_kst().replace(year=now_kst().year + 1))
    search(live)  # 24시간 TTL을 넘겼으므로 다시 호출
    assert kipris.call_count == 2


def test_rerun_uses_cache_unless_refresh(live, kipris):
    kipris.mock(return_value=xml("kipris_ok.xml"))
    run_id = search(live).json()["run"]["id"]
    assert live.post(f"/api/runs/{run_id}/rerun").json()["run"]["from_cache"] is True
    assert kipris.call_count == 1
    live.post(f"/api/runs/{run_id}/rerun?refresh=true")
    assert kipris.call_count == 2


def test_sample_mode_never_caches(client):
    assert client.post("/api/search", json={"keyword": "감속"}).json()["run"]["from_cache"] is False
    assert client.post("/api/search", json={"keyword": "감속"}).json()["run"]["from_cache"] is False


# ---- 월 사용량 ----------------------------------------------------------------
def test_retries_count_as_calls(live, kipris):
    kipris.mock(side_effect=[httpx.Response(500), httpx.Response(500), xml("kipris_ok.xml")])

    # 재시도 대기(0.5s+1s)를 건너뛰려면 어댑터 sleep을 바꿔야 하므로 직접 주입한다.
    async def nosleep(_):
        return None

    live.app.state.ctx.source._sleep = nosleep
    assert search(live).status_code == 200
    assert usage(live) == 3


def test_warning_at_80_percent_and_block_at_100(tmp_path, kipris):
    c = make_client(tmp_path, kipris_monthly_limit=5)
    kipris.mock(return_value=xml("kipris_ok.xml"))
    for i in range(3):
        search(c, keyword=f"조건{i}")
    assert c.get("/api/health").json()["usage_level"] == "ok"
    search(c, keyword="조건3")  # 4/5 = 80%
    assert c.get("/api/health").json()["usage_level"] == "warn"
    assert "80%" in c.get("/").text

    search(c, keyword="조건4")  # 5/5
    calls = kipris.call_count
    blocked = search(c, keyword="조건5")
    assert blocked.status_code == 429
    assert (
        blocked.json()["status"] == "quota_error" and blocked.json()["result_code"] == "local_limit"
    )
    assert kipris.call_count == calls  # 한도에 도달하면 HTTP 호출 자체를 하지 않는다
    assert usage(c) == 5 and c.get("/api/health").json()["usage_level"] == "blocked"
    assert "차단" in c.get("/").text
    # 캐시 적중은 한도와 무관하게 계속 동작한다
    assert search(c, keyword="조건0").status_code == 200


# ---- 속도 제한 ----------------------------------------------------------------
def test_token_bucket_throttles_to_rate():
    import asyncio

    now = [0.0]
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    bucket = TokenBucket(5, clock=lambda: now[0], sleep=fake_sleep)

    async def go():
        for _ in range(5):
            await bucket.acquire()
        assert slept == []  # 처음 5건은 즉시
        await bucket.acquire()
        await bucket.acquire()

    asyncio.run(go())
    assert slept == pytest.approx([0.2, 0.2])  # 이후는 초당 5건 = 0.2초 간격
    assert SearchQuery().is_empty()
