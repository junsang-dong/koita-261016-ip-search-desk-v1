import pytest

from ipdesk.domain.models import (
    PatentRecord,
    RightType,
    dedup_records,
    format_application_number,
    normalize_application_number,
)
from ipdesk.services import search as search_module


@pytest.mark.parametrize(
    "raw", ["10-2024-0001234", "1020240001234", "10 2024 0001234", " 10-2024-0001234 "]
)
def test_normalization_variants_collapse_to_same_key(raw):
    assert normalize_application_number(raw) == ("1020240001234", True)


def test_non_13_digit_keeps_original_and_flags():
    assert normalize_application_number("10-2024-123") == ("10-2024-123", False)
    assert normalize_application_number("") == ("", False)


def test_display_format_roundtrip():
    assert format_application_number("1020240001234") == "10-2024-0001234"
    assert format_application_number("abc") == "abc"


def _rec(number: str, title: str) -> PatentRecord:
    return PatentRecord(number, RightType.PATENT, title, [], None, None, [], None)


def test_dedup_keeps_first_position_and_latest_value():
    old = [_rec("1020240000001", "옛값"), _rec("1020240000002", "B")]
    new = [_rec("1020240000003", "C"), _rec("1020240000001", "새값")]
    merged = dedup_records(old, new)
    assert [(r.application_number[-1], r.title) for r in merged] == [
        ("1", "새값"),
        ("2", "B"),
        ("3", "C"),
    ]


def test_two_searches_merge_into_one_row_per_number(client):
    a = client.post("/api/search", json={"keyword": "감속기", "page_size": 50}).json()
    b = client.post("/api/search", json={"keyword": "액추에이터", "page_size": 50}).json()
    nums_a = {r["application_number"] for r in a["records"]}
    nums_b = {r["application_number"] for r in b["records"]}
    assert nums_a & nums_b, "테스트 전제: 두 검색에 겹치는 문헌이 있어야 한다"

    repo = client.app.state.ctx.repo
    with repo.db.connect() as conn:
        patents = conn.execute("SELECT COUNT(*) FROM patents").fetchone()[0]
        per_run = {
            r["run_id"]: r["n"]
            for r in conn.execute("SELECT run_id, COUNT(*) AS n FROM run_records GROUP BY run_id")
        }
    assert patents == len(nums_a | nums_b)  # 문헌 한 줄씩
    assert per_run == {a["run"]["id"]: len(nums_a), b["run"]["id"]: len(nums_b)}  # 출처는 모두 남김


def test_compare_counts_are_consistent(client):
    body = client.post(
        "/api/compare", json={"a": {"keyword": "감속기"}, "b": {"keyword": "액추에이터"}}
    ).json()
    assert body["status"] == "ok" and body["truncated"] is False
    assert body["both"] + body["only_a"] == body["fetched_a"] == body["count_a"]
    assert body["both"] + body["only_b"] == body["fetched_b"] == body["count_b"]
    both = {r["application_number"] for r in body["records"]["both"]}
    only_a = {r["application_number"] for r in body["records"]["only_a"]}
    assert not both & only_a
    assert len(client.get("/api/runs").json()["runs"]) == 2  # A, B 각각 이력에 남는다


def test_compare_marks_truncation(client, monkeypatch):
    monkeypatch.setattr(search_module, "COMPARE_PAGE_SIZE", 10)
    monkeypatch.setattr(search_module, "COMPARE_MAX_PAGES", 1)
    body = client.post(
        "/api/compare", json={"a": {"keyword": ""}, "b": {"keyword": "감속기"}}
    ).json()
    assert body["truncated"] is True and body["fetched_a"] == 10 and body["count_a"] == 25


def test_compare_ui_renders_table(client):
    html = client.post(
        "/ui/compare",
        data={"keyword_a": "감속기", "keyword_b": "액추에이터", "include_patent": "1"},
    ).text
    assert "A∩B" in html and "조건 A" in html
    assert "모두 입력" in client.post("/ui/compare", data={"keyword_a": "x"}).text
