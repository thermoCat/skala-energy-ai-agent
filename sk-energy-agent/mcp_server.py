"""all_total 통합 실습 — MCP Server (Wave 2 축).

Tool / Resource / Prompt 3대 capability를 모두 노출하는 실제 fastmcp 서버.
FastAPI 백엔드(server.py)가 lifespan에서 이 파일을 subprocess(stdio)로 1회 기동해
프로세스가 살아 있는 동안 재사용한다 (Wave 1 싱글턴 + Wave 2 표준화의 결합).

- Tool    get_monitoring_status : 호출마다 값이 달라지는 동적 조회 → Tool이 맞다
- Resource runbook://incident-response/{incident_type} : URI로 식별되는 정적 문서
- Prompt  incident_analysis_prompt : 여러 Tool을 조합하는 표준 진단 절차를 강제
"""
import json
import random
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from energy.indicators import ev_supply, fuel_demand, station_card
from energy.rules.score import district_baseline
from energy.store import load_snapshot

mcp = FastMCP(name="capstone-all-total-ops")

DATA_DIR = Path(__file__).parent / "data"

# 스냅샷은 서버 시작 때 1번만 로드 — 요청마다 CSV를 읽으면 응답이 느려진다
SNAPSHOT = load_snapshot()


def _with_meta(payload: dict, source: str) -> dict:
    return {**payload, "snapshot": SNAPSHOT.name, "source": source}


def _error(message: str, **detail) -> dict:
    # 예외를 던지지 않고 error 로 돌려줌 — 한 Tool 실패가 전체 답변을 멈추지 않게 (장애 격리)
    return {"error": {"message": message, **detail}, "snapshot": SNAPSHOT.name}


@mcp.tool()
def get_station_card(station_id: str) -> dict:
    """주유소 번호로 거점 기본 정보(상호·회사·시설 종류·자치구·주유소 안 충전기)를 반환합니다."""
    card = station_card(SNAPSHOT, station_id)
    if card is None:
        return _error("없는 주유소 번호", station_id=station_id)
    return _with_meta(card, "오피넷 반경 검색 + 환경공단 충전기 (seoul_stations.csv)")


@mcp.tool()
def get_fuel_demand(gu: str) -> dict:
    """자치구의 5개 연료 등록 대수와 1년 증감률(서울 전체 증감률 포함)을 반환합니다."""
    try:
        return _with_meta(fuel_demand(SNAPSHOT, gu), "서울시 OA-15640 자치구별 연료별 등록현황")
    except (KeyError, ZeroDivisionError):
        return _error("없는 자치구", gu=gu, fuel="all")


@mcp.tool()
def get_ev_supply(gu: str) -> dict:
    """자치구의 급속·완속 충전기 ID 수, 급속 충전 중 비율, 전기차 1만 대당 급속 ID를 반환합니다."""
    try:
        return _with_meta(ev_supply(SNAPSHOT, gu), "환경공단 충전기 API (seoul_fuel_balance.csv)")
    except KeyError:
        return _error("없는 자치구", gu=gu, fuel="전기")


@mcp.tool()
def score_district(gu: str) -> dict:
    """자치구 기준 판정(전기 병목·경유 유휴 위험)을 반환합니다. 권위 게이트 전용입니다."""
    try:
        return _with_meta(district_baseline(SNAPSHOT, gu), "energy/rules/score.py")
    except KeyError:
        return _error("없는 자치구", gu=gu)


class SystemStatus(BaseModel):
    system_name: str = Field(description="시스템 이름")
    status: str = Field(description="상태: healthy / degraded / down")
    cpu_percent: float = Field(description="CPU 사용률 (%)")
    memory_percent: float = Field(description="메모리 사용률 (%)")
    last_checked: str = Field(description="마지막 체크 시각 (ISO 8601)")
    alerts: list[str] = Field(default_factory=list, description="활성 알림 목록")


@mcp.tool()
def get_monitoring_status(system_name: str) -> SystemStatus:
    """지정된 시스템의 현재 모니터링 상태를 조회합니다 (호출마다 값이 달라지는 동적 Tool)."""
    monitoring_data = json.loads((DATA_DIR / "monitoring.json").read_text())
    if system_name not in monitoring_data:
        return SystemStatus(
            system_name=system_name, status="unknown",
            cpu_percent=0.0, memory_percent=0.0,
            last_checked=datetime.now().isoformat(),
            alerts=[f"System '{system_name}' not found in monitoring"],
        )
    data = monitoring_data[system_name]
    jitter = random.uniform(-3.0, 8.0)
    return SystemStatus(
        system_name=system_name,
        status=data["status"],
        cpu_percent=round(data["cpu_base"] + jitter, 1),
        memory_percent=round(data["memory_base"] + jitter / 2, 1),
        last_checked=datetime.now().isoformat(timespec="microseconds"),
        alerts=data.get("alerts", []),
    )


@mcp.resource("runbook://incident-response/{incident_type}")
def get_incident_runbook(incident_type: str) -> str:
    """특정 incident 유형의 runbook(정적 문서)을 반환합니다."""
    runbook_path = DATA_DIR / "runbooks" / f"{incident_type}.md"
    if not runbook_path.exists():
        return f"# Runbook Not Found\n\nNo runbook for incident type: {incident_type}"
    return runbook_path.read_text()


@mcp.prompt()
def incident_analysis_prompt(system_name: str, symptom: str) -> str:
    """장애 진단 표준 절차 프롬프트 — planning 노드가 계획을 세울 때 이 절차를 따른다."""
    return f"""다음 장애에 대해 표준 진단 절차를 수행하세요.

## 장애 정보
- 시스템: {system_name}
- 증상: {symptom}

## 진단 절차 (반드시 순서대로 수행)
1. get_monitoring_status Tool로 관련 시스템의 현재 상태를 조회하세요.
2. 증상에 해당하는 runbook(runbook://incident-response/...)을 확인하세요.
3. 위 정보를 종합해 가능한 원인과 권장 조치를 제시하세요.
"""


if __name__ == "__main__":
    try:
        mcp.run(show_banner=False)
    except TypeError:   # 구버전 fastmcp 호환
        mcp.run()
