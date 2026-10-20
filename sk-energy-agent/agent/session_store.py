"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "session.db"


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
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

    def close(self) -> None:
        self.conn.close()
