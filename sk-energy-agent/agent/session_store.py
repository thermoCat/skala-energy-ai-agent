"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "session.db"


def default_db_path() -> Path:
    """대화·판단 이력 DB 경로를 반환하는 함수

    서버와 MCP 서버(하위 프로세스)가 같은 파일을 보도록 SESSION_DB_PATH 환경변수를 먼저 본다.

    Returns:
        SQLite 파일 경로
    """
    return Path(os.environ.get("SESSION_DB_PATH") or DB_PATH)


class SqliteSessionStore:
    def __init__(self, db_path: Path | None = None):
        self.conn = sqlite3.connect(db_path or default_db_path(), check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        # 판별 기록: LLM 판별·기준 판정·교정 여부 (gate 노드만 씀)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS decision_log ("
            " id INTEGER PRIMARY KEY, company TEXT, scope_type TEXT, scope_id TEXT,"
            " snapshot TEXT, rule_version TEXT, llm_verdict TEXT, baseline TEXT,"
            " verdict TEXT, corrected INTEGER, payload JSON, created_at TEXT)"
        )
        # 사람의 결정: 대시보드 API만 씀 (에이전트는 쓰지 못함)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS human_decision ("
            " id INTEGER PRIMARY KEY, company TEXT, station_id TEXT, gu TEXT, choice TEXT,"
            " conversion_type TEXT, note TEXT, decided_by TEXT, created_at TEXT)"
        )
        # 상시 작업 결과: 알림함과 작업 실행 기록 (LLM 호출 수로 비용을 확인)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS alert ("
            " id INTEGER PRIMARY KEY, company TEXT, job TEXT, kind TEXT, scope_id TEXT,"
            " window TEXT, message TEXT, payload JSON, created_at TEXT, read INTEGER DEFAULT 0)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS job_run ("
            " id INTEGER PRIMARY KEY, job TEXT, company TEXT, scope_id TEXT, started_at TEXT,"
            " finished_at TEXT, status TEXT, llm_calls INTEGER, alert_id INTEGER, error TEXT)"
        )
        self.conn.commit()

    def load(self, thread_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
            (thread_id,),
        ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        self.conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (thread_id, role, content, datetime.now().isoformat()),
        )
        self.conn.commit()

    def log_decision(self, company: str, scope_type: str, scope_id: str,
                     snapshot: str, rule_version: str, llm_verdict: dict,
                     baseline: dict, verdict: dict, corrections: list[dict]) -> None:
        """게이트의 판별 결과 1건을 decision_log 에 저장하는 함수

        Args:
            company: 회사 (예: "SK에너지")
            scope_type: "station" 또는 "gu"
            scope_id: 주유소 번호 또는 자치구 이름
            snapshot: 사용한 스냅샷 이름
            rule_version: 기준 규칙 버전
            llm_verdict: 분석 에이전트 판별
            baseline: 코드 기준 판정
            verdict: 게이트 확정 판정
            corrections: 교정된 항목 목록
        """
        dump = lambda v: json.dumps(v, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO decision_log (company, scope_type, scope_id, snapshot,"
            " rule_version, llm_verdict, baseline, verdict, corrected, payload, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (company, scope_type, scope_id, snapshot, rule_version, dump(llm_verdict),
             dump(baseline), dump(verdict), int(bool(corrections)),
             dump({"corrections": corrections}), datetime.now().isoformat()),
        )
        self.conn.commit()

    def load_decisions(self, scope_id: str) -> list[dict]:
        """대상 하나의 판별 기록을 오래된 순서로 반환하는 함수

        Args:
            scope_id: 주유소 번호 또는 자치구 이름
        Returns:
            decision_log 행 dict 목록
        """
        cur = self.conn.execute(
            "SELECT * FROM decision_log WHERE scope_id=? ORDER BY id", (scope_id,))
        names = [c[0] for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]

    def record_human_decision(self, company: str, station_id: str, gu: str, choice: str,
                              conversion_type: str | None, note: str,
                              decided_by: str) -> dict:
        """사람이 입력한 결정 1건을 저장하고 저장한 행을 돌려주는 함수

        Args:
            company: 회사
            station_id: 주유소 번호
            gu: 자치구
            choice: "진행" | "보류" | "제외"
            conversion_type: 전환 유형 선택지 (없으면 None)
            note: 메모 (예: "부지 협의 필요")
            decided_by: 결정한 사람
        Returns:
            저장한 결정 dict
        """
        row = {"company": company, "station_id": station_id, "gu": gu, "choice": choice,
               "conversion_type": conversion_type, "note": note, "decided_by": decided_by,
               "created_at": datetime.now().isoformat(timespec="seconds")}
        cur = self.conn.execute(
            "INSERT INTO human_decision (company, station_id, gu, choice, conversion_type,"
            " note, decided_by, created_at) VALUES (?,?,?,?,?,?,?,?)", tuple(row.values()))
        self.conn.commit()
        return {"id": cur.lastrowid, **row}

    def save_alert(self, company: str, job: str, kind: str, scope_id: str, window: str,
                   message: str, payload: dict) -> tuple[int, bool]:
        """알림 1건을 저장하는 함수 — 같은 회사·범위·감시 구간 알림이 이미 있으면 새로 만들지 않음

        Args:
            company: 회사
            job: 작업 이름 (예: "daily_watch")
            kind: 알림 종류 (예: "new_charger_failure")
            scope_id: 범위 (회사 전체면 회사 이름, 자치구 감시면 자치구)
            window: 감시 구간 (같은 데이터로 두 번 돌려도 알림이 1건이게 하는 열쇠)
            message: 알림 문장
            payload: 거점 목록 등 화면용 원본
        Returns:
            (알림 id, 새로 만들었는지)
        """
        found = self.conn.execute(
            "SELECT id FROM alert WHERE company=? AND job=? AND scope_id=? AND window=?",
            (company, job, scope_id, window)).fetchone()
        if found:
            return found[0], False
        cur = self.conn.execute(
            "INSERT INTO alert (company, job, kind, scope_id, window, message, payload,"
            " created_at) VALUES (?,?,?,?,?,?,?,?)",
            (company, job, kind, scope_id, window, message,
             json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        return cur.lastrowid, True

    def list_alerts(self, company: str, unread_only: bool = False) -> list[dict]:
        """회사 알림을 최신순으로 반환하는 함수

        Args:
            company: 회사
            unread_only: 안 읽은 것만
        Returns:
            알림 dict 목록 (payload 는 dict 로 풀어서)
        """
        sql = "SELECT * FROM alert WHERE company=?" + (" AND read=0" if unread_only else "")
        cur = self.conn.execute(sql + " ORDER BY id DESC", (company,))
        names = [c[0] for c in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchall()]
        return [{**r, "payload": json.loads(r["payload"])} for r in rows]

    def mark_alert_read(self, alert_id: int, company: str) -> bool:
        """알림을 읽음으로 표시하는 함수 (다른 회사 알림은 건드리지 않음)

        Args:
            alert_id: 알림 id
            company: 회사
        Returns:
            표시했는지
        """
        cur = self.conn.execute("UPDATE alert SET read=1 WHERE id=? AND company=?",
                                (alert_id, company))
        self.conn.commit()
        return cur.rowcount == 1

    def save_job_run(self, job: str, company: str, scope_id: str, started_at: str,
                     status: str, llm_calls: int, alert_id: int | None,
                     error: str | None = None) -> int:
        """상시 작업 실행 1건을 기록하는 함수

        Args:
            job: 작업 이름
            company: 회사
            scope_id: 범위
            started_at: 시작 시각
            status: "ok" | "error"
            llm_calls: 이번 실행의 LLM 호출 수 (이상 없으면 0이어야 함)
            alert_id: 만든 알림 id (없으면 None)
            error: 오류 설명
        Returns:
            실행 기록 id
        """
        cur = self.conn.execute(
            "INSERT INTO job_run (job, company, scope_id, started_at, finished_at, status,"
            " llm_calls, alert_id, error) VALUES (?,?,?,?,?,?,?,?,?)",
            (job, company, scope_id, started_at, datetime.now().isoformat(timespec="seconds"),
             status, llm_calls, alert_id, error))
        self.conn.commit()
        return cur.lastrowid

    def close(self) -> None:
        self.conn.close()


def read_decision_log(db_path: Path, company: str, station_id: str | None = None,
                      gu: str | None = None, limit: int = 10) -> dict:
    """사람 결정과 게이트 판별 기록을 읽기 전용으로 조회하는 함수 (MCP get_decision_log 용)

    Args:
        db_path: SQLite 파일 경로
        company: 회사 — 다른 회사 기록은 보지 않는다
        station_id: 주유소 번호 (주유소 단위 조회)
        gu: 자치구 (묶음 단위 조회 — 그 구의 사람 결정 전부)
        limit: 종류별 최대 건수 (최신순)
    Returns:
        {"human": [...], "judged": [...]}
    """
    if not Path(db_path).exists():
        return {"human": [], "judged": []}
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        where, args = ("station_id=?", station_id) if station_id else ("gu=?", gu)
        human = conn.execute(
            f"SELECT * FROM human_decision WHERE company=? AND {where}"
            " ORDER BY id DESC LIMIT ?", (company, args, limit)).fetchall()
        scope = station_id or gu
        judged = conn.execute(
            "SELECT scope_type, scope_id, rule_version, corrected, verdict, created_at"
            " FROM decision_log WHERE company=? AND scope_id=? ORDER BY id DESC LIMIT ?",
            (company, scope, limit)).fetchall()
    except sqlite3.OperationalError:   # 아직 표가 없는 새 DB
        return {"human": [], "judged": []}
    finally:
        conn.close()
    return {"human": [dict(r) for r in human],
            "judged": [{**dict(r), "verdict": json.loads(r["verdict"])} for r in judged]}
