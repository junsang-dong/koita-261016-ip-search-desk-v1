import json
import re
from datetime import date
from pathlib import Path

from ipdesk.domain.models import (
    PatentRecord,
    SearchQuery,
    SearchResult,
    SourceKind,
    normalize_application_number,
    right_type_of,
)


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _to_record(raw: dict) -> PatentRecord:
    number, _ = normalize_application_number(raw["application_number"])
    return PatentRecord(
        application_number=number,
        right_type=right_type_of(number),
        title=raw["title"],
        applicants=list(raw.get("applicants") or []),
        application_date=_d(raw.get("application_date")),
        status=raw.get("status"),
        ipc_codes=list(raw.get("ipc_codes") or []),
        abstract=raw.get("abstract"),
        open_number=raw.get("open_number"),
        open_date=_d(raw.get("open_date")),
        register_number=raw.get("register_number"),
        register_date=_d(raw.get("register_date")),
        drawing_url=raw.get("drawing_url"),
    )


def _keyword_matches(keyword: str, haystack: str) -> bool:
    """`A AND B`, `A OR B`, 공백 구분(=AND)을 지원하는 단순 매처. OR이 AND보다 낮은 우선순위."""
    hay = haystack.lower()
    for group in re.split(r"\s+OR\s+", keyword.strip()):
        terms = [t for t in re.split(r"\s+(?:AND\s+)?", group.strip()) if t and t != "AND"]
        if terms and all(t.strip("()").lower() in hay for t in terms):
            return True
    return False


class SampleAdapter:
    kind = SourceKind.SAMPLE

    def __init__(self, path: Path):
        self._records = [_to_record(r) for r in json.loads(path.read_text(encoding="utf-8"))]
        self._by_number = {r.application_number: r for r in self._records}

    def _matches(self, q: SearchQuery, r: PatentRecord) -> bool:
        if r.right_type.value == "patent" and not q.include_patent:
            return False
        if r.right_type.value == "utility" and not q.include_utility:
            return False
        if q.keyword:
            hay = " ".join([r.title, r.abstract or "", " ".join(r.applicants)])
            if not _keyword_matches(q.keyword, hay):
                return False
        if q.title and q.title.lower() not in r.title.lower():
            return False
        if q.applicant and not any(q.applicant.lower() in a.lower() for a in r.applicants):
            return False
        if q.ipc:
            want = q.ipc.replace(" ", "").lower()
            if not any(c.replace(" ", "").lower().startswith(want) for c in r.ipc_codes):
                return False
        if q.date_from and (not r.application_date or r.application_date < q.date_from):
            return False
        if q.date_to and (not r.application_date or r.application_date > q.date_to):
            return False
        return True

    async def search(self, query: SearchQuery) -> SearchResult:
        hits = [r for r in self._records if self._matches(query, r)]
        hits.sort(key=lambda r: r.application_date or date.min, reverse=True)
        start = (query.page - 1) * query.page_size
        return SearchResult(
            records=hits[start : start + query.page_size],
            total_count=len(hits),
            result_code="00",
        )

    async def get_detail(self, application_number: str) -> PatentRecord | None:
        number, _ = normalize_application_number(application_number)
        return self._by_number.get(number)
