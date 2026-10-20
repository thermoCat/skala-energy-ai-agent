"""실제 LLM 출력 다듬기 테스트 — 형식이 깨지거나 허용 밖 값이 와도 코드가 걸러내는지"""
from agent import energy_prompts as p
from energy.seoul import BRAND_ALIASES, SEOUL_GU

FACTS = [
    {"id": "F1", "key": "ev_growth_pct", "label": "동대문구 전기차 1년 증감률", "value": 57.0,
     "unit": "%", "source": "s"},
    {"id": "F2", "key": "fast_1km:A0009172", "label": "풍한주유소 (주유소) 반경 1km 급속 ID",
     "value": 10, "unit": "ID", "source": "s", "station_id": "A0009172", "prodcd": "B027",
     "name": "풍한주유소"},
    {"id": "F3", "key": "fast_1km:A0009251", "label": "(주)홍릉가스 (LPG 충전소) 반경 1km 급속 ID",
     "value": 13, "unit": "ID", "source": "s", "station_id": "A0009251", "prodcd": "K015",
     "name": "(주)홍릉가스"},
]


def test_classify_keeps_only_values_found_in_question():
    content = '{"gu": "강남구", "station_name": "박미주유소", "brand": "GS", "fast_add": 3, "kw": null}'
    result = p.normalize_classify(content, "박미주유소에 급속 2기 넣으면?", SEOUL_GU, BRAND_ALIASES)
    # 질문에 없는 강남구·GS, 질문과 다른 급속 3기는 버린다
    assert result == {"gu": None, "station_name": "박미주유소", "brand": None,
                      "fast_add": None, "kw": None}


def test_classify_reads_fenced_json():
    content = '설명입니다\n```json\n{"gu": "동대문구", "station_name": null, "brand": null, ' \
              '"fast_add": null, "kw": null}\n```'
    result = p.normalize_classify(content, "동대문구 SK 거점 어때?", SEOUL_GU, BRAND_ALIASES)
    assert result["gu"] == "동대문구"


def test_judgement_drops_unknown_facts_and_non_gas_candidates():
    content = ('{"ev_bottleneck": {"value": true, "facts": ["F1", "F99"], "reason": "증가 빠름"},'
               ' "diesel_idle": {"value": "yes", "facts": [], "reason": ""},'
               ' "fast_candidates": {"value": ["A0009251", "A0009172", "A9999999"], "facts": ["F2"],'
               ' "reason": "적은 순", "candidate_reasons": [{"station_id": "A0009172",'
               ' "facts": ["F2"], "text": "급속 적음"}]}}')
    result = p.normalize_judgement(content, "district", FACTS)
    assert result["ev_bottleneck"] == {"value": True, "facts": ["F1"], "reason": "증가 빠름"}
    assert result["diesel_idle"]["value"] is None          # bool 이 아니면 판별 없음
    assert result["lpg_idle"]["value"] is None             # 빠진 항목도 판별 없음
    # LPG 충전소·묶음 밖 번호는 후보에서 뺀다
    assert result["fast_candidates"]["value"] == ["A0009172"]
    assert result["fast_candidates"]["reasons"]["A0009172"]["text"] == "급속 적음"


def test_broken_judgement_output_becomes_no_judgement():
    result = p.normalize_judgement("모르겠습니다", "scenario", FACTS)
    assert result["ev_bottleneck_after"]["value"] is None


def test_answer_judgement_parsing():
    assert p.normalize_answer_judgement('{"issues": []}') == {"passed": True, "issues": []}
    assert p.normalize_answer_judgement('{"issues": ["현장 확인 항목 없음"]}')["passed"] is False
    assert p.normalize_answer_judgement("형식 붕괴")["passed"] is True
    # 프롬프트 문구를 그대로 베낀 지적은 버린다
    assert p.normalize_answer_judgement('{"issues": ["모두 지켰으면 issues 는 빈 목록이다."]}')["passed"] is True


def test_write_prompt_contains_facts_verdicts_and_musts():
    verdicts = {"fast_candidates": {"id": "J4", "value": ["A0009172"]}}
    FACTS_WITH_MEDIAN = FACTS + [{"id": "F4", "key": "cluster_median_fast_1km", "label": "묶음 중앙값",
                                  "value": 26.0, "unit": "ID", "source": "s"}]
    system, user = p.write_prompt("district", FACTS_WITH_MEDIAN, verdicts, [], True, [], ("부지",),
                                  {"fast_candidates": "묶음 급속 후보"}, None, ["근거 없는 숫자: 320.0"])
    assert "[F2] 풍한주유소 (주유소) 반경 1km 급속 ID: 10 ID" in user
    assert "## 답변 뼈대" in user and "풍한주유소 — 반경 1km 급속 ID 10[F2]" in user
    assert "현장 확인 항목: 부지" in user and "근거 없는 숫자: 320.0" in user
