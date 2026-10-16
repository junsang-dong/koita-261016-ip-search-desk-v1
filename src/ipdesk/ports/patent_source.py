from typing import Protocol

from ipdesk.domain.models import PatentRecord, SearchQuery, SearchResult, SourceKind


class PatentSource(Protocol):
    """검색 데이터 소스. 실패하면 domain.errors.SourceError 계열을 던진다."""

    kind: SourceKind

    async def search(self, query: SearchQuery) -> SearchResult: ...

    async def get_detail(self, application_number: str) -> PatentRecord | None: ...
