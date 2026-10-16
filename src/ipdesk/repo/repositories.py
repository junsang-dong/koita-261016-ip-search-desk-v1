import json
import sqlite3
from datetime import date, datetime

from ipdesk.domain.models import (
    ExportItem,
    Favorite,
    PatentRecord,
    RightType,
    SearchQuery,
    SearchRun,
    SourceKind,
)
from ipdesk.repo.db import Database


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _iso(v: date | None) -> str | None:
    return v.isoformat() if v else None


def _row_to_patent(r: sqlite3.Row) -> PatentRecord:
    return PatentRecord(
        application_number=r["application_number"],
        right_type=RightType(r["right_type"]),
        title=r["title"],
        applicants=json.loads(r["applicants"] or "[]"),
        application_date=_d(r["application_date"]),
        status=r["status"],
        ipc_codes=json.loads(r["ipc_codes"] or "[]"),
        abstract=r["abstract"],
        open_number=r["open_number"],
        open_date=_d(r["open_date"]),
        register_number=r["register_number"],
        register_date=_d(r["register_date"]),
        drawing_url=r["drawing_url"],
    )


def _row_to_run(r: sqlite3.Row, numbers: list[str] | None = None) -> SearchRun:
    return SearchRun(
        id=r["id"],
        query=SearchQuery.from_dict(json.loads(r["query_json"])),
        source=SourceKind(r["source"]),
        executed_at=datetime.fromisoformat(r["executed_at"]),
        total_count=r["total_count"],
        result_code=r["result_code"],
        status=r["status"],
        from_cache=bool(r["from_cache"]),
        application_numbers=numbers or [],
    )


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ---- patents -------------------------------------------------------
    def upsert_patents(
        self, records: list[PatentRecord], source: SourceKind, fetched_at: datetime
    ) -> None:
        rows = [
            (
                r.application_number,
                r.right_type.value,
                r.title,
                json.dumps(r.applicants, ensure_ascii=False),
                _iso(r.application_date),
                r.status,
                json.dumps(r.ipc_codes, ensure_ascii=False),
                r.abstract,
                r.open_number,
                _iso(r.open_date),
                r.register_number,
                _iso(r.register_date),
                r.drawing_url,
                source.value,
                fetched_at.isoformat(),
            )
            for r in records
        ]
        with self.db.connect() as conn:
            conn.executemany(
                """INSERT INTO patents (application_number, right_type, title, applicants,
                       application_date, status, ipc_codes, abstract, open_number, open_date,
                       register_number, register_date, drawing_url, source, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(application_number) DO UPDATE SET
                       right_type=excluded.right_type, title=excluded.title,
                       applicants=excluded.applicants, application_date=excluded.application_date,
                       status=excluded.status, ipc_codes=excluded.ipc_codes,
                       abstract=excluded.abstract, open_number=excluded.open_number,
                       open_date=excluded.open_date, register_number=excluded.register_number,
                       register_date=excluded.register_date, drawing_url=excluded.drawing_url,
                       source=excluded.source, fetched_at=excluded.fetched_at""",
                rows,
            )

    def get_patent(self, application_number: str) -> PatentRecord | None:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM patents WHERE application_number = ?", (application_number,)
            ).fetchone()
        return _row_to_patent(r) if r else None

    # ---- runs ----------------------------------------------------------
    def create_run(self, run: SearchRun) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO search_runs (query_json, query_hash, source, executed_at,
                       total_count, result_code, status, from_cache)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    json.dumps(run.query.to_dict(), ensure_ascii=False),
                    run.query.hash(),
                    run.source.value,
                    run.executed_at.isoformat(),
                    run.total_count,
                    run.result_code,
                    run.status,
                    int(run.from_cache),
                ),
            )
            return cur.lastrowid

    def add_run_records(self, run_id: int, numbers: list[str], start_rank: int) -> None:
        with self.db.connect() as conn:
            conn.executemany(
                """INSERT OR IGNORE INTO run_records (run_id, application_number, rank)
                   VALUES (?,?,?)""",
                [(run_id, n, start_rank + i) for i, n in enumerate(numbers)],
            )

    def get_run(self, run_id: int) -> SearchRun | None:
        with self.db.connect() as conn:
            r = conn.execute("SELECT * FROM search_runs WHERE id = ?", (run_id,)).fetchone()
            if not r:
                return None
            nums = [
                x[0]
                for x in conn.execute(
                    "SELECT application_number FROM run_records WHERE run_id = ? ORDER BY rank",
                    (run_id,),
                )
            ]
        return _row_to_run(r, nums)

    def latest_run_by_hash(self, query_hash: str) -> SearchRun | None:
        """같은 검색 조건(쪽 번호 제외)의 가장 최근 정상 실행."""
        with self.db.connect() as conn:
            r = conn.execute(
                """SELECT * FROM search_runs WHERE query_hash = ? AND status IN ('ok','empty')
                   ORDER BY id DESC LIMIT 1""",
                (query_hash,),
            ).fetchone()
        return _row_to_run(r) if r else None

    def list_runs(self, limit: int = 50) -> list[SearchRun]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM search_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    # ---- favorites -----------------------------------------------------
    def upsert_favorite(
        self, application_number: str, note: str, now: datetime, run_id: int | None
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO favorites (application_number, note, added_at, added_from_run)
                   VALUES (?,?,?,?)
                   ON CONFLICT(application_number) DO UPDATE SET note = excluded.note""",
                (application_number, note, now.isoformat(), run_id),
            )

    def delete_favorite(self, application_number: str) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                "DELETE FROM favorites WHERE application_number = ?", (application_number,)
            )
            return cur.rowcount > 0

    def get_favorite(self, application_number: str) -> Favorite | None:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT * FROM favorites WHERE application_number = ?", (application_number,)
            ).fetchone()
        return _row_to_fav(r) if r else None

    def list_favorites(self) -> list[tuple[Favorite, PatentRecord]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT f.application_number AS f_number, f.note, f.added_at, f.added_from_run,
                          p.* FROM favorites f JOIN patents p USING (application_number)
                   ORDER BY f.added_at DESC"""
            ).fetchall()
        return [
            (
                Favorite(
                    application_number=r["application_number"],
                    note=r["note"] or "",
                    added_at=datetime.fromisoformat(r["added_at"]),
                    added_from_run=r["added_from_run"],
                ),
                _row_to_patent(r),
            )
            for r in rows
        ]

    def favorite_numbers(self) -> set[str]:
        with self.db.connect() as conn:
            return {r[0] for r in conn.execute("SELECT application_number FROM favorites")}

    # ---- 응답 캐시 ------------------------------------------------------
    def get_cache(self, key: str) -> tuple[str, datetime] | None:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT payload_json, created_at FROM response_cache WHERE query_hash = ?", (key,)
            ).fetchone()
        return (r["payload_json"], datetime.fromisoformat(r["created_at"])) if r else None

    def put_cache(self, key: str, payload_json: str, now: datetime) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO response_cache (query_hash, payload_json, created_at) VALUES (?,?,?)
                   ON CONFLICT(query_hash) DO UPDATE SET
                       payload_json = excluded.payload_json, created_at = excluded.created_at""",
                (key, payload_json, now.isoformat()),
            )

    # ---- 월 사용량 ------------------------------------------------------
    def get_usage(self, month: str) -> int:
        with self.db.connect() as conn:
            r = conn.execute(
                "SELECT call_count FROM api_usage WHERE month = ?", (month,)
            ).fetchone()
        return r[0] if r else 0

    def incr_usage(self, month: str) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """INSERT INTO api_usage (month, call_count) VALUES (?, 1)
                   ON CONFLICT(month) DO UPDATE SET call_count = call_count + 1""",
                (month,),
            )
            return conn.execute(
                "SELECT call_count FROM api_usage WHERE month = ?", (month,)
            ).fetchone()[0]

    # ---- CSV 내보내기용 ---------------------------------------------------
    def export_favorites(self) -> list[ExportItem]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT p.*, f.note AS fav_note, f.added_from_run AS from_run
                   FROM favorites f JOIN patents p USING (application_number)
                   ORDER BY f.added_at DESC"""
            ).fetchall()
        runs: dict[int, SearchRun | None] = {}
        items = []
        for r in rows:
            run_id = r["from_run"]
            if run_id is not None and run_id not in runs:
                runs[run_id] = self.get_run(run_id)
            run = runs.get(run_id)
            items.append(_export_item(r, r["fav_note"], run))
        return items

    def export_run(self, run_id: int) -> list[ExportItem]:
        run = self.get_run(run_id)
        if run is None:
            return []
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT p.*, f.note AS fav_note
                   FROM run_records rr JOIN patents p USING (application_number)
                   LEFT JOIN favorites f USING (application_number)
                   WHERE rr.run_id = ? ORDER BY rr.rank""",
                (run_id,),
            ).fetchall()
        return [_export_item(r, r["fav_note"], run) for r in rows]


def _export_item(r: sqlite3.Row, note: str | None, run: SearchRun | None) -> ExportItem:
    if run is not None:
        source, queried_at, text = run.source, run.executed_at.isoformat(), run.query.describe()
    else:  # 검색 없이 상세에서 직접 추가한 즐겨찾기
        source, queried_at, text = SourceKind(r["source"]), r["fetched_at"], "(직접 추가)"
    return ExportItem(
        record=_row_to_patent(r),
        note=note or "",
        source=source,
        queried_at=queried_at,
        query_text=text,
    )


def _row_to_fav(r: sqlite3.Row) -> Favorite:
    return Favorite(
        application_number=r["application_number"],
        note=r["note"] or "",
        added_at=datetime.fromisoformat(r["added_at"]),
        added_from_run=r["added_from_run"],
    )
