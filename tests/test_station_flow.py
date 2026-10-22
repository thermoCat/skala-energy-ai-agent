"""주유소 핀 클릭 흐름 테스트 — 실제 MCP 서버 + 실데이터 스냅샷 + fake LLM"""
from tests.conftest import event_types

PUNGHAN = "A0009172"    # 동대문구 SK에너지 풍한주유소
HONGNEUNG = "A0009177"  # 동대문구 S-OIL 홍능주유소 (타사)


def test_own_station_gate_corrects_diesel_judgement(ask, store):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id=PUNGHAN)

    assert result["request_type"] == "station" and result["gu"] == "동대문구"
    assert result["llm_verdicts"]["diesel_idle"]["value"] is True    # fake의 의도적 오판
    assert result["verdicts"]["ev_bottleneck"]["value"] is True
    assert result["verdicts"]["diesel_idle"]["value"] is False       # 게이트가 교정
    assert result["gate_corrections"] == [
        {"item": "diesel_idle", "llm": True, "baseline": False}]
    assert PUNGHAN in result["verdicts"]["fast_candidates"]["value"]
    assert "GateCorrected" in event_types(result)

    # 분석 에이전트 입력(사실)에 기준 판정 값이 섞이지 않음
    assert all(not any(word in f["key"] for word in ("bottleneck", "idle", "candidate"))
               for f in result["facts"])

    answer = result["final"]
    for text in ("동대문구", "풍한주유소", "+57.0%", "+43.2%", "31.5대", "17.1대",
                 "-4.8%", "-6.6%", "⚡ 충전기 후보입니다", "게이트 교정", "현장 확인 항목"):
        assert text in answer
    assert result["validation"] == {"passed": True, "issues": []}

    logs = store.load_decisions(PUNGHAN)
    assert len(logs) == 1 and logs[0]["corrected"] == 1


def test_same_question_three_times_gives_same_final_verdict(ask):
    finals = [ask("이 주유소에 무엇을 더해야 해?", station_id=PUNGHAN)["verdicts"]
              for _ in range(3)]
    assert finals[0] == finals[1] == finals[2]


def test_other_company_station_gets_public_info_only(ask, store):
    result = ask("이 주유소 주변 경쟁 현황은?", station_id=HONGNEUNG)

    assert result["is_own"] is False
    assert "llm_verdicts" not in result and "verdicts" not in result
    assert "투자 판별은 하지 않습니다" in result["final"]
    assert store.load_decisions(HONGNEUNG) == []


def test_unknown_station_id_does_not_crash(ask):
    result = ask("이 주유소에 무엇을 더해야 해?", station_id="A0000000")
    assert "찾지 못했습니다" in result["final"]
