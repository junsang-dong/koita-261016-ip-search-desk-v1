"""KIPRIS Plus Open API 어댑터 (HTTP + XML → 도메인 모델).

⚠️ 엔드포인트·파라미터·필드명은 명세 9장의 '예상' 값이다. 실제 응답과 다르면 아래
PARAM_MAP / FIELD_MAP / ITEM_PATHS 세 곳만 고친다. 외부 필드명은 이 파일 밖으로 새지 않는다.
"""

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from ipdesk.adapters import kipris_codes
from ipdesk.domain.errors import (
    ApiError,
    AuthError,
    NetworkError,
    ParseError,
    QuotaError,
    SourceError,
    UpstreamError,
)
from ipdesk.domain.models import (
    PatentRecord,
    SearchQuery,
    SearchResult,
    SourceKind,
    normalize_application_number,
    right_type_of,
)

log = logging.getLogger("ipdesk.kipris")

SERVICE = "patUtiModInfoSearchSevice"  # 'Sevice' 철자는 KIPRIS 원본 표기
OP_WORD = "getWordSearch"
OP_ADVANCED = "getAdvancedSearch"
OP_DETAIL = "getBibliographyDetailInfoSearch"

# SearchQuery 필드 → KIPRIS 요청 파라미터 (명세 9.2, 확인 필요)
PARAM_MAP = {
    "keyword": "word",
    "title": "inventionTitle",
    "applicant": "applicant",
    "ipc": "ipcNumber",
    "date_range": "applicationDate",
    "include_patent": "patent",
    "include_utility": "utility",
    "page": "pageNo",
    "page_size": "numOfRows",
    "detail_number": "applicationNumber",
}

# PatentRecord 필드 → KIPRIS 응답 태그 (명세 9.3, 확인 필요). 튜플이면 앞에서부터 먼저 있는 값.
FIELD_MAP = {
    "application_number": ("applicationNumber",),
    "title": ("inventionTitle",),
    "applicants": ("applicantName",),
    "application_date": ("applicationDate",),
    "status": ("registerStatus",),
    "ipc_codes": ("ipcNumber",),
    "abstract": ("astrtCont",),
    "open_number": ("openNumber",),
    "open_date": ("openDate",),
    "register_number": ("registerNumber",),
    "register_date": ("registerDate",),
    "drawing_url": ("drawing", "bigDrawing"),
}
TOTAL_COUNT_TAGS = ("totalCount",)
ITEM_PATHS = ("body/items",)  # 이 아래의 직계 자식을 문헌 1건으로 본다 (태그 이름은 무시)
DETAIL_PATH = "body"  # 상세 응답은 이 아래 모든 말단 태그를 평탄화해 FIELD_MAP으로 읽는다

MAX_RETRIES = 2
BACKOFF_BASE_SEC = 0.5

_KEY_RE = re.compile(r"(ServiceKey=)[^&\s\"']*", re.IGNORECASE)


def mask_secrets(text: str) -> str:
    """로그·오류 문자열에서 ServiceKey 값을 가린다."""
    return _KEY_RE.sub(r"\1***", text)


class KeyMaskingFilter(logging.Filter):
    """httpx 등 어떤 로거가 URL을 찍더라도 ServiceKey는 ***로 바꾼다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "servicekey" in msg.lower():
            record.msg = mask_secrets(msg)
            record.args = ()
        return True


_masking_filter = KeyMaskingFilter()


def install_key_masking() -> None:
    """여러 번 호출해도 안전하다 (logging은 같은 필터 객체를 한 번만 등록한다)."""
    for name in ("httpx", "httpcore", "ipdesk"):
        logging.getLogger(name).addFilter(_masking_filter)
    for h in logging.getLogger().handlers:
        h.addFilter(_masking_filter)


def _parse_date(s: str | None) -> date | None:
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _split(s: str | None) -> list[str]:
    return [p.strip() for p in re.split(r"[|,]", s or "") if p.strip()]


def _flatten(el) -> dict[str, str]:
    """요소 아래 모든 말단 태그를 {태그: 텍스트}로. 같은 태그는 첫 값을 유지."""
    out: dict[str, str] = {}
    for node in el.iter():
        if len(node) == 0 and node.text and node.text.strip():
            out.setdefault(node.tag, node.text.strip())
    return out


def _pick(flat: dict[str, str], field: str) -> str | None:
    for tag in FIELD_MAP[field]:
        if flat.get(tag):
            return flat[tag]
    return None


def _to_record(flat: dict[str, str]) -> PatentRecord | None:
    raw_number = _pick(flat, "application_number")
    if not raw_number:
        log.warning("출원번호가 없는 항목을 건너뜁니다")
        return None
    number, normalized = normalize_application_number(raw_number)
    if not normalized:
        log.warning("출원번호 정규화 실패(13자리 아님): %s", raw_number)
    return PatentRecord(
        application_number=number,
        right_type=right_type_of(number),
        title=_pick(flat, "title") or "(제목 없음)",
        applicants=_split(_pick(flat, "applicants")),
        application_date=_parse_date(_pick(flat, "application_date")),
        status=_pick(flat, "status"),
        ipc_codes=[p.strip() for p in (_pick(flat, "ipc_codes") or "").split("|") if p.strip()],
        abstract=_pick(flat, "abstract"),
        open_number=_pick(flat, "open_number"),
        open_date=_parse_date(_pick(flat, "open_date")),
        register_number=_pick(flat, "register_number"),
        register_date=_parse_date(_pick(flat, "register_date")),
        drawing_url=_pick(flat, "drawing_url"),
    )


class KiprisAdapter:
    kind = SourceKind.KIPRIS

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://plus.kipris.or.kr/kipo-api/kipi",
        timeout_sec: float = 10,
        raw_dir: Path | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        before_request: Callable[[], Awaitable[None]] | None = None,
    ):
        # before_request: HTTP 시도 직전마다 호출(재시도 포함). 속도 제한·사용량·월 한도용.
        self._before_request = before_request
        self._key = api_key.strip()
        self._base = base_url.rstrip("/")
        self._timeout = timeout_sec
        self._raw_dir = raw_dir  # 디버그 모드에서만 지정. 마스킹된 원문을 저장한다.
        self._sleep = sleep
        self.last_raw: str | None = None  # 마지막 응답 원문(마스킹) — `ip-desk check --raw`용

    # ---- 요청 만들기 ------------------------------------------------------
    def _url(self, operation: str, params: dict[str, str]) -> str:
        # ServiceKey는 발급받은 그대로 붙인다. 이미 %가 포함된 키를 다시 인코딩하면 인증에 실패한다.
        qs = f"ServiceKey={self._key}&" + urlencode(params, quote_via=quote)
        return f"{self._base}/{SERVICE}/{operation}?{qs}"

    def _search_request(self, q: SearchQuery) -> tuple[str, dict[str, str]]:
        p = PARAM_MAP
        params: dict[str, str] = {}
        advanced = any([q.title, q.applicant, q.ipc, q.date_from, q.date_to])
        if q.keyword:
            params[p["keyword"]] = q.keyword
        if q.title:
            params[p["title"]] = q.title
        if q.applicant:
            params[p["applicant"]] = q.applicant
        if q.ipc:
            params[p["ipc"]] = q.ipc
        if q.date_from or q.date_to:
            start = (q.date_from or date(1960, 1, 1)).strftime("%Y%m%d")
            end = (q.date_to or date.today()).strftime("%Y%m%d")
            params[p["date_range"]] = f"{start}~{end}"
        params[p["include_patent"]] = str(q.include_patent).lower()
        params[p["include_utility"]] = str(q.include_utility).lower()
        params[p["page"]] = str(q.page)
        params[p["page_size"]] = str(q.page_size)
        return (OP_ADVANCED if advanced else OP_WORD), params

    # ---- HTTP (재시도·오류 판정 1~3단계) ------------------------------------
    async def _get(self, operation: str, params: dict[str, str]) -> bytes:
        url = self._url(operation, params)
        safe_url = mask_secrets(url)
        last: SourceError | None = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                await self._sleep(BACKOFF_BASE_SEC * 2 ** (attempt - 1))
            if self._before_request:
                await self._before_request()
            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url)
            except httpx.TransportError as e:  # 타임아웃, DNS·연결 실패
                log.warning("GET %s 실패(%s) 시도 %d", safe_url, type(e).__name__, attempt + 1)
                last = NetworkError()
                continue
            ms = int((time.monotonic() - started) * 1000)
            log.info("GET %s -> %d (%dms)", safe_url, resp.status_code, ms)
            if resp.status_code >= 500:
                last = UpstreamError(result_code=f"HTTP {resp.status_code}")
                continue
            if resp.status_code in (401, 403):
                raise AuthError(result_code=f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise ApiError(
                    f"KIPRIS가 HTTP {resp.status_code}를 반환했습니다",
                    result_code=f"HTTP {resp.status_code}",
                )
            return resp.content
        assert last is not None
        raise last

    # ---- XML 해석 (4~8단계) ------------------------------------------------
    def _save_raw(self, operation: str, content: bytes) -> None:
        text = mask_secrets(content.decode("utf-8", errors="replace"))
        self.last_raw = text
        if self._raw_dir:
            self._raw_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            (self._raw_dir / f"{stamp}_{operation}.xml").write_text(text, encoding="utf-8")

    def _parse(self, operation: str, content: bytes):
        self._save_raw(operation, content)
        try:
            root = SafeET.fromstring(content)
        except (SafeET.ParseError, DefusedXmlException, ValueError) as e:
            snippet = mask_secrets(content[:200].decode("utf-8", errors="replace"))
            log.error("XML 파싱 실패 (%s): %s", type(e).__name__, snippet)
            raise ParseError() from e

        header = root.find("header")
        code_el = header.find("resultCode") if header is not None else None
        if code_el is None:  # 공공데이터포털식 오류 포맷 대비
            code_el = root.find(".//returnReasonCode")
        if code_el is None:
            raise ParseError("응답에서 resultCode를 찾지 못했습니다")
        code = (code_el.text or "").strip()
        msg_el = root.find("header/resultMsg")
        if msg_el is None:
            msg_el = root.find(".//returnAuthMsg")
        msg = ((msg_el.text if msg_el is not None else "") or "").strip()

        kind = kipris_codes.classify(code)
        if kind == "auth":
            raise AuthError(result_code=code)
        if kind == "quota":
            raise QuotaError(result_code=code)
        if kind == "other":
            raise ApiError(f"{msg or 'KIPRIS 오류'}", result_code=code)
        return root, code

    @staticmethod
    def _total(root) -> int:
        for tag in TOTAL_COUNT_TAGS:
            el = root.find(f".//{tag}")
            if el is not None and (el.text or "").strip().isdigit():
                return int(el.text.strip())
        return 0

    # ---- PatentSource 구현 ------------------------------------------------
    async def search(self, query: SearchQuery) -> SearchResult:
        if not (query.include_patent or query.include_utility):
            return SearchResult(records=[], total_count=0, result_code="00")
        if query.is_empty():
            raise ApiError("검색 조건을 하나 이상 입력하세요", result_code="client")
        operation, params = self._search_request(query)
        content = await self._get(operation, params)
        root, code = self._parse(operation, content)

        records: list[PatentRecord] = []
        for path in ITEM_PATHS:
            container = root.find(path)
            if container is None:
                continue
            for item in container:
                rec = _to_record(_flatten(item))
                if rec:
                    records.append(rec)
        total = self._total(root)
        if total == 0 and records:  # totalCount 누락 대비
            total = len(records)
        return SearchResult(records=records, total_count=total, result_code=code or "00")

    async def get_detail(self, application_number: str) -> PatentRecord | None:
        number, _ = normalize_application_number(application_number)
        params = {PARAM_MAP["detail_number"]: number}
        content = await self._get(OP_DETAIL, params)
        root, _ = self._parse(OP_DETAIL, content)
        body = root.find(DETAIL_PATH)
        if body is None:
            return None
        return _to_record(_flatten(body))
