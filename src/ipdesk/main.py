import argparse
import asyncio
import logging
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ipdesk.adapters.kipris import KiprisAdapter, install_key_masking, mask_secrets
from ipdesk.api import pages, routes
from ipdesk.config import ConfigError, Settings, load_settings
from ipdesk.context import build_context, build_usage_guard
from ipdesk.domain.errors import SourceError
from ipdesk.domain.models import SearchQuery
from ipdesk.repo.db import Database
from ipdesk.repo.repositories import Repository

WEB_DIR = Path(__file__).parent / "web"


def create_app(settings: Settings | None = None) -> FastAPI:
    install_key_masking()
    app = FastAPI(title="BCR IP Search Desk")
    app.state.ctx = build_context(settings or load_settings())
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    app.include_router(routes.router)
    app.include_router(pages.router)
    return app


def check(show_raw: bool) -> int:
    """`ip-desk check`: 실제 KIPRIS에 1건 검색을 보내 키와 연결을 확인한다."""
    settings = Settings()
    if not settings.kipris_api_key.strip():
        print("[키 없음] .env에 KIPRIS_API_KEY를 입력한 뒤 다시 실행하세요.", file=sys.stderr)
        return 1
    guard = build_usage_guard(settings, Repository(Database(settings.db_path)))
    adapter = KiprisAdapter(
        api_key=settings.kipris_api_key,
        base_url=settings.kipris_base_url,
        timeout_sec=settings.kipris_timeout_sec,
        before_request=guard.before_request,  # check 호출도 월 사용량에 센다
    )
    code = 0
    try:
        result = asyncio.run(adapter.search(SearchQuery(keyword="로봇", page_size=1)))
        print(f"[성공] resultCode={result.result_code} 전체 {result.total_count}건")
        if result.records:
            r = result.records[0]
            print(f"  첫 문헌: {r.display_number} {r.title}")
        else:
            print("  (0건: 응답 구조는 정상이나 문헌이 없거나 필드 매핑이 다를 수 있습니다)")
    except SourceError as e:
        code = 1
        print(f"[실패] {e.status}: {e.message} (resultCode={e.result_code})", file=sys.stderr)
    if show_raw and adapter.last_raw:
        print("\n--- 응답 원문 (ServiceKey 마스킹됨, 앞 3000자) ---")
        print(mask_secrets(adapter.last_raw[:3000]))
    return code


def run() -> None:
    parser = argparse.ArgumentParser(prog="ip-desk", description="BCR IP Search Desk")
    sub = parser.add_subparsers(dest="command")
    chk = sub.add_parser("check", help="KIPRIS 키 확인 (1건 검색)")
    chk.add_argument("--raw", action="store_true", help="응답 원문(키 마스킹)도 출력")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    install_key_masking()

    if args.command == "check":
        raise SystemExit(check(args.raw))

    try:
        settings = load_settings()
        app = create_app(settings)
    except ConfigError as e:
        print(f"[설정 오류] {e}", file=sys.stderr)
        raise SystemExit(1) from e

    url = f"http://{settings.host}:{settings.port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=settings.host, port=settings.port)
