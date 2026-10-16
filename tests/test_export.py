import csv
import io
import re

import pytest

from ipdesk.domain.models import ExportItem, PatentRecord, RightType, SourceKind
from ipdesk.services.export import COLUMNS, build_csv, sanitize_cell


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("=SUM(A1:A2)", "'=SUM(A1:A2)"),
        ("+1+1", "'+1+1"),
        ("-2+3", "'-2+3"),
        ("@cmd", "'@cmd"),
        ("정상 텍스트", "정상 텍스트"),
        ("줄1\n줄2\r\n줄3", "줄1 줄2 줄3"),
        ("중간에 = 는 안전", "중간에 = 는 안전"),
        (None, ""),
    ],
)
def test_sanitize_cell(raw, expected):
    assert sanitize_cell(raw) == expected


def _item(title='=HYPERLINK("http://x")', note="") -> ExportItem:
    rec = PatentRecord(
        application_number="1020240001234",
        right_type=RightType.PATENT,
        title=title,
        applicants=["가 주식회사", "나 주식회사"],
        application_date=None,
        status="공개",
        ipc_codes=["F16H 1/32", "B25J 9/10"],
        abstract="첫 줄\n둘째 줄, 쉼표 포함",
    )
    return ExportItem(rec, note, SourceKind.KIPRIS, "2026-10-16T13:05:22+09:00", "word=감속기")


def test_csv_has_bom_columns_and_escaping():
    data = build_csv([_item()])
    assert data.startswith(b"\xef\xbb\xbf")  # 엑셀 한글 깨짐 방지
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0] == COLUMNS
    row = dict(zip(COLUMNS, rows[1], strict=True))
    assert row["출원번호"] == "10-2024-0001234" and row["권리구분"] == "특허"
    assert row["발명의명칭"].startswith("'=")  # 수식 주입 방지
    assert row["출원인"] == "가 주식회사; 나 주식회사"
    assert row["IPC"] == "F16H 1/32; B25J 9/10"
    assert row["초록"] == "첫 줄 둘째 줄, 쉼표 포함"
    assert row["조회조건"] == "word=감속기"
    assert row["조회시각"].endswith("+09:00") and row["출처"] == "KIPRIS Plus"


def _read(resp) -> list[dict]:
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    return [dict(zip(rows[0], r, strict=True)) for r in rows[1:]]


def test_favorites_export_via_api_has_provenance(client, settings):
    client.post("/ui/search", data={"keyword": "감속기", "include_patent": "1"})
    run_id = client.get("/api/runs").json()["runs"][0]["id"]
    nums = [
        r["application_number"]
        for r in client.post("/api/search", json={"keyword": "감속기"}).json()["records"]
    ]
    # 검색 결과에서 담기(run_id 포함)와 상세에서 직접 담기(run 없음)
    client.post(f"/ui/favorites/{nums[0]}/toggle", data={"run_id": run_id})
    client.put(f"/api/favorites/{nums[1]}", json={"note": "=메모\n두 줄"})

    resp = client.get("/api/export.csv?scope=favorites")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert re.fullmatch(
        r'attachment; filename="ipdesk_favorites_\d{8}_\d{4}\.csv"',
        resp.headers["content-disposition"],
    )
    rows = {r["출원번호"].replace("-", ""): r for r in _read(resp)}
    from_run, direct = rows[nums[0]], rows[nums[1]]
    assert from_run["조회조건"].startswith("word=감속기") and from_run["출처"] == "샘플"
    assert from_run["조회시각"].endswith("+09:00")
    assert direct["조회조건"] == "(직접 추가)" and direct["메모"] == "'=메모 두 줄"
    assert len(list((settings.export_dir).glob("ipdesk_favorites_*.csv"))) == 1  # 사본 저장


def test_run_export_has_all_loaded_rows(client):
    body = client.post("/api/search", json={"keyword": "감속기"}).json()
    resp = client.get(f"/api/export.csv?run_id={body['run']['id']}")
    rows = _read(resp)
    assert len(rows) == body["run"]["total_count"]
    assert all(r["출처"] == "샘플" and r["조회조건"].startswith("word=감속기") for r in rows)
    assert "run" in resp.headers["content-disposition"]


def test_export_argument_errors(client):
    assert client.get("/api/export.csv").status_code == 400
    assert client.get("/api/export.csv?run_id=999").status_code == 404
    assert _read(client.get("/api/export.csv?scope=favorites")) == []  # 빈 즐겨찾기도 헤더는 있다
