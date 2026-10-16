from ipdesk.domain.errors import AuthError
from ipdesk.domain.models import SourceKind


class FailingSource:
    kind = SourceKind.SAMPLE

    async def search(self, query):
        raise AuthError(result_code="30")

    async def get_detail(self, number):
        return None


def test_source_error_is_not_shown_as_zero_results(client):
    client.app.state.ctx.search.source = FailingSource()

    api = client.post("/api/search", json={"keyword": "감속"})
    assert api.status_code == 401
    assert api.json()["status"] == "auth_error"
    assert api.json()["result_code"] == "30"

    ui = client.post("/ui/search", data={"keyword": "감속", "include_patent": "1"})
    assert ui.headers["HX-Retarget"] == "#banner"
    assert "auth_error" in ui.text and "조건에 맞는 문헌이 없습니다" not in ui.text

    runs = client.get("/api/runs").json()["runs"]
    assert [r["status"] for r in runs] == ["auth_error", "auth_error"]
