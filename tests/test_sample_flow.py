from fastapi.testclient import TestClient

from ipdesk.main import create_app

A200 = "1020240001234"


def test_search_keyword_and_provenance(client):
    r = client.post("/api/search", json={"keyword": "액추에이터 AND 감속"})
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "ok"
    assert body["run"]["source"] == "sample"
    assert body["run"]["total_count"] >= 5
    assert body["run"]["executed_at"].endswith("+09:00")
    assert all(len(x["application_number"]) == 13 for x in body["records"])


def test_zero_results_is_empty_not_error(client):
    r = client.post("/api/search", json={"keyword": "존재하지않는단어xyz"})
    assert r.status_code == 200
    assert r.json()["status"] == "empty"
    assert r.json()["run"]["total_count"] == 0


def test_right_type_filter(client):
    r = client.post("/api/search", json={"keyword": "", "include_patent": False})
    assert {x["right_type"] for x in r.json()["records"]} == {"utility"}


def test_pagination_reuses_run(client):
    p1 = client.post("/api/search", json={"keyword": "", "page_size": 10}).json()
    p2 = client.post("/api/search", json={"keyword": "", "page": 2, "page_size": 10}).json()
    assert p1["pagination"]["total_pages"] == 3
    assert p1["run"]["id"] == p2["run"]["id"]
    assert len(client.get("/api/runs").json()["runs"]) == 1


def test_detail_accepts_hyphenated_number(client):
    r = client.get("/api/patents/10-2024-0001234")
    assert r.status_code == 200
    assert r.json()["display_number"] == "10-2024-0001234"
    assert client.get("/api/patents/1020990000000").status_code == 404


def test_favorites_persist_across_restart(settings):
    c1 = TestClient(create_app(settings))
    c1.post("/api/search", json={"keyword": "감속"})
    assert c1.put(f"/api/favorites/{A200}", json={"note": "핵심 선행문헌"}).status_code == 200

    c2 = TestClient(create_app(settings))  # 앱 재시작
    favs = c2.get("/api/favorites").json()["favorites"]
    assert [f["application_number"] for f in favs] == [A200]
    assert favs[0]["note"] == "핵심 선행문헌"
    assert c2.delete(f"/api/favorites/{A200}").status_code == 200
    assert c2.get("/api/favorites").json()["favorites"] == []


def test_index_and_ui_search_render(client):
    assert "BCR IP Search Desk" in client.get("/").text
    html = client.post("/ui/search", data={"keyword": "감속", "include_patent": "1"}).text
    assert "출처: 샘플" in html and "결과" in html
    empty = client.post("/ui/search", data={"keyword": "zzzz", "include_patent": "1"}).text
    assert "조건에 맞는 문헌이 없습니다" in empty
