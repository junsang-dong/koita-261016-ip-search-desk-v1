import csv
import io
from pathlib import Path

from ipdesk.domain.models import ExportItem
from ipdesk.repo.repositories import Repository
from ipdesk.services.clock import now_kst

COLUMNS = [
    "출원번호",
    "권리구분",
    "발명의명칭",
    "출원인",
    "출원일",
    "상태",
    "IPC",
    "초록",
    "메모",
    "조회조건",
    "조회시각",
    "출처",
]
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value: object) -> str:
    """줄바꿈을 공백으로 펴고, 엑셀 수식으로 해석될 시작 문자에는 '를 붙인다."""
    text = " ".join(str(value if value is not None else "").split())
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def build_csv(items: list[ExportItem]) -> bytes:
    """UTF-8 BOM + CRLF. 엑셀에서 한글이 깨지지 않는다."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for it in items:
        r = it.record
        row = [
            r.display_number,
            r.right_type.label,
            r.title,
            "; ".join(r.applicants),
            r.application_date.isoformat() if r.application_date else "",
            r.status or "",
            "; ".join(r.ipc_codes),
            r.abstract or "",
            it.note,
            it.query_text,
            it.queried_at,
            it.source.label,
        ]
        writer.writerow([sanitize_cell(c) for c in row])
    return buf.getvalue().encode("utf-8-sig")


class ExportService:
    def __init__(self, repo: Repository, export_dir: Path):
        self.repo = repo
        self.export_dir = export_dir

    def _finish(self, scope: str, items: list[ExportItem]) -> tuple[str, bytes]:
        data = build_csv(items)
        filename = f"ipdesk_{scope}_{now_kst().strftime('%Y%m%d_%H%M')}.csv"
        self.export_dir.mkdir(parents=True, exist_ok=True)
        (self.export_dir / filename).write_bytes(data)  # exports/ 폴더에도 사본을 남긴다
        return filename, data

    def favorites_csv(self) -> tuple[str, bytes]:
        return self._finish("favorites", self.repo.export_favorites())

    def run_csv(self, run_id: int) -> tuple[str, bytes] | None:
        if self.repo.get_run(run_id) is None:
            return None
        return self._finish(f"run{run_id}", self.repo.export_run(run_id))
