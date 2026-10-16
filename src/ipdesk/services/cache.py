import json
from datetime import date, timedelta

from ipdesk.domain.models import PatentRecord, RightType, SearchQuery, SearchResult, SourceKind
from ipdesk.repo.repositories import Repository
from ipdesk.services.clock import now_kst


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _iso(v: date | None) -> str | None:
    return v.isoformat() if v else None


def result_to_json(result: SearchResult) -> str:
    return json.dumps(
        {
            "total_count": result.total_count,
            "result_code": result.result_code,
            "records": [
                {
                    "application_number": r.application_number,
                    "right_type": r.right_type.value,
                    "title": r.title,
                    "applicants": r.applicants,
                    "application_date": _iso(r.application_date),
                    "status": r.status,
                    "ipc_codes": r.ipc_codes,
                    "abstract": r.abstract,
                    "open_number": r.open_number,
                    "open_date": _iso(r.open_date),
                    "register_number": r.register_number,
                    "register_date": _iso(r.register_date),
                    "drawing_url": r.drawing_url,
                }
                for r in result.records
            ],
        },
        ensure_ascii=False,
    )


def result_from_json(payload: str) -> SearchResult:
    d = json.loads(payload)
    records = [
        PatentRecord(
            application_number=r["application_number"],
            right_type=RightType(r["right_type"]),
            title=r["title"],
            applicants=r["applicants"],
            application_date=_d(r["application_date"]),
            status=r["status"],
            ipc_codes=r["ipc_codes"],
            abstract=r["abstract"],
            open_number=r["open_number"],
            open_date=_d(r["open_date"]),
            register_number=r["register_number"],
            register_date=_d(r["register_date"]),
            drawing_url=r["drawing_url"],
        )
        for r in d["records"]
    ]
    return SearchResult(records=records, total_count=d["total_count"], result_code=d["result_code"])


class ResponseCache:
    """조건 해시(쪽 번호 포함)별 응답 캐시. 성공 결과만 저장한다."""

    def __init__(self, repo: Repository, ttl_hours: float, kind: SourceKind):
        self.repo = repo
        self.ttl = timedelta(hours=ttl_hours)
        self.kind = kind

    def _key(self, query: SearchQuery) -> str:
        return f"{self.kind.value}:{query.hash(include_paging=True)}"

    def get(self, query: SearchQuery) -> SearchResult | None:
        row = self.repo.get_cache(self._key(query))
        if row is None:
            return None
        payload, created_at = row
        if now_kst() - created_at > self.ttl:
            return None
        return result_from_json(payload)

    def put(self, query: SearchQuery, result: SearchResult) -> None:
        self.repo.put_cache(self._key(query), result_to_json(result), now_kst())
