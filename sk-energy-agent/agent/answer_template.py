"""답변 뼈대 — 숫자와 근거 번호는 코드가 배치하고, 문장은 작성 에이전트가 다듬는다"""

TOOL_FUELS = {"get_fuel_demand": "5개 연료 수요", "get_ev_supply": "전기 공급",
              "get_h2_status": "수소", "search_stations": "자사 거점 목록"}
YES_NO = {True: "예", False: "아니오"}
ORDINALS = ("첫 번째", "두 번째", "세 번째", "네 번째")   # 숫자로 쓰면 근거 없는 숫자로 잡힘


class EnergyAnswer:
    """사실·확정 판정으로 답변 뼈대를 만드는 도우미 (숫자는 사실 값 그대로, [F]·[J] 번호 포함)

    fake 작성 에이전트는 이 뼈대를 그대로 답으로 쓰고, 실제 LLM 작성 에이전트는
    이 뼈대의 숫자·번호를 지킨 채 문장만 다듬는다.
    """

    def __init__(self, facts: list[dict], verdicts: dict):
        self.by_key = {f["key"]: f for f in facts}
        self.verdicts = verdicts

    def cite(self, key: str, fmt: str = "{}") -> str:
        if key not in self.by_key:
            return "데이터 없음"
        return fmt.format(self.by_key[key]["value"]) + f"[{self.by_key[key]['id']}]"

    def j(self, item: str) -> str:
        if item not in self.verdicts:
            return ""
        # "[J2·교정]" 처럼 붙이면 번호의 숫자가 근거 없는 숫자로 잡혀서 괄호를 밖에 둔다
        mark = "(교정됨)" if self.verdicts[item].get("corrected") else ""
        return f"[{self.verdicts[item]['id']}]{mark}"

    def value(self, item: str):
        return self.verdicts.get(item, {}).get("value")

    def name(self, station_id: str) -> str:
        return self.by_key.get(f"fast_1km:{station_id}", {}).get("name", station_id)

    def demand_supply(self) -> list[str]:
        c = self.cite
        h2 = (f"수소충전소 {c('h2_stations', '{}곳')}, 대기 {c('h2_wait', '{}대')}"
              if "h2_stations" in self.by_key else "수소: 데이터 없음")
        return [
            f"1) 차량 1년 변화: 전기차 {c('ev_growth_pct', '{:+.1f}%')} "
            f"(서울 {c('seoul_ev_growth_pct', '{:+.1f}%')}) · "
            f"경유차 {c('diesel_rate_pct', '{:+.1f}%')} (서울 {c('seoul_diesel_rate_pct', '{:+.1f}%')}) · "
            f"LPG차 {c('lpg_rate_pct', '{:+.1f}%')} (서울 {c('seoul_lpg_rate_pct', '{:+.1f}%')})",
            f"2) 공급: 급속 1개당 전기차 {c('ev_per_fast', '{}대')} "
            f"(서울 {c('seoul_ev_per_fast', '{}대')}) · {h2}",
        ]

    def district_verdicts(self) -> str:
        ev = {True: "충전기 부족 구", False: "충전기 부족 아님"}
        idle = {True: "🔻 감소 큰 구", False: "감소 신호 아님"}
        line = (f"3) 판정: {ev.get(self.value('ev_bottleneck'), '충전기 부족 판별 불가')}{self.j('ev_bottleneck')}"
                f" · 경유차 {idle.get(self.value('diesel_idle'), '판별 불가')}{self.j('diesel_idle')}"
                f" · LPG차 {idle.get(self.value('lpg_idle'), '판별 불가')}{self.j('lpg_idle')}")
        if self.value("diesel_idle") or self.value("lpg_idle"):
            # 판매량이 없어 같은 구 거점끼리 구별할 근거가 없다
            line += " (구 단위 신호)"
        return line

    def priority(self) -> dict:
        return self.verdicts.get("fast_candidates", {}).get("priority") or {}

    def versus_typical(self) -> str:
        # "묶음 중앙값" 대신 같은 구 자사 주유소의 보통 수와 비교해 적은지 많은지를 바로 쓴다
        if "fast_1km" not in self.by_key or "cluster_median_fast_1km" not in self.by_key:
            return ""
        mine, typical = self.by_key["fast_1km"]["value"], self.by_key["cluster_median_fast_1km"]["value"]
        rel = "보다 적음" if mine < typical else "와 같음" if mine == typical else "보다 많음"
        return f" — 같은 구 자사 주유소 보통({self.cite('cluster_median_fast_1km', '{}개')}){rel}"

    def station_lines(self, station_id: str) -> list[str]:
        c = self.cite
        chosen = self.value("fast_candidates") or []
        candidate = station_id in chosen
        verdict = "⚡ 급속 후보입니다" if candidate else "이번 기준으로는 급속 후보가 아닙니다"
        lines = [f"4) 이 주유소: 주유소 안 급속 {c('in_station_fast_ids', '{}개')} · "
                 f"1km 안 급속 {c('fast_1km', '{}개')}{self.versus_typical()} "
                 f"→ {verdict}{self.j('fast_candidates')}"]
        pri = self.priority()
        if candidate and pri.get("by") == "llm":
            ordinal = ORDINALS[chosen.index(station_id)]
            lines.append(f"   같은 구 후보 {len(chosen)}곳 중 {ordinal}로 볼 곳 (AI 판단: "
                         f"{pri['reasons'][station_id]['text']})")
        return lines

    def cluster_lines(self) -> list[str]:
        chosen = self.value("fast_candidates") or []
        if not chosen:
            return [f"4) 급속 후보{self.j('fast_candidates')}: 없음 (충전기 부족 구가 아님)"]
        marks = "①②③④"
        pri = self.priority()
        if pri.get("by") == "llm":
            picks = [f"{marks[i]} {self.name(sid)} — {pri['reasons'][sid]['text']}"
                     for i, sid in enumerate(chosen)]
            head = f"4) 급속 후보{self.j('fast_candidates')} — 먼저 볼 순서 (AI 판단): "
        else:
            picks = [f"{marks[i]} {self.name(sid)} — 1km 안 급속 {self.cite(f'fast_1km:{sid}', '{}개')}, "
                     f"주유소 안 급속 {self.cite(f'in_station_fast:{sid}', '{}개')}"
                     for i, sid in enumerate(chosen)]
            head = f"4) 급속 후보{self.j('fast_candidates')}: "
            if pri.get("by") == "fallback":
                head = (f"4) 급속 후보{self.j('fast_candidates')} — AI 우선순위 판단이 검사를 "
                        "통과하지 못해 1km 안 급속이 적은 순: ")
        return [head + "  ".join(picks),
                f"   나머지 주유소는 유지 (같은 구 자사 주유소 보통 1km 안 급속 "
                f"{self.cite('cluster_median_fast_1km', '{}개')})"]

    def scenario_lines(self) -> list[str]:
        c = self.cite
        change = {(True, True): "여전히 충전기 부족 구입니다",
                  (True, False): "충전기 부족 구에서 벗어납니다",
                  (False, False): "추가 전후 모두 충전기 부족 구가 아닙니다",
                  (False, True): "추가 후 충전기 부족 구가 됩니다"}
        before_after = (self.value("ev_bottleneck_before"), self.value("ev_bottleneck_after"))
        return [
            f"충전기 {c('fast_add', '{}대')} ({c('kw', '{}kW')} 급속) 신설 시나리오입니다.",
            f"1) 자치구 급속 {c('fast_ids_before', '{}개')} → {c('fast_ids_after', '{}개')}, "
            f"급속 1개당 전기차 {c('ev_per_fast_before', '{}대')} → "
            f"{c('ev_per_fast_after', '{}대')}",
            f"2) 급속 밀도 순위 {c('rank_density_before', '{}위')} → {c('rank_density_after', '{}위')}, "
            f"충전기 부족 순위 {c('score_rank_before', '{}위')} → {c('score_rank_after', '{}위')} → "
            f"{change.get(before_after, '판별 불가')}{self.j('ev_bottleneck_before')}"
            f"{self.j('ev_bottleneck_after')}",
            f"3) 필요 주차면 {c('parking_spaces', '{}개')}, 시간당 처리 약 {c('cars_per_hour', '{}대')} "
            f"(급속 1회 충전 시간 중앙값 {c('occupancy_min', '{}분')} 가정)",
        ]

    def decision_lines(self) -> list[str]:
        decisions = [f for k, f in self.by_key.items() if k.startswith("decision:")]
        station_id = self.by_key.get("station", {}).get("station_id")
        if station_id:   # 주유소 질문이면 그 주유소의 결정만
            decisions = [f for f in decisions if f["station_id"] == station_id]
        return [f"※ 지난 결정 — {f['name']}: {f['value']}[{f['id']}] (이번 판정과 함께 검토하세요)"
                for f in decisions]

    def competition_line(self, number: str = "") -> list[str]:
        ops = sorted((f for k, f in self.by_key.items() if k.startswith("operator:")),
                     key=lambda f: f["key"])
        if not ops:
            return []
        listed = " · ".join(f"{f['name']} {f['value']}[{f['id']}]" for f in ops)
        return [f"{number}주변 경쟁: 1km 안 급속 운영사 {self.cite('nearby_operators', '{}곳')} "
                f"— 많은 순 {listed}"]

    def weekly_lines(self) -> list[str]:
        c = self.cite
        lines = [f"서울 급속 신설 {c('seoul_added', '{}개')} · 철거 {c('seoul_removed', '{}개')} 중 "
                 f"자사 거점 반경 1km 안 변화 {c('near_changes', '{}건')} "
                 f"(타사 신설 {c('competitor_new', '{}건')}, 그중 일반 이용 "
                 f"{c('competitor_new_public', '{}건')}){self.j('report_needed')}."]
        if not self.value("report_needed"):
            return lines
        marks = "①②③④⑤"
        picks = []
        for i, sid in enumerate(self.value("watch_competitors") or []):
            for k, f in self.by_key.items():
                if k.startswith("change:") and f["station_id"] == sid:
                    charger = k.split(":", 1)[1]
                    picks.append(f"{marks[len(picks) % 5]} {f['name']}({f['gu']}) — "
                                 f"{f['value']}[{f['id']}] {c(f'count:{charger}', '{}대')}, "
                                 f"{c(f'distance:{charger}', '{}m')}")
        lines.append(f"먼저 볼 거점{self.j('watch_competitors')}: " + "  ".join(picks))
        lines.append("주변에 생긴 타사 급속은 같은 충전 수요를 나눠 가질 수 있어, 해당 거점의 급속 "
                     "후보 판단과 함께 보세요.")
        return lines

    def reasons(self, station_id: str | None = None) -> str:
        # "새 고장 N개" 대신 멈춘 이유별로 — 통신이상이 몰리면 운영사 장애, 점검중이면 곧 복구일 수 있다
        prefix = f"reason:{station_id}:" if station_id else "reason:"
        items = [f for k, f in self.by_key.items()
                 if k.startswith(prefix) and (station_id or k.count(":") == 1)]
        return ", ".join(f"{f['reason']} {f['value']}개[{f['id']}]" for f in items) or "이유 데이터 없음"

    def watch_lines(self) -> list[str]:
        c = self.cite
        if not self.value("alert_needed"):
            return [f"자사 거점 주변에 새로 멈춘 급속충전기가 없습니다{self.j('alert_needed')}."]
        marks = "①②③④⑤"
        top = self.value("alert_stations") or []
        picks = [f"{marks[i]} {self.by_key[f'new_failures:{sid}']['name']}"
                 f"({self.by_key[f'new_failures:{sid}']['gu']}) — {self.reasons(sid)}"
                 for i, sid in enumerate(top)]
        lines = [f"자사 주유소 {c('stations_affected', '{}곳')} 주변 1km에서 급속충전기가 새로 "
                 f"멈췄습니다{self.j('alert_needed')} — {self.reasons()} "
                 f"(기준: {c('watch_rule')}).",
                 f"먼저 볼 거점{self.j('alert_stations')}: " + "  ".join(picks)]
        if "stations_rest" in self.by_key:
            lines.append(f"나머지 {c('stations_rest', '{}곳')}은 알림을 눌러 목록에서 확인하세요.")
        lines.append("주변 충전기가 멈추면 자사 거점의 충전 수요·고객 안내에 영향이 있어 "
                     "현장 확인이 필요합니다.")
        return lines

    def correction_lines(self, corrections: list[dict], item_labels: dict) -> list[str]:
        lines = []
        for fix in corrections:
            label = item_labels.get(fix["item"], fix["item"])
            if isinstance(fix["baseline"], list):
                before = ", ".join(self.name(s) for s in fix["llm"] or []) or "없음"
                after = ", ".join(self.name(s) for s in fix["baseline"]) or "없음"
            else:
                before = YES_NO.get(fix["llm"], "판별 없음")
                after = YES_NO.get(fix["baseline"], "판별 없음")
            lines.append(f"※ 게이트 교정: {label} — AI '{before}' → 기준 '{after}'")
        return lines

    def summary_line(self, request_type: str, is_own: bool) -> str:
        """답변 맨 위 요약 한 줄을 만드는 함수 — 화면은 기본으로 제목과 이 줄만 보여준다

        Args:
            request_type: 요청 종류
            is_own: 자사 거점 여부
        Returns:
            "요약: …" 한 줄 (숫자는 사실 값, 근거 번호 포함)
        """
        c = self.cite
        if not is_own:
            ops = (f" (주변 급속 운영사 {c('nearby_operators', '{}곳')})"
                   if "nearby_operators" in self.by_key else "")
            return f"요약: 타사 거점 — 공개 정보만{ops}"
        if request_type == "scenario":
            change = {(True, True): "여전히 충전기 부족", (True, False): "충전기 부족에서 벗어남",
                      (False, False): "전후 모두 충전기 부족 아님", (False, True): "충전기 부족이 됨"}
            before_after = (self.value("ev_bottleneck_before"), self.value("ev_bottleneck_after"))
            return (f"요약: 충전기 {c('fast_add', '{}대')} 신설 → {change.get(before_after, '판별 불가')}, "
                    f"시간당 약 {c('cars_per_hour', '{}대')}")
        ev = {True: "충전기 부족 구", False: "충전기 부족 아님"}.get(self.value("ev_bottleneck"),
                                                              "충전기 부족 판별 불가")
        idle = [f"{fuel} 감소 큰 구" for item, fuel in (("diesel_idle", "경유차"), ("lpg_idle", "LPG차"))
                if self.value(item)]
        chosen = self.value("fast_candidates") or []
        if request_type == "station":
            station_id = self.by_key["station"]["station_id"]
            head = "급속 후보 아님"
            if station_id in chosen:
                head = "⚡ 급속 후보"
                if len(chosen) > 1:
                    head += f" — 같은 구 후보 {len(chosen)}곳 중 {ORDINALS[chosen.index(station_id)]}"
            parts = [head, ev]
        else:
            first = f"급속 후보 {len(chosen)}곳 — 먼저 {self.name(chosen[0])}" if chosen else "급속 후보 없음"
            parts = [ev, first]
        return "요약: " + " · ".join(parts + idle)

    def build(self, request_type, corrections, is_own, errors, site_checks, item_labels,
              replanned_from) -> list[str]:
        station = self.by_key.get("station", {}).get("value")
        if request_type in ("watch", "weekly"):
            title = "일간 추적" if request_type == "watch" else "주유소 주간 추적"
            body = self.watch_lines() if request_type == "watch" else self.weekly_lines()
            lines = [f"[{self.by_key['watch_scope']['value']} {title} · "
                     f"{self.by_key['watch_window']['value']}]"]
            return lines + body + self.correction_lines(corrections, item_labels)
        if request_type == "district":
            own = self.by_key.get("own_count", {})
            header = f"[{own.get('label', '').replace(' 거점 수', '')} 거점 {own.get('value', 0)}곳]"
        else:
            header = f"[{station}]"
        lines = [header, self.summary_line(request_type, is_own)]
        if replanned_from:
            lines.append(f"※ {replanned_from}에는 자사 거점이 없어 가장 가까운 자치구로 대신 진단합니다.")

        if not is_own:
            c = self.cite
            lines += ["공개 정보입니다.",
                      f"- 주유소 안 급속충전기 {c('in_station_fast_ids', '{}개')}, "
                      f"1km 안 급속 {c('fast_1km', '{}개')}",
                      f"- 자치구 전기차 1년 {c('ev_growth_pct', '{:+.1f}%')} "
                      f"(서울 {c('seoul_ev_growth_pct', '{:+.1f}%')}), 급속 1개당 전기차 "
                      f"{c('ev_per_fast', '{}대')} (서울 {c('seoul_ev_per_fast', '{}대')})",
                      *self.competition_line("- "),
                      "타사 거점이라 투자 판별은 하지 않습니다."]
            return lines

        if request_type == "scenario":
            lines += self.scenario_lines()
        else:
            lines.append("확정 판정 기준 진단입니다.")
            lines += self.demand_supply()
            lines.append(self.district_verdicts())
            if request_type == "station":
                lines += self.station_lines(self.by_key["station"]["station_id"])
                lines += self.competition_line("5) ")
            else:
                lines += self.cluster_lines()
        lines += self.decision_lines()
        lines += self.correction_lines(corrections, item_labels)
        lines += [f"※ 데이터 없음: {TOOL_FUELS.get(e['tool'], e['tool'])}" for e in errors]
        lines.append("현장 확인 항목: " + " · ".join(site_checks))
        lines.append("이 답변은 투자 결정이 아니라 진단입니다. 결정은 담당자가 합니다.")
        return lines
