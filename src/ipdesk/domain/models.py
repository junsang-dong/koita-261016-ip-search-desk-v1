import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class RightType(StrEnum):
    PATENT = "patent"  # 출원번호 접두 10
    UTILITY = "utility"  # 출원번호 접두 20

    @property
    def label(self) -> str:
        return "특허" if self is RightType.PATENT else "실용신안"


class SourceKind(StrEnum):
    KIPRIS = "kipris"
    SAMPLE = "sample"

    @property
    def label(self) -> str:
        return "KIPRIS Plus" if self is SourceKind.KIPRIS else "샘플"


PAGE_SIZES = (10, 20, 50)


def normalize_application_number(raw: str) -> tuple[str, bool]:
    """숫자 이외 문자를 제거한다. 13자리면 (정규화값, True), 아니면 (원문, False)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 13:
        return digits, True
    return (raw or "").strip(), False


def format_application_number(number: str) -> str:
    """1020240001234 -> 10-2024-0001234. 13자리가 아니면 원문 그대로."""
    if re.fullmatch(r"\d{13}", number):
        return f"{number[:2]}-{number[2:6]}-{number[6:]}"
    return number


def right_type_of(number: str) -> RightType:
    return RightType.UTILITY if number.startswith("20") else RightType.PATENT


@dataclass(frozen=True)
class PatentRecord:
    application_number: str  # 정규화: 숫자 13자리 (예: 1020240001234)
    right_type: RightType
    title: str
    applicants: list[str]
    application_date: date | None
    status: str | None  # 공개 / 등록 / 소멸 / 거절 등 (원문 그대로)
    ipc_codes: list[str]
    abstract: str | None
    open_number: str | None = None
    open_date: date | None = None
    register_number: str | None = None
    register_date: date | None = None
    drawing_url: str | None = None

    @property
    def display_number(self) -> str:
        return format_application_number(self.application_number)


@dataclass(frozen=True)
class SearchQuery:
    keyword: str | None = None
    title: str | None = None
    applicant: str | None = None
    ipc: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_patent: bool = True
    include_utility: bool = True
    page: int = 1
    page_size: int = 20

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("date_from", "date_to"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SearchQuery":
        def _d(v: Any) -> date | None:
            return date.fromisoformat(v) if v else None

        return cls(
            keyword=(d.get("keyword") or None),
            title=(d.get("title") or None),
            applicant=(d.get("applicant") or None),
            ipc=(d.get("ipc") or None),
            date_from=_d(d.get("date_from")),
            date_to=_d(d.get("date_to")),
            include_patent=bool(d.get("include_patent", True)),
            include_utility=bool(d.get("include_utility", True)),
            page=max(1, int(d.get("page", 1))),
            page_size=int(d.get("page_size", 20))
            if int(d.get("page_size", 20)) in PAGE_SIZES
            else 20,
        )

    def is_empty(self) -> bool:
        return not any(
            [self.keyword, self.title, self.applicant, self.ipc, self.date_from, self.date_to]
        )

    def hash(self, include_paging: bool = False) -> str:
        """조건 해시 (SHA-256). 기본은 쪽 번호·쪽당 건수를 제외한 '검색 조건' 해시."""
        d = self.to_dict()
        for k in ("keyword", "title", "applicant", "ipc"):
            if d[k]:
                d[k] = " ".join(d[k].split())
        if not include_paging:
            d.pop("page")
            d.pop("page_size")
        raw = json.dumps(d, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()

    def describe(self) -> str:
        """사람이 읽을 수 있는 조회 조건 문자열 (CSV·이력 표시용)."""
        parts = []
        if self.keyword:
            parts.append(f"word={self.keyword}")
        if self.title:
            parts.append(f"title={self.title}")
        if self.applicant:
            parts.append(f"applicant={self.applicant}")
        if self.ipc:
            parts.append(f"ipc={self.ipc}")
        if self.date_from or self.date_to:
            f = self.date_from.isoformat() if self.date_from else ""
            t = self.date_to.isoformat() if self.date_to else ""
            parts.append(f"applicationDate={f}~{t}")
        parts.append(f"patent={'Y' if self.include_patent else 'N'}")
        parts.append(f"utility={'Y' if self.include_utility else 'N'}")
        return "; ".join(parts)


@dataclass
class SearchResult:
    """어댑터가 돌려주는 한 쪽 분량의 결과."""

    records: list[PatentRecord]
    total_count: int
    result_code: str | None = None


@dataclass
class SearchRun:  # 출처 기록 (provenance)
    id: int | None
    query: SearchQuery
    source: SourceKind
    executed_at: datetime
    total_count: int | None
    result_code: str | None  # KIPRIS 원본 코드 보존
    status: str  # ok / empty / auth_error / quota_error / network_error / ...
    from_cache: bool = False
    application_numbers: list[str] = field(default_factory=list)


@dataclass
class Favorite:
    application_number: str
    note: str
    added_at: datetime
    added_from_run: int | None = None


@dataclass
class ExportItem:
    """CSV 한 줄: 문헌 + 메모 + 어느 조회에서 나왔는지(출처 기록)."""

    record: PatentRecord
    note: str
    source: SourceKind
    queried_at: str  # ISO 8601 (+09:00)
    query_text: str


def dedup_records(*groups: Iterable[PatentRecord]) -> list[PatentRecord]:
    """정규화된 출원번호 기준 병합. 같은 번호는 나중에 조회한 값으로 덮어쓰고 첫 위치를 유지한다."""
    merged: dict[str, PatentRecord] = {}
    for group in groups:
        for r in group:
            merged[r.application_number] = r
    return list(merged.values())
