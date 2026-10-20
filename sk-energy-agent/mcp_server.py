"""에너지 허브 MCP 서버 (FastMCP, stdio) — 대시보드 API와 에이전트가 같은 Tool을 쓴다

- Tool     조회(카드·수요·공급·수소·검색·이름·핀·경쟁), 기준 판정·시나리오, 상시 감지, 판단 이력 읽기
- Resource criteria://energy-mix — 판별 기준 임계값·가정값·규칙 버전
- Prompt   district_review_prompt — 자치구 묶음 진단 표준 절차

결정 기록(쓰기)은 여기 없다 — 대시보드 API만 쓸 수 있어 에이전트는 결정을 남길 수 없다.
"""
import json

from fastmcp import FastMCP

from agent.session_store import default_db_path, read_decision_log
from energy.indicators import ev_supply, fuel_demand
from energy.rules.anomaly import detect_anomalies as _detect
from energy.rules.cluster import review_cluster as _review_cluster
from energy.rules.competitors import nearby_operators, weekly_competition
from energy.rules.names import find_stations
from energy.rules.score import district_baseline
from energy.rules.simulate import simulate_fast_add
from energy.store import load_snapshot, load_status_history
from energy.views import (criteria_resource, h2_status, map_pins, nearby_districts as _nearby,
                          search_stations as _search, station_card_view)

mcp = FastMCP(name="sk-energy-hub")

# 스냅샷은 서버 시작 때 1번만 로드 — 요청마다 CSV를 읽으면 응답이 느려진다
SNAPSHOT = load_snapshot()
STATUS_HISTORY = load_status_history()
STATIONS_SOURCE = "오피넷 반경 검색 + 환경공단 충전기 (seoul_stations.csv)"
DEMAND_SOURCE = "서울시 OA-15640 자치구별 연료별 등록현황"
CHARGER_SOURCE = "환경공단 충전기 API (서울 자치구 배정 급속 기준)"


def _with_meta(payload: dict, source: str) -> dict:
    return {**payload, "snapshot": SNAPSHOT.name, "source": source}


def _error(message: str, **detail) -> dict:
    # 예외를 던지지 않고 error 로 돌려줌 — 한 Tool 실패가 전체 답변을 멈추지 않게 (장애 격리)
    return {"error": {"message": message, **detail}, "snapshot": SNAPSHOT.name}


def _known_gu(gu: str) -> bool:
    return gu in SNAPSHOT.balance.index


# ── 조회 Tool (숫자) ─────────────────────────────────────────────

@mcp.tool()
def get_station_card(station_id: str, company: str = "SK에너지",
                     with_verdict: bool = True) -> dict:
    """주유소 번호로 정보 패널 카드를 반환합니다. 자사면 기준 판정·전환 유형·현장 확인 항목 포함, 타사면 공개 정보만."""
    card = station_card_view(SNAPSHOT, station_id, company, with_verdict)
    if card is None:
        return _error("없는 주유소 번호", station_id=station_id)
    return _with_meta(card, STATIONS_SOURCE)


@mcp.tool()
def get_fuel_demand(gu: str) -> dict:
    """자치구의 5개 연료 등록 대수와 1년 증감률(서울 전체 증감률 포함)을 반환합니다."""
    if not _known_gu(gu):
        return _error("없는 자치구", gu=gu, fuel="all")
    return _with_meta(fuel_demand(SNAPSHOT, gu), DEMAND_SOURCE)


@mcp.tool()
def get_ev_supply(gu: str) -> dict:
    """자치구의 급속·완속 충전기 ID 수, 급속 충전 중 비율, 전기차 1만 대당 급속 ID를 반환합니다."""
    if not _known_gu(gu):
        return _error("없는 자치구", gu=gu, fuel="전기")
    return _with_meta(ev_supply(SNAPSHOT, gu), CHARGER_SOURCE)


@mcp.tool()
def get_h2_status(gu: str | None = None) -> dict:
    """서울 수소충전소별 대기 차량을 반환합니다. 좌표가 없어 자치구 단위로만 봅니다."""
    if gu is not None and not _known_gu(gu):
        return _error("없는 자치구", gu=gu, fuel="수소")
    return _with_meta(h2_status(SNAPSHOT, gu), "석유관리원 수소충전소 대기 차량 API")


@mcp.tool()
def search_stations(gu: str | None = None, brand: str | None = None,
                    prodcd: str | None = None) -> dict:
    """자치구·회사·시설 종류(B027 주유소, K015 LPG)로 거점 목록을 찾습니다."""
    stations = _search(SNAPSHOT, gu, brand, prodcd)
    return _with_meta({"stations": stations, "count": len(stations)}, STATIONS_SOURCE)


@mcp.tool()
def resolve_station(query: str, brand: str | None = None) -> dict:
    """주유소 이름으로 후보를 찾습니다. 같은 이름이 2곳 이상이면 ambiguous=true 로 되물어야 합니다."""
    return _with_meta(find_stations(SNAPSHOT.stations, query, brand), STATIONS_SOURCE)


@mcp.tool()
def nearby_districts(gu: str, brand: str = "SK에너지") -> dict:
    """가까운 자치구 순서와 각 구의 회사 거점 수를 반환합니다. 거점이 없는 구를 물었을 때 재계획용입니다."""
    if not _known_gu(gu):
        return _error("없는 자치구", gu=gu)
    return _with_meta({"gu": gu, "brand": brand, "districts": _nearby(SNAPSHOT, gu, brand)},
                      STATIONS_SOURCE)


@mcp.tool()
def get_map_pins(company: str = "SK에너지", brand: str | None = None) -> dict:
    """지도 핀 470개(좌표·회사·시설·자사 여부·⚡ 배지)와 자치구 색칠 값을 반환합니다."""
    return _with_meta(map_pins(SNAPSHOT, company, brand), STATIONS_SOURCE)


# ── 기준 판정·시나리오 Tool (권위 게이트·대시보드용) ──────────────────

@mcp.tool()
def score_district(gu: str) -> dict:
    """자치구 기준 판정(전기 병목·경유/LPG 유휴 위험·전기 유휴·수소 병목)을 반환합니다. 권위 게이트 전용입니다."""
    if not _known_gu(gu):
        return _error("없는 자치구", gu=gu)
    return _with_meta(district_baseline(SNAPSHOT, gu), "energy/rules/score.py")


@mcp.tool()
def review_cluster(gu: str, brand: str = "SK에너지") -> dict:
    """자치구 안 자사 거점 묶음의 거점별 기준 판정(⚡ 급속 후보/유지)과 연료 신호를 반환합니다. 권위 게이트 전용입니다."""
    if not _known_gu(gu):
        return _error("없는 자치구", gu=gu)
    return _with_meta(_review_cluster(SNAPSHOT, gu, brand), "energy/rules/cluster.py")


@mcp.tool()
def simulate(station_id: str, fast_add: int, kw: int = 100) -> dict:
    """거점에 급속 N기를 더했을 때 자치구 순위·병목 전후, 필요 주차면, 시간당 처리 대수를 계산합니다."""
    try:
        return _with_meta(simulate_fast_add(SNAPSHOT, station_id, fast_add, kw),
                          "energy/rules/simulate.py")
    except KeyError:
        return _error("없는 주유소 번호", station_id=station_id)
    except ValueError as exc:
        return _error(str(exc), fast_add=fast_add)


@mcp.tool()
def detect_anomalies(company: str = "SK에너지", gu: str | None = None) -> dict:
    """매일 감시 1차 감지: 자사 거점 반경 1km 급속이 최근 3회 연속 고장이고 24시간 안에 멈춘 것을 거점별로 묶습니다."""
    if gu is not None and not _known_gu(gu):
        return _error("없는 자치구", gu=gu)
    return _with_meta(_detect(STATUS_HISTORY, SNAPSHOT.stations, company, gu),
                      "환경공단 충전기 상태 (매시 수집 fast_status.csv)")


@mcp.tool()
def get_nearby_competition(station_id: str) -> dict:
    """거점 반경 1km 급속충전기를 운영사별로 셉니다 (공개 데이터, 타사 거점도 조회 가능)."""
    result = nearby_operators(SNAPSHOT, station_id)
    if result is None:
        return _error("없는 주유소 번호", station_id=station_id)
    return _with_meta(result, CHARGER_SOURCE)


@mcp.tool()
def diff_competitors(company: str = "SK에너지") -> dict:
    """비교 구간 처음과 끝 수집 사이 자사 거점 반경 1km 급속 신설·철거를 운영사·거리와 함께 반환합니다."""
    return _with_meta(weekly_competition(STATUS_HISTORY, SNAPSHOT.stations, company),
                      "환경공단 충전기 정보 (수집 시점 비교)")


@mcp.tool()
def get_decision_log(company: str = "SK에너지", station_id: str | None = None,
                     gu: str | None = None) -> dict:
    """사람이 입력한 결정과 게이트 판별 기록을 읽습니다 (읽기 전용 — 결정 기록은 대시보드 API만)."""
    if not station_id and not gu:
        return _error("station_id 또는 gu 가 필요함")
    log = read_decision_log(default_db_path(), company, station_id=station_id, gu=gu)
    return {**log, "company": company, "station_id": station_id, "gu": gu,
            "source": "판단 이력 (SQLite)"}


# ── Resource · Prompt ──────────────────────────────────────────

@mcp.resource("criteria://energy-mix")
def energy_mix_criteria() -> str:
    """판별 기준 임계값·가정값·규칙 버전 (Supervisor·검증 에이전트가 읽는 기준표)"""
    return json.dumps(_with_meta(criteria_resource(SNAPSHOT), "energy/criteria.py"),
                       ensure_ascii=False)


@mcp.prompt()
def district_review_prompt(gu: str, brand: str = "SK에너지") -> str:
    """자치구 묶음 진단 표준 절차 — 수요 → 공급 → 신호 → 묶음 역할 → 현장 확인 순서를 강제한다."""
    return f"""{gu} {brand} 거점 묶음을 아래 순서로 진단하세요.

1. 수요: 5개 연료 1년 증감 (get_fuel_demand)
2. 공급: 급속 밀도·충전 중 비율, 수소 대기 (get_ev_supply, get_h2_status)
3. 신호: 사실 번호 [F]를 인용해 전기 병목·경유/LPG 유휴 위험을 판별
4. 묶음 역할: 거점별 반경 1km 급속 수를 비교해 급속 후보와 우선순위·이유를 판별
5. 현장 확인: 부지·폭발위험장소·전력 인입·주차면은 데이터가 없으므로 사람에게 넘김

숫자는 Tool 결과만 쓰고, 투자 결정이 아니라 진단임을 밝히세요.
"""


if __name__ == "__main__":
    try:
        mcp.run(show_banner=False)
    except TypeError:   # 구버전 fastmcp 호환
        mcp.run()
