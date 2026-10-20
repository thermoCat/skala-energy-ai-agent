"""Tool 응답을 번호 붙은 사실 [F1..Fn] 목록으로 바꾸는 함수 (분석·작성·검증 에이전트 공통 입력)"""
from statistics import median


class FactList:
    """번호를 차례로 붙이며 사실을 모으는 목록"""

    def __init__(self):
        self.items: list[dict] = []

    def add(self, key: str, label: str, value, unit: str, source: str, **extra) -> None:
        self.items.append({"id": f"F{len(self.items) + 1}", "key": key, "label": label,
                           "value": value, "unit": unit, "source": source, **extra})


def _ok(result: dict | None) -> bool:
    return bool(result) and "error" not in result


def _add_district(facts: FactList, demand: dict, supply: dict, h2: dict) -> None:
    if _ok(demand):
        gu, months, n = demand["gu"], "→".join(demand["months"]), demand["gu_count"]
        src = demand["source"]
        ev = demand["fuels"]["전기"]
        facts.add("ev_growth_pct", f"{gu} 전기차 1년 증감률 ({months})", ev["rate_pct"], "%", src)
        facts.add("seoul_ev_growth_pct", f"서울 전기차 1년 증감률 ({months})",
                  ev["seoul_rate_pct"], "%", src)
        for fuel, key in (("경유", "diesel"), ("LPG", "lpg")):
            f = demand["fuels"][fuel]
            facts.add(f"{key}_rate_pct", f"{gu} {fuel}차 1년 증감률 ({months})",
                      f["rate_pct"], "%", src)
            facts.add(f"seoul_{key}_rate_pct", f"서울 {fuel}차 1년 증감률 ({months})",
                      f["seoul_rate_pct"], "%", src)
            facts.add(f"{key}_decline_rank", f"{gu} {fuel} 감소율 순위 ({n}구 중 많이 줄어든 순)",
                      f["decline_rank"], "위", src, gu_count=n)
    if _ok(supply):
        gu, src = supply["gu"], supply["source"]
        facts.add("fast_per_10k_ev", f"{gu} 전기차 1만 대당 급속 ID", supply["fast_per_10k_ev"],
                  "ID", src)
        facts.add("seoul_fast_per_10k_ev", "서울 전기차 1만 대당 급속 ID",
                  supply["seoul_fast_per_10k_ev"], "ID", src)
        if supply["fast_busy_pct"] is not None:
            facts.add("fast_busy_pct", f"{gu} 급속 충전 중 비율 (한 시점)",
                      supply["fast_busy_pct"], "%", src)
    if _ok(h2):
        facts.add("h2_stations", f"{h2['gu']} 수소충전소", h2["station_count"], "곳", h2["source"])
        facts.add("h2_wait", f"{h2['gu']} 수소 대기 차량 (한 시점)", h2["wait_total"], "대",
                  h2["source"])


def _add_cluster(facts: FactList, cards: list[dict], company: str) -> None:
    cards = [c for c in cards if _ok(c)]
    if not cards:
        return
    src = cards[0]["source"]
    facts.add("own_count", f"{cards[0]['gu']} {company} 거점 수", len(cards), "곳", src)
    facts.add("cluster_median_fast_1km", "묶음 반경 1km 급속 ID 중앙값",
              median(c["fast_1km"] for c in cards), "ID", src)
    for c in sorted(cards, key=lambda c: c["station_id"]):
        tag = {"station_id": c["station_id"], "prodcd": c["prodcd"], "name": c["name"]}
        facts.add(f"fast_1km:{c['station_id']}", f"{c['name']} ({c['facility']}) 반경 1km 급속 ID",
                  c["fast_1km"], "ID", src, **tag)
        facts.add(f"in_station_fast:{c['station_id']}",
                  f"{c['name']} ({c['facility']}) 주유소 안 급속 ID", c["in_station_fast_ids"],
                  "개", src, **tag)


def _add_scenario(facts: FactList, sim: dict) -> None:
    src, before, after = sim["source"], sim["before"], sim["after"]
    facts.add("fast_add", "추가 가정 급속충전기", sim["fast_add"], "기", src)
    facts.add("kw", "추가 가정 출력", sim["kw"], "kW", src)
    for label, key, unit in (("급속 ID", "fast_ids", "ID"),
                             ("전기차 1만 대당 급속 ID", "fast_per_10k_ev", "ID"),
                             ("급속 밀도 순위 (낮은 순)", "rank_density", "위"),
                             ("병목 점수 순위", "score_rank", "위")):
        facts.add(f"{key}_before", f"{sim['gu']} {label} (현재)", before[key], unit, src)
        facts.add(f"{key}_after", f"{sim['gu']} {label} (추가 후)", after[key], unit, src)
    facts.add("parking_spaces", "필요 주차면", sim["parking_spaces"], "개", src)
    facts.add("occupancy_min", "급속 1회 충전 시간 중앙값 (가정)", sim["occupancy_min"], "분",
              sim["assumptions"]["occupancy"])
    facts.add("cars_per_hour", "시간당 처리 대수 (가정)", sim["cars_per_hour"], "대", src)


def build_facts(results: dict, company: str) -> list[dict]:
    """조회 결과를 번호 붙은 사실 목록으로 만드는 함수 (실패한 Tool의 사실은 빠짐)

    Args:
        results: card·demand·supply·h2·cluster_cards·simulate 조회 결과
        company: 로그인 회사
    Returns:
        {"id", "key", "label", "value", "unit", "source", ...} 목록
    """
    facts = FactList()
    card = results.get("card")
    if _ok(card):
        # 상호에 회사명이 이미 들어 있으면("SK에너지(주) 박미주유소") 한 번만 쓴다
        title = card["name"] if card["name"].startswith(card["brand"]) else \
            f"{card['brand']} {card['name']}"
        facts.add("station", "거점", f"{card['gu']} · {title} ({card['facility']})",
                  "", card["source"], station_id=card["station_id"])
        facts.add("in_station_fast_ids", "이 거점 주유소 안 급속 ID (반경 150m)",
                  card["in_station_fast_ids"], "개", card["source"])
        facts.add("fast_1km", "이 거점 반경 1km 급속 ID", card["fast_1km"], "ID", card["source"])
    _add_district(facts, results.get("demand"), results.get("supply"), results.get("h2"))
    if card is None or (_ok(card) and card["is_own"]):
        _add_cluster(facts, results.get("cluster_cards", []), company)
    if _ok(results.get("simulate")):
        _add_scenario(facts, results["simulate"])
    return facts.items
