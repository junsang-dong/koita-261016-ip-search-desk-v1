from dataclasses import dataclass

from ipdesk.config import Settings
from ipdesk.domain.models import SourceKind
from ipdesk.ports.patent_source import PatentSource
from ipdesk.repo.db import Database
from ipdesk.repo.repositories import Repository
from ipdesk.services.cache import ResponseCache
from ipdesk.services.export import ExportService
from ipdesk.services.favorites import FavoriteService
from ipdesk.services.search import SearchService
from ipdesk.services.usage import UsageGuard


@dataclass
class AppContext:
    settings: Settings
    repo: Repository
    source: PatentSource
    search: SearchService
    favorites: FavoriteService
    usage: UsageGuard
    export: ExportService


def build_usage_guard(settings: Settings, repo: Repository) -> UsageGuard:
    return UsageGuard(repo, settings.kipris_monthly_limit, settings.kipris_rate_per_sec)


def build_source(settings: Settings, guard: UsageGuard) -> PatentSource:
    if settings.ipdesk_mode == SourceKind.SAMPLE:
        from ipdesk.adapters.sample import SampleAdapter

        return SampleAdapter(settings.data_dir / "sample" / "patents_a200.json")
    from ipdesk.adapters.kipris import KiprisAdapter

    return KiprisAdapter(
        api_key=settings.kipris_api_key,
        base_url=settings.kipris_base_url,
        timeout_sec=settings.kipris_timeout_sec,
        raw_dir=settings.log_dir / "raw" if settings.debug else None,
        before_request=guard.before_request,
    )


def build_context(settings: Settings) -> AppContext:
    repo = Repository(Database(settings.db_path))
    guard = build_usage_guard(settings, repo)
    source = build_source(settings, guard)
    # 캐시는 호출 한도가 있는 라이브 소스에만 쓴다. 샘플은 항상 새로 읽는다.
    cache = (
        ResponseCache(repo, settings.cache_ttl_hours, source.kind)
        if source.kind == SourceKind.KIPRIS
        else None
    )
    return AppContext(
        settings=settings,
        repo=repo,
        source=source,
        search=SearchService(source, repo, cache),
        favorites=FavoriteService(repo),
        usage=guard,
        export=ExportService(repo, settings.export_dir),
    )
