"""답변 맨 위 "요약:" 한 줄 테스트 — 화면은 기본으로 제목과 이 줄만 보여준다"""


def _summary(result):
    return result["final"].splitlines()[1]


def test_station_summary(ask):
    first = ask("이 주유소에 무엇을 더해야 해?", station_id="A0009172")
    assert _summary(first) == "요약: ⚡ 충전기 후보 — 같은 구 후보 2곳 중 첫 번째 · 충전기 부족 구"
    other = ask("이 주유소에 무엇을 더해야 해?", station_id="A0001146")          # 금천 박미
    assert _summary(other).startswith("요약: 충전기 후보 아님 · 충전기 부족 아님")


def test_district_summary_names_first_candidate_and_idle_signal(ask):
    assert _summary(ask("동대문구 SK 거점 어떻게 써?", gu="동대문구")) == \
        "요약: 충전기 부족 구 · 충전기 후보 2곳 — 먼저 풍한주유소"
    gangnam = _summary(ask("강남구 SK 거점 어떻게 써?", gu="강남구"))
    assert gangnam == "요약: 충전기 부족 아님 · 충전기 후보 없음 · 경유차 감소 큰 구 · LPG차 감소 큰 구"


def test_scenario_and_public_summary(ask):
    scenario = ask("충전기 2대 신설하면?", station_id="A0009172")
    assert _summary(scenario).startswith("요약: 충전기 2대[") and "여전히 충전기 부족" in _summary(scenario)
    assert scenario["validation"]["passed"] is True                 # 요약 줄 숫자도 근거 대조 통과
    public = ask("이 주유소 주변 경쟁 현황은?", station_id="A0009177")
    assert _summary(public).startswith("요약: 타사 거점 — 공개 정보만 (주변 급속 운영사")
