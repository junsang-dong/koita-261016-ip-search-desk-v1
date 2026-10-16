"""htmx 화면용 HTML 조각 라우터. JSON API(routes.py)와 같은 서비스 계층을 사용한다."""

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from ipdesk.context import AppContext
from ipdesk.domain.errors import SourceError
from ipdesk.domain.models import PAGE_SIZES, SearchQuery
from ipdesk.services.search import SearchOutcome

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "web" / "templates")

router = APIRouter()

ERROR_HINTS = {
    "auth_error": "설정의 API 키 또는 KIPRIS Plus 서비스 활용신청 상태를 확인하세요.",
    "quota_error": "IPDESK_MODE=sample 로 바꿔 샘플 모드로 계속할 수 있습니다.",
    "network_error": "인터넷 연결을 확인한 뒤 다시 시도하세요.",
    "upstream_error": "잠시 후 다시 시도하세요.",
    "parse_error": "응답 형식이 달라졌을 수 있습니다. 로그를 확인하세요.",
    "api_error": "표시된 코드와 메시지를 KIPRIS Plus 문서에서 확인하세요.",
}
RETRYABLE = {"network_error", "upstream_error"}


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


def _date(v: str) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def render(request: Request, name: str, **context) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(request, name, context)


def _history(request: Request) -> dict:
    return {"runs": ctx_of(request).repo.list_runs(50)}


def _outcome_response(request: Request, outcome: SearchOutcome) -> Response:
    extra = {"u": ctx_of(request).usage.state(), **_history(request)}
    if outcome.error:
        resp = render(
            request,
            "partials/error.html",
            outcome=outcome,
            hint=ERROR_HINTS.get(outcome.error.status, ""),
            retryable=outcome.error.status in RETRYABLE,
            **extra,
        )
        # 오류 시 이전 결과는 그대로 두고 배너만 갱신한다 (CSS로 흐리게 표시).
        resp.headers["HX-Retarget"] = "#banner"
        resp.headers["HX-Reswap"] = "innerHTML"
        return resp
    return render(request, "partials/results.html", outcome=outcome, **extra)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    c = ctx_of(request)
    return render(
        request,
        "index.html",
        mode=c.source.kind,
        u=c.usage.state(),
        fav_count=len(c.repo.favorite_numbers()),
        page_sizes=PAGE_SIZES,
        **_history(request),
    )


@router.post("/ui/search")
async def ui_search(
    request: Request,
    keyword: str = Form(""),
    title: str = Form(""),
    applicant: str = Form(""),
    ipc: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    include_patent: str = Form(""),
    include_utility: str = Form(""),
    page: int = Form(1),
    page_size: int = Form(20),
    refresh: str = Form(""),
):
    query = SearchQuery(
        keyword=keyword.strip() or None,
        title=title.strip() or None,
        applicant=applicant.strip() or None,
        ipc=ipc.strip() or None,
        date_from=_date(date_from),
        date_to=_date(date_to),
        include_patent=bool(include_patent),
        include_utility=bool(include_utility),
        page=max(1, page),
        page_size=page_size if page_size in PAGE_SIZES else 20,
    )
    outcome = await ctx_of(request).search.search(query, refresh=bool(refresh))
    return _outcome_response(request, outcome)


@router.post("/ui/runs/{run_id}/rerun")
async def ui_rerun(request: Request, run_id: int, refresh: str = Form("")):
    outcome = await ctx_of(request).search.rerun(run_id, refresh=bool(refresh))
    if outcome is None:
        return Response(status_code=404)
    return _outcome_response(request, outcome)


@router.post("/ui/compare", response_class=HTMLResponse)
async def ui_compare(
    request: Request,
    keyword_a: str = Form(""),
    keyword_b: str = Form(""),
    include_patent: str = Form(""),
    include_utility: str = Form(""),
    refresh: str = Form(""),
):
    def q(keyword: str) -> SearchQuery:
        return SearchQuery(
            keyword=keyword.strip() or None,
            include_patent=bool(include_patent),
            include_utility=bool(include_utility),
        )

    qa, qb = q(keyword_a), q(keyword_b)
    c = ctx_of(request)
    if qa.is_empty() or qb.is_empty():
        return render(
            request, "partials/compare_result.html", message="조건 A와 B 키워드를 모두 입력하세요."
        )
    try:
        result = await c.search.compare(qa, qb, refresh=bool(refresh))
    except SourceError as e:
        return render(
            request,
            "partials/compare_result.html",
            error=e,
            hint=ERROR_HINTS.get(e.status, ""),
            u=c.usage.state(),
        )
    return render(
        request, "partials/compare_result.html", r=result, qa=qa, qb=qb, u=c.usage.state()
    )


@router.get("/ui/patents/{application_number}", response_class=HTMLResponse)
async def ui_detail(request: Request, application_number: str):
    c = ctx_of(request)
    record = await c.search.get_detail(application_number)
    if record is None:
        return render(request, "partials/detail_missing.html")
    fav = c.favorites.get(record.application_number)
    return render(request, "partials/detail.html", r=record, fav=fav)


@router.get("/ui/favorites", response_class=HTMLResponse)
def ui_favorites(request: Request):
    return render(request, "partials/favorites_list.html", items=ctx_of(request).favorites.list())


def _fav_response(request: Request, number: str, *, detail: bool, view: str) -> HTMLResponse:
    c = ctx_of(request)
    fav = c.favorites.get(number)
    record = c.repo.get_patent(number)
    return render(
        request,
        "partials/fav_update.html",
        n=number,
        r=record,
        fav=fav,
        detail=detail,
        view=view,
        items=c.favorites.list(),
        fav_count=len(c.repo.favorite_numbers()),
    )


@router.post("/ui/favorites/{application_number}/toggle", response_class=HTMLResponse)
def ui_toggle_favorite(
    request: Request,
    application_number: str,
    from_detail: str = Form(""),
    view: str = Form(""),
    run_id: int | None = Form(None),
):
    c = ctx_of(request)
    if c.favorites.get(application_number):
        c.favorites.remove(application_number)
    else:
        c.favorites.save(application_number, "", run_id)
    return _fav_response(request, application_number, detail=bool(from_detail), view=view)


@router.post("/ui/favorites/{application_number}/note", response_class=HTMLResponse)
def ui_save_note(request: Request, application_number: str, note: str = Form("")):
    ctx_of(request).favorites.save(application_number, note)
    return _fav_response(request, application_number, detail=True, view="")
