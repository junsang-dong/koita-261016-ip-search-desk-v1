import math
from dataclasses import dataclass, field, replace

from ipdesk.domain.errors import SourceError
from ipdesk.domain.models import (
    PatentRecord,
    SearchQuery,
    SearchResult,
    SearchRun,
    dedup_records,
    normalize_application_number,
)
from ipdesk.ports.patent_source import PatentSource
from ipdesk.repo.repositories import Repository
from ipdesk.services.cache import ResponseCache
from ipdesk.services.clock import now_kst

COMPARE_PAGE_SIZE = 50
COMPARE_MAX_PAGES = 4  # 조건당 최대 200건까지만 가져와 호출 한도를 보호한다


@dataclass
class SearchOutcome:
    query: SearchQuery
    run: SearchRun
    records: list[PatentRecord] = field(default_factory=list)
    favorites: set[str] = field(default_factory=set)
    error: SourceError | None = None
    from_cache: bool = False  # 이번 쪽 결과가 캐시에서 왔는지

    @property
    def total_pages(self) -> int:
        total = self.run.total_count or 0
        return max(1, math.ceil(total / self.query.page_size))


@dataclass
class CompareResult:
    run_a: SearchRun
    run_b: SearchRun
    count_a: int  # 조건별 전체 건수 (KIPRIS totalCount)
    count_b: int
    both: list[PatentRecord]
    only_a: list[PatentRecord]
    only_b: list[PatentRecord]
    fetched_a: int
    fetched_b: int
    truncated: bool  # 상한 때문에 일부만 비교했으면 True (교집합·차집합은 하한값)


class SearchService:
    def __init__(self, source: PatentSource, repo: Repository, cache: ResponseCache | None = None):
        self.source = source
        self.repo = repo
        self.cache = cache

    async def _fetch(self, query: SearchQuery, refresh: bool) -> tuple[SearchResult, bool]:
        """캐시 → 소스 순으로 한 쪽 분량을 가져온다. 오류는 캐시에 저장하지 않는다."""
        if self.cache and not refresh:
            hit = self.cache.get(query)
            if hit is not None:
                return hit, True
        result = await self.source.search(query)
        if self.cache:
            self.cache.put(query, result)
        return result, False

    async def search(self, query: SearchQuery, refresh: bool = False) -> SearchOutcome:
        now = now_kst()
        try:
            result, from_cache = await self._fetch(query, refresh)
        except SourceError as e:
            run = SearchRun(
                id=None,
                query=query,
                source=self.source.kind,
                executed_at=now,
                total_count=None,
                result_code=e.result_code,
                status=e.status,
            )
            run.id = self.repo.create_run(run)
            return SearchOutcome(query=query, run=run, error=e)

        self.repo.upsert_patents(result.records, self.source.kind, now)
        numbers = [r.application_number for r in result.records]

        # 2쪽 이후는 같은 조건의 직전 실행에 이어 붙여 이력이 쪽마다 늘어나지 않게 한다.
        run = self.repo.latest_run_by_hash(query.hash()) if query.page > 1 else None
        if run is None:
            run = SearchRun(
                id=None,
                query=query,
                source=self.source.kind,
                executed_at=now,
                total_count=result.total_count,
                result_code=result.result_code,
                status="ok" if result.total_count > 0 else "empty",
                from_cache=from_cache,
            )
            run.id = self.repo.create_run(run)
        self.repo.add_run_records(run.id, numbers, start_rank=(query.page - 1) * query.page_size)
        run.application_numbers = numbers

        return SearchOutcome(
            query=query,
            run=run,
            records=result.records,
            favorites=self.repo.favorite_numbers(),
            from_cache=from_cache,
        )

    async def rerun(self, run_id: int, refresh: bool = False) -> SearchOutcome | None:
        old = self.repo.get_run(run_id)
        if old is None:
            return None
        return await self.search(replace(old.query, page=1), refresh=refresh)

    async def get_detail(self, application_number: str) -> PatentRecord | None:
        number, _ = normalize_application_number(application_number)
        cached = self.repo.get_patent(number)
        if cached is not None:
            return cached
        record = await self.source.get_detail(number)
        if record is not None:
            self.repo.upsert_patents([record], self.source.kind, now_kst())
        return record

    # ---- A/B 비교 ---------------------------------------------------------
    async def _collect(self, query: SearchQuery, refresh: bool):
        """한 조건의 결과를 최대 COMPARE_MAX_PAGES쪽까지 모은다. 1쪽은 이력에 남는다."""
        first_q = replace(query, page=1, page_size=COMPARE_PAGE_SIZE)
        first = await self.search(first_q, refresh=refresh)
        if first.error:
            raise first.error
        records = list(first.records)
        last_page = min(first.total_pages, COMPARE_MAX_PAGES)
        for page in range(2, last_page + 1):
            q = replace(first_q, page=page)
            result, _ = await self._fetch(q, refresh)
            self.repo.upsert_patents(result.records, self.source.kind, now_kst())
            self.repo.add_run_records(
                first.run.id,
                [r.application_number for r in result.records],
                start_rank=(page - 1) * COMPARE_PAGE_SIZE,
            )
            records.extend(result.records)
        return first, dedup_records(records), first.total_pages > COMPARE_MAX_PAGES

    async def compare(
        self, qa: SearchQuery, qb: SearchQuery, refresh: bool = False
    ) -> CompareResult:
        a, recs_a, trunc_a = await self._collect(qa, refresh)
        b, recs_b, trunc_b = await self._collect(qb, refresh)
        nums_a = {r.application_number for r in recs_a}
        nums_b = {r.application_number for r in recs_b}
        return CompareResult(
            run_a=a.run,
            run_b=b.run,
            count_a=a.run.total_count or 0,
            count_b=b.run.total_count or 0,
            both=[r for r in recs_a if r.application_number in nums_b],
            only_a=[r for r in recs_a if r.application_number not in nums_b],
            only_b=[r for r in recs_b if r.application_number not in nums_a],
            fetched_a=len(recs_a),
            fetched_b=len(recs_b),
            truncated=trunc_a or trunc_b,
        )
