import asyncio
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ipdesk.adapters.kipris import KeyMaskingFilter, KiprisAdapter, mask_secrets
from ipdesk.config import ConfigError, Settings, load_settings
from ipdesk.domain.errors import (
    ApiError,
    AuthError,
    NetworkError,
    ParseError,
    QuotaError,
    UpstreamError,
)
from ipdesk.domain.models import RightType, SearchQuery
from ipdesk.main import create_app

FIX = Path(__file__).parent / "fixtures"
BASE = "https://plus.kipris.or.kr/kipo-api/kipi"
KEY = "SECRETKEY%2Bvalue%3D%3D"  # 포털에서 발급받은 인코딩된 키 형태


def xml(name: str) -> httpx.Response:
    return httpx.Response(200, content=(FIX / name).read_bytes())


async def _nosleep(_: float) -> None:
    return None


@pytest.fixture
def adapter() -> KiprisAdapter:
    return KiprisAdapter(api_key=KEY, base_url=BASE, sleep=_nosleep)


@pytest.fixture
def word_route(respx_mock):
    return respx_mock.get(url__startswith=f"{BASE}/patUtiModInfoSearchSevice/getWordSearch")


def search(adapter, **kw):
    return asyncio.run(adapter.search(SearchQuery(**{"keyword": "액추에이터", **kw})))


# ---- 정상 / 0건 -------------------------------------------------------------
def test_ok_response_maps_to_domain(adapter, word_route):
    word_route.mock(return_value=xml("kipris_ok.xml"))
    res = search(adapter)
    assert res.total_count == 128 and res.result_code == "00"
    assert [r.application_number for r in res.records] == [
        "1020240001234",
        "1020230001037",
        "2020200000555",
    ]
    a, b, c = res.records
    assert a.display_number == "10-2024-0001234"
    assert a.right_type is RightType.PATENT and c.right_type is RightType.UTILITY
    assert a.ipc_codes == ["F16H 1/32", "B25J 9/10"]
    assert b.applicants == ["한빛모션 주식회사", "대성정밀 주식회사"]
    assert str(b.application_date) == "2023-05-18"  # '2023.05.18' 형식도 해석
    assert str(a.open_date) == "2025-01-03" and a.drawing_url.startswith("https://")
    assert b.register_number == "10-2025-0001937"


def test_zero_results_is_not_an_error(adapter, word_route):
    word_route.mock(return_value=xml("kipris_zero.xml"))
    res = search(adapter)
    assert res.total_count == 0 and res.records == []


# ---- 요청 형태 ----------------------------------------------------------------
def test_service_key_sent_as_issued_not_double_encoded(adapter, word_route):
    word_route.mock(return_value=xml("kipris_zero.xml"))
    search(adapter, page=2, page_size=10)
    raw_url = str(word_route.calls.last.request.url)
    assert f"ServiceKey={KEY}&" in raw_url  # %2B가 %252B로 바뀌면 안 된다
    assert "word=%EC%95%A1%EC%B6%94%EC%97%90%EC%9D%B4%ED%84%B0" in raw_url
    assert "pageNo=2" in raw_url and "numOfRows=10" in raw_url
    assert "patent=true" in raw_url and "utility=true" in raw_url


def test_advanced_search_uses_advanced_operation(adapter, respx_mock):
    route = respx_mock.get(url__startswith=f"{BASE}/patUtiModInfoSearchSevice/getAdvancedSearch")
    route.mock(return_value=xml("kipris_zero.xml"))
    from datetime import date

    search(adapter, applicant="예시", date_from=date(2024, 1, 1), date_to=date(2024, 12, 31))
    url = str(route.calls.last.request.url)
    assert "applicant=%EC%98%88%EC%8B%9C" in url
    assert "applicationDate=20240101~20241231" in url


def test_no_right_type_selected_makes_no_call(adapter, word_route):
    res = search(adapter, include_patent=False, include_utility=False)
    assert res.total_count == 0 and word_route.call_count == 0


# ---- 오류 판정 (명세 9.4 표 순서) ----------------------------------------------
def test_auth_error_in_http_200_is_not_zero_results(adapter, word_route):
    word_route.mock(return_value=xml("kipris_auth_error.xml"))
    with pytest.raises(AuthError) as ei:
        search(adapter)
    assert ei.value.status == "auth_error" and ei.value.result_code == "30"
    assert word_route.call_count == 1  # 인증 오류는 재시도하지 않는다


def test_broken_xml_is_parse_error_without_retry(adapter, word_route):
    word_route.mock(return_value=xml("kipris_broken.xml"))
    with pytest.raises(ParseError):
        search(adapter)
    assert word_route.call_count == 1


def test_xxe_payload_is_rejected(adapter, word_route):
    word_route.mock(return_value=xml("kipris_xxe.xml"))
    with pytest.raises(ParseError):
        search(adapter)


def test_unknown_result_code_is_api_error_with_code_preserved(adapter, word_route):
    body = (FIX / "kipris_zero.xml").read_text().replace("<resultCode>00", "<resultCode>99")
    word_route.mock(return_value=httpx.Response(200, text=body))
    with pytest.raises(ApiError) as ei:
        search(adapter)
    assert ei.value.result_code == "99" and ei.value.status == "api_error"


def test_quota_code_maps_to_quota_error(adapter, word_route):
    body = (FIX / "kipris_zero.xml").read_text().replace("<resultCode>00", "<resultCode>22")
    word_route.mock(return_value=httpx.Response(200, text=body))
    with pytest.raises(QuotaError):
        search(adapter)


def test_5xx_retries_twice_then_succeeds(adapter, word_route):
    word_route.mock(side_effect=[httpx.Response(503), httpx.Response(500), xml("kipris_ok.xml")])
    assert search(adapter).total_count == 128
    assert word_route.call_count == 3


def test_5xx_gives_up_after_two_retries(adapter, word_route):
    word_route.mock(return_value=httpx.Response(502))
    with pytest.raises(UpstreamError):
        search(adapter)
    assert word_route.call_count == 3


def test_timeout_is_network_error_after_retries(adapter, word_route):
    word_route.mock(side_effect=httpx.ConnectTimeout("t"))
    with pytest.raises(NetworkError):
        search(adapter)
    assert word_route.call_count == 3


def test_backoff_is_exponential(respx_mock):
    waits: list[float] = []

    async def rec(s: float) -> None:
        waits.append(s)

    ad = KiprisAdapter(api_key=KEY, base_url=BASE, sleep=rec)
    respx_mock.get(url__startswith=BASE).mock(return_value=httpx.Response(500))
    with pytest.raises(UpstreamError):
        search(ad)
    assert waits == [0.5, 1.0]


# ---- 상세 -----------------------------------------------------------------------
def test_detail_flattens_nested_response(adapter, respx_mock):
    route = respx_mock.get(url__startswith=f"{BASE}/patUtiModInfoSearchSevice/getBibliography")
    route.mock(return_value=xml("kipris_detail.xml"))
    rec = asyncio.run(adapter.get_detail("10-2024-0001234"))
    assert "applicationNumber=1020240001234" in str(route.calls.last.request.url)
    assert rec.abstract == "초록 전문입니다." and rec.applicants == ["주식회사 예시로보틱스"]
    assert rec.open_number == "10-2025-0001734"


# ---- 키 마스킹 ---------------------------------------------------------------------
def test_mask_secrets():
    assert mask_secrets(f"GET https://x/y?ServiceKey={KEY}&word=a") == (
        "GET https://x/y?ServiceKey=***&word=a"
    )


def test_key_never_appears_in_logs(adapter, word_route, caplog):
    caplog.handler.addFilter(KeyMaskingFilter())
    caplog.set_level(logging.DEBUG)
    word_route.mock(side_effect=[httpx.Response(500), xml("kipris_broken.xml")])
    with pytest.raises(ParseError):
        search(adapter)
    assert caplog.records
    assert "SECRETKEY" not in caplog.text and "ServiceKey=***" in caplog.text


# ---- 앱 통합: 화면·API에서 0건과 실패가 다르게 보이는가 -----------------------------
@pytest.fixture
def live_client(tmp_path) -> TestClient:
    s = Settings(
        _env_file=None,
        ipdesk_mode="kipris",
        kipris_api_key=KEY,
        ipdesk_db_path=str(tmp_path / "t.db"),
    )
    return TestClient(create_app(s))


def test_app_auth_error_banner_vs_zero_results(live_client, respx_mock):
    route = respx_mock.get(url__startswith=BASE)
    route.mock(return_value=xml("kipris_auth_error.xml"))
    bad = live_client.post("/ui/search", data={"keyword": "감속", "include_patent": "1"})
    assert bad.headers["HX-Retarget"] == "#banner"
    assert "auth_error" in bad.text and "조건에 맞는 문헌이 없습니다" not in bad.text
    assert live_client.post("/api/search", json={"keyword": "감속"}).status_code == 401

    route.mock(return_value=xml("kipris_zero.xml"))
    empty = live_client.post("/ui/search", data={"keyword": "감속", "include_patent": "1"})
    assert "조건에 맞는 문헌이 없습니다" in empty.text and "HX-Retarget" not in empty.headers
    assert live_client.post("/api/search", json={"keyword": "감속"}).json()["status"] == "empty"


def test_app_live_search_records_provenance(live_client, respx_mock):
    respx_mock.get(url__startswith=BASE).mock(return_value=xml("kipris_ok.xml"))
    body = live_client.post("/api/search", json={"keyword": "액추에이터"}).json()
    assert body["run"]["source"] == "kipris" and body["run"]["total_count"] == 128
    assert body["run"]["result_code"] == "00"
    assert body["pagination"]["total_pages"] == 7
    assert live_client.get("/api/health").json()["key_configured"] is True


def test_kipris_mode_without_key_refuses_to_start(monkeypatch):
    monkeypatch.setenv("IPDESK_MODE", "kipris")
    monkeypatch.setenv("KIPRIS_API_KEY", "")
    with pytest.raises(ConfigError, match="IPDESK_MODE=sample"):
        load_settings()
