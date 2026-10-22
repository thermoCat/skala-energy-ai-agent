"""Tool 응답을 번호 붙은 사실 [F1..Fn] 목록으로 바꾸는 함수 (분석·작성·검증 에이전트 공통 입력)"""
from statistics import median

from energy.indicators import ev_per_fast


class FactList:
    """번호를 차례로 붙이며 사실을 모으는 목록"""

    def __init__(self, start: int = 0):
        self.start = start            # 이미 있는 사실 뒤에 이어 붙일 때 번호가 겹치지 않게
        self.items: list[dict] = []

    def add(self, key: str, label: str, value, unit: str, source: str, **extra) -> None:
        self.items.append({"id": f"F{self.start + len(self.items) + 1}", "key": key,
                           "label": label, "value": value, "unit": unit, "source": source,
                           **extra})


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
        facts.add("ev_growth_rank", f"{gu} 전기차 증가율 순위 ({n}구 중 1위 = 가장 빠르게 증가)",
                  ev["growth_rank"], "위", src, gu_count=n)
        for fuel, key in (("경유", "diesel"), ("LPG", "lpg")):
            f = demand["fuels"][fuel]
            facts.add(f"{key}_rate_pct", f"{gu} {fuel}차 1년 증감률 ({months})",
                      f["rate_pct"], "%", src)
            facts.add(f"seoul_{key}_rate_pct", f"서울 {fuel}차 1년 증감률 ({months})",
                      f["seoul_rate_pct"], "%", src)
            facts.add(f"{key}_decline_rank", f"{gu} {fuel} 감소율 순위 ({n}구 중 1위 = 가장 많이 감소)",
                      f["decline_rank"], "위", src, gu_count=n)
    if _ok(supply):
        gu, src = supply["gu"], supply["source"]
        facts.add("fast_per_10k_ev", f"{gu} 전기차 1만 대당 급속 ID", supply["fast_per_10k_ev"],
                  "ID", src)
        facts.add("seoul_fast_per_10k_ev", "서울 전기차 1만 대당 급속 ID",
                  supply["seoul_fast_per_10k_ev"], "ID", src)
        facts.add("fast_density_rank", f"{gu} 전기차 1만 대당 급속 순위 (25구 중 1위 = 가장 부족)",
                  supply["density_rank"], "위", src)
        # 화면에 보여주는 말 — "1만 대당 급속"보다 "급속 1개를 몇 대가 나눠 쓰나"가 바로 읽힌다
        facts.add("ev_per_fast", f"{gu} 급속 1개당 전기차", ev_per_fast(supply["fast_per_10k_ev"]),
                  "대", src)
        facts.add("seoul_ev_per_fast", "서울 급속 1개당 전기차",
                  ev_per_fast(supply["seoul_fast_per_10k_ev"]), "대", src)
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
        if c.get("fast_1km_busy_pct") is not None:
            facts.add(f"busy_1km:{c['station_id']}",
                      f"{c['name']} 반경 1km 급속 충전 중 비율 (한 시점, 대리 지표)",
                      c["fast_1km_busy_pct"], "%", src, **tag)


def _add_scenario(facts: FactList, sim: dict) -> None:
    src, before, after = sim["source"], sim["before"], sim["after"]
    facts.add("fast_add", "신설 가정 급속충전기", sim["fast_add"], "대", src)
    facts.add("kw", "추가 가정 출력", sim["kw"], "kW", src)
    for label, key, unit in (("급속 ID", "fast_ids", "ID"),
                             ("전기차 1만 대당 급속 ID", "fast_per_10k_ev", "ID"),
                             ("급속 밀도 순위 (낮은 순)", "rank_density", "위"),
                             ("병목 점수 순위", "score_rank", "위")):
        facts.add(f"{key}_before", f"{sim['gu']} {label} (현재)", before[key], unit, src)
        facts.add(f"{key}_after", f"{sim['gu']} {label} (추가 후)", after[key], unit, src)
    facts.add("ev_per_fast_before", f"{sim['gu']} 급속 1개당 전기차 (현재)",
              ev_per_fast(before["fast_per_10k_ev"]), "대", src)
    facts.add("ev_per_fast_after", f"{sim['gu']} 급속 1개당 전기차 (추가 후)",
              ev_per_fast(after["fast_per_10k_ev"]), "대", src)
    facts.add("parking_spaces", "필요 주차면", sim["parking_spaces"], "개", src)
    facts.add("occupancy_min", "급속 1회 충전 시간 중앙값 (가정)", sim["occupancy_min"], "분",
              sim["assumptions"]["occupancy"])
    facts.add("cars_per_hour", "시간당 처리 대수 (가정)", sim["cars_per_hour"], "대", src)


def _add_decisions(facts: FactList, log: dict, names: dict) -> None:
    latest: dict = {}
    for d in log.get("human", []):            # 최신순이라 거점마다 첫 행이 최근 결정
        latest.setdefault(d["station_id"], d)
    for sid, d in sorted(latest.items()):
        kind = f" · {d['conversion_type']}" if d.get("conversion_type") else ""
        facts.add(f"decision:{sid}", f"{names.get(sid, sid)} 지난 사람 결정",
                  f"{d['created_at'][:10]} {d['choice']}{kind} — {d['note']} ({d['decided_by']})",
                  "", log["source"], station_id=sid, name=names.get(sid, sid))


def _add_competition(facts: FactList, comp: dict) -> None:
    src = comp["source"]
    facts.add("nearby_operators", "반경 1km 급속 운영사 수", comp["operators"], "곳", src)
    for i, row in enumerate(comp["top"], start=1):
        facts.add(f"operator:{i}", f"반경 1km 급속 운영사 {row['operator']}", row["fast_ids"], "ID",
                  src, name=row["operator"])


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
    if _ok(results.get("competition")):
        _add_competition(facts, results["competition"])
    if _ok(results.get("decisions")):
        names = {c["station_id"]: c["name"] for c in results.get("cluster_cards", []) if _ok(c)}
        _add_decisions(facts, results["decisions"], names)
    return facts.items


def build_watch_facts(summary: dict, top: int) -> list[dict]:
    """일간 추적 요약을 번호 붙은 사실 목록으로 만드는 함수

    Args:
        summary: detect_anomalies 결과
        top: 알림 위에 올릴 거점 수
    Returns:
        사실 목록 (감시 범위·구간·규칙, 새 고장 수, 거점별 새 고장 수)
    """
    facts = FactList()
    src = summary["source"]
    scope = f"{summary['company']} · {summary['gu'] or '서울 전체'}"
    facts.add("watch_scope", "감시 범위", scope, "", src)
    facts.add("watch_window", "감시 구간 (수집 시각)",
              " → ".join(w[:16].replace("T", " ") for w in summary["window"]), "", src)
    facts.add("watch_rule", "새로 멈춤 기준", "직전 수집에서 정상이던 급속이 그 뒤 수집 내내 "
              "통신이상·운영중지·점검중", "", src)
    facts.add("own_stations", "감시한 자사 거점", summary["own_stations"], "곳", src)
    facts.add("new_failures", "자사 거점 반경 1km 새로 멈춘 급속", summary["new_failures"], "개", src)
    for reason, n in summary.get("reasons", {}).items():
        facts.add(f"reason:{reason}", f"새로 멈춘 급속 중 {reason}", n, "개", src, reason=reason)
    facts.add("stations_affected", "새로 멈춘 급속이 주변에 있는 자사 거점", len(summary["stations"]),
              "곳", src)
    for s in summary["stations"]:
        facts.add(f"new_failures:{s['station_id']}", f"{s['name']} ({s['gu']}) 반경 1km 새로 멈춘 급속",
                  s["new_failures"], "개", src, station_id=s["station_id"], name=s["name"], gu=s["gu"])
        for reason, n in s.get("reasons", {}).items():
            facts.add(f"reason:{s['station_id']}:{reason}", f"{s['name']} 주변 {reason}", n, "개", src,
                      station_id=s["station_id"], reason=reason)
    rest = len(summary["stations"]) - top
    if rest > 0:
        facts.add("stations_rest", "알림 위에 올리지 않은 나머지 거점", rest, "곳", src)
    return facts.items


def build_competition_facts(summary: dict) -> list[dict]:
    """주유소 주간 추적 요약을 번호 붙은 사실 목록으로 만드는 함수

    Args:
        summary: diff_competitors 결과
    Returns:
        사실 목록 (범위·구간, 서울 신설·철거 수, 자사 거점 1km 안 변화별 운영사·거리)
    """
    facts = FactList()
    src = summary["source"]
    facts.add("watch_scope", "추적 범위", f"{summary['company']} · 서울 전체", "", src)
    facts.add("watch_window", "비교 구간 (수집 시각)",
              " → ".join(w[:16].replace("T", " ") for w in summary["window"]), "", src)
    facts.add("seoul_added", "서울 급속 신설", summary["seoul_added"], "개", src)
    facts.add("seoul_removed", "서울 급속 철거", summary["seoul_removed"], "개", src)
    facts.add("near_changes", "자사 거점 반경 1km 안 변화", len(summary["changes"]), "건", src)
    facts.add("competitor_new", "그중 타사 신설", summary["competitor_new"], "건", src)
    facts.add("competitor_new_public", "타사 신설 중 일반 이용 (버스 전용·이용 제한 제외)",
              summary.get("competitor_new_public", 0), "건", src)
    # 같은 충전소에 같은 날 여러 기가 생기면(버스 차고지 8기 등) 한 줄로 묶고 대수를 따로 센다
    groups: dict[tuple, list[dict]] = {}
    for c in summary["changes"]:
        key = (c["station_id"], c["change"], c["operator"], c.get("place"), c["distance_m"])
        groups.setdefault(key, []).append(c)
    for members in groups.values():
        c, first = members[0], members[0]["charger"]
        who = "자사·계열" if c["own_operator"] else "타사"
        use = c.get("use", "이용 정보 없음")
        facts.add(f"change:{first}", f"{c['name']} ({c['gu']}) 주변 변화",
                  f"{c['change']} · {c['operator']}({who}) · {use}", "", src,
                  station_id=c["station_id"], name=c["name"], gu=c["gu"], use=use,
                  competitor=not c["own_operator"] and c["change"] == "신설")
        facts.add(f"count:{first}", f"{c['name']} 주변 같은 충전소 변화 대수", len(members), "대", src,
                  station_id=c["station_id"], charger=first)
        facts.add(f"distance:{first}", f"{c['name']} 에서 거리", c["distance_m"], "m", src,
                  station_id=c["station_id"], name=c["name"], charger=first)
    return facts.items


def build_priority_facts(start: int, candidates: list[str], names: dict, competition: dict,
                         watch: dict, weekly: dict) -> list[dict]:
    """충전기 후보마다 우선순위 판단에 쓸 신호(주변 운영사·새 고장·타사 신설)를 사실로 만드는 함수

    Args:
        start: 이미 있는 사실 수 (번호를 이어 붙임)
        candidates: 확정된 충전기 후보 주유소 번호
        names: 주유소 번호 → 이름
        competition: 주유소 번호 → get_nearby_competition 결과
        watch: detect_anomalies 결과 (자치구 범위)
        weekly: diff_competitors 결과
    Returns:
        새 사실 목록 (조회에 실패한 신호는 빠짐)
    """
    facts = FactList(start)
    failures = {s["station_id"]: s["new_failures"] for s in watch.get("stations", [])} \
        if _ok(watch) else None
    rivals = {}
    if _ok(weekly):
        for c in weekly["changes"]:
            if c["change"] == "신설" and not c["own_operator"]:
                rivals[c["station_id"]] = rivals.get(c["station_id"], 0) + 1
    for sid in candidates:
        tag = {"station_id": sid, "name": names.get(sid, sid)}
        name = names.get(sid, sid)
        comp = competition.get(sid)
        if _ok(comp):
            facts.add(f"operators_1km:{sid}", f"{name} 반경 1km 급속 운영사 수", comp["operators"],
                      "곳", comp["source"], **tag)
        if failures is not None:
            facts.add(f"new_failures_near:{sid}",
                      f"{name} 반경 1km 새로 멈춘 급속 (최근 3회 연속 고장 + 24시간 이내)",
                      failures.get(sid, 0), "개", watch["source"], **tag)
        if _ok(weekly):
            facts.add(f"competitor_new_near:{sid}", f"{name} 반경 1km 타사 급속 신설 (비교 구간)",
                      rivals.get(sid, 0), "건", weekly["source"], **tag)
    return facts.items
