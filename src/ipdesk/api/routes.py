from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ipdesk.context import AppContext
from ipdesk.domain.errors import SourceError
from ipdesk.domain.models import PAGE_SIZES, PatentRecord, SearchQuery, SearchRun
from ipdesk.services.search import CompareResult, SearchOutcome

router = APIRouter(prefix="/api")

ERROR_HTTP_CODES = {
    "auth_error": 401,
    "quota_error": 429,
    "network_error": 502,
    "upstream_error": 502,
    "parse_error": 502,
    "api_error": 502,
}


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


class SearchRequest(BaseModel):
    keyword: str | None = None
    title: str | None = None
    applicant: str | None = None
    ipc: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    include_patent: bool = True
    include_utility: bool = True
    page: int = Field(1, ge=1)
    page_size: int = 20
    refresh: bool = False  # True면 캐시를 무시하고 새로 조회

    def to_query(self) -> SearchQuery:
        if self.page_size not in PAGE_SIZES:
            raise HTTPException(422, f"page_size는 {PAGE_SIZES} 중 하나여야 합니다")
        data = {k: (v or None) if isinstance(v, str) else v for k, v in self if k != "refresh"}
        return SearchQuery(**data)


class CompareRequest(BaseModel):
    a: SearchRequest
    b: SearchRequest


class NoteBody(BaseModel):
    note: str = ""


def record_to_dict(r: PatentRecord, is_favorite: bool = False) -> dict:
    return {
        "application_number": r.application_number,
        "display_number": r.display_number,
        "right_type": r.right_type.value,
        "title": r.title,
        "applicants": r.applicants,
        "application_date": r.application_date.isoformat() if r.application_date else None,
        "status": r.status,
        "ipc_codes": r.ipc_codes,
        "abstract": r.abstract,
        "open_number": r.open_number,
        "open_date": r.open_date.isoformat() if r.open_date else None,
        "register_number": r.register_number,
        "register_date": r.register_date.isoformat() if r.register_date else None,
        "drawing_url": r.drawing_url,
        "is_favorite": is_favorite,
    }


def run_to_dict(run: SearchRun, from_cache: bool | None = None) -> dict:
    return {
        "id": run.id,
        "query": run.query.to_dict(),
        "source": run.source.value,
        "executed_at": run.executed_at.isoformat(),
        "total_count": run.total_count,
        "result_code": run.result_code,
        "status": run.status,
        "from_cache": run.from_cache if from_cache is None else from_cache,
    }


def outcome_response(o: SearchOutcome) -> JSONResponse:
    if o.error:
        return JSONResponse(
            {
                "status": o.error.status,
                "message": o.error.message,
                "result_code": o.error.result_code,
                "run": run_to_dict(o.run),
            },
            status_code=ERROR_HTTP_CODES.get(o.error.status, 502),
        )
    return JSONResponse(
        {
            "status": o.run.status,
            "records": [record_to_dict(r, r.application_number in o.favorites) for r in o.records],
            "run": run_to_dict(o.run, o.from_cache),
            "pagination": {
                "page": o.query.page,
                "page_size": o.query.page_size,
                "total_pages": o.total_pages,
            },
        }
    )


@router.get("/health")
def health(request: Request):
    c = ctx_of(request)
    u = c.usage.state()
    return {
        "mode": c.source.kind.value,
        "key_configured": bool(c.settings.kipris_api_key.strip()),
        "monthly_usage": u.used,
        "monthly_limit": u.limit,
        "usage_level": u.level,
    }


@router.post("/search")
async def search(body: SearchRequest, request: Request):
    return outcome_response(
        await ctx_of(request).search.search(body.to_query(), refresh=body.refresh)
    )


def compare_to_dict(c: CompareResult) -> dict:
    return {
        "count_a": c.count_a,
        "count_b": c.count_b,
        "both": len(c.both),
        "only_a": len(c.only_a),
        "only_b": len(c.only_b),
        "fetched_a": c.fetched_a,
        "fetched_b": c.fetched_b,
        "truncated": c.truncated,  # True면 both/only_*는 가져온 범위 안의 하한값
        "runs": {"a": c.run_a.id, "b": c.run_b.id},
        "records": {
            "both": [record_to_dict(r) for r in c.both],
            "only_a": [record_to_dict(r) for r in c.only_a],
            "only_b": [record_to_dict(r) for r in c.only_b],
        },
    }


@router.post("/compare")
async def compare(body: CompareRequest, request: Request):
    try:
        result = await ctx_of(request).search.compare(
            body.a.to_query(), body.b.to_query(), refresh=body.a.refresh or body.b.refresh
        )
    except SourceError as e:
        return JSONResponse(
            {"status": e.status, "message": e.message, "result_code": e.result_code},
            status_code=ERROR_HTTP_CODES.get(e.status, 502),
        )
    return {"status": "ok", **compare_to_dict(result)}


@router.get("/patents/{application_number}")
async def patent_detail(application_number: str, request: Request):
    c = ctx_of(request)
    record = await c.search.get_detail(application_number)
    if record is None:
        raise HTTPException(404, "해당 출원번호의 문헌을 찾을 수 없습니다")
    fav = c.favorites.get(record.application_number)
    d = record_to_dict(record, fav is not None)
    d["note"] = fav.note if fav else ""
    return d


@router.get("/runs")
def runs(request: Request):
    return {"runs": [run_to_dict(r) for r in ctx_of(request).repo.list_runs(50)]}


@router.post("/runs/{run_id}/rerun")
async def rerun(run_id: int, request: Request, refresh: bool = False):
    outcome = await ctx_of(request).search.rerun(run_id, refresh=refresh)
    if outcome is None:
        raise HTTPException(404, "검색 이력을 찾을 수 없습니다")
    return outcome_response(outcome)


@router.get("/favorites")
def favorites(request: Request):
    items = ctx_of(request).favorites.list()
    return {
        "favorites": [
            {**record_to_dict(p, True), "note": f.note, "added_at": f.added_at.isoformat()}
            for f, p in items
        ]
    }


@router.put("/favorites/{application_number}")
def put_favorite(application_number: str, body: NoteBody, request: Request):
    if not ctx_of(request).favorites.save(application_number, body.note):
        raise HTTPException(404, "먼저 검색하거나 상세를 열어 문헌을 불러와야 합니다")
    return {"status": "ok"}


@router.delete("/favorites/{application_number}")
def delete_favorite(application_number: str, request: Request):
    if not ctx_of(request).favorites.remove(application_number):
        raise HTTPException(404, "즐겨찾기에 없는 문헌입니다")
    return {"status": "ok"}


@router.get("/export.csv")
def export_csv(request: Request, scope: str | None = None, run_id: int | None = None):
    svc = ctx_of(request).export
    if scope == "favorites":
        filename, data = svc.favorites_csv()
    elif run_id is not None:
        found = svc.run_csv(run_id)
        if found is None:
            raise HTTPException(404, "검색 이력을 찾을 수 없습니다")
        filename, data = found
    else:
        raise HTTPException(400, "scope=favorites 또는 run_id 중 하나를 지정하세요")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
