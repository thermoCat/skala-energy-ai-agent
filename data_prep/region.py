"""시군구 지명 정규화.

세 데이터의 지명 표기를 하나의 키 (시도 약칭, 시군구) 로 맞춘다.
- 교통안전공단 전기차 등록(2025.4): "서울 강남구", "경기 성남시 분당구", "경상북도 성주군"
- 환경공단 충전소(2022.10): 시도 "서울특별시", 군구 "성남시" (세종은 군구 없음)
- 국토부 등록현황(2026.9): 시도 "전남광주"(광주+전남 통합 표기), 시군구 "화성시 동탄구"

규칙
1. 시도는 약칭으로 통일한다 (서울특별시, 서울 → 서울 / 강원도, 강원특별자치도 → 강원).
2. 일반구는 시 단위로 합친다 (성남시 분당구 → 성남시). 충전소 데이터가 시 단위이기 때문.
3. 2023.7 경북 군위군이 대구로 편입된 것을 반영해 군위군은 대구로 둔다.
4. 세종은 시군구가 하나뿐이므로 ("세종", "세종시") 로 둔다.
5. 광주와 전남은 키에서는 분리하고, 2026년 통합 표기 "전남광주"는 보정 그룹(sido_group)에서만 쓴다.
6. 인천 2026.7 개편 구는 옛 구와 함께 권역으로 묶는다 (SIGUNGU_MERGES).
7. 환경공단 충전소 데이터는 시도 칸이 틀린 행이 있어(예: 시도 "제주", 주소 "경상남도 창원시"), 주소에서 키를 먼저 만든다.
"""

import re

SIDO_ALIASES = {
    "서울": ["서울특별시", "서울시"],
    "부산": ["부산광역시"],
    "대구": ["대구광역시"],
    "인천": ["인천광역시"],
    "광주": ["광주광역시"],
    "대전": ["대전광역시"],
    "울산": ["울산광역시"],
    "세종": ["세종특별자치시", "세종시"],
    "경기": ["경기도"],
    "강원": ["강원도", "강원특별자치도"],
    "충북": ["충청북도"],
    "충남": ["충청남도"],
    "전북": ["전라북도", "전북특별자치도"],
    "전남": ["전라남도"],
    "경북": ["경상북도"],
    "경남": ["경상남도"],
    "제주": ["제주특별자치도", "제주도"],
}
_SIDO_LOOKUP = {short: short for short in SIDO_ALIASES}
for short, aliases in SIDO_ALIASES.items():
    for alias in aliases:
        _SIDO_LOOKUP[alias] = short

# 2026년 국토부 통계의 통합 표기. 광주·전남을 하나의 보정 그룹으로 묶는다.
MERGED_SIDO = {"전남광주": ("광주", "전남"), "전남광주통합특별시": ("광주", "전남")}

# 시도 경계 변경: (옛 시도, 시군구) → 새 시도
SIDO_MOVES = {("경북", "군위군"): "대구"}

# 시군구 경계 변경을 시점 간 비교가 가능한 권역으로 묶는다.
# - 인천 2026.7 개편: 중구 내륙+동구 → 제물포구, 영종 → 영종구, 서구 → 서구(국토부 2026.9 표기는 "서해구") + 검단구.
#   새 구와 옛 구의 경계가 1:1이 아니므로 옛 구 단위로 되돌리지 않고 권역으로 합친다.
# - 충북 청원군은 2014년 청주시에 통합됐다(국토부 통계에 잔여 행이 남아 있음).
SIGUNGU_MERGES = {
    ("인천", "중구"): "중구·동구",
    ("인천", "동구"): "중구·동구",
    ("인천", "제물포구"): "중구·동구",
    ("인천", "영종구"): "중구·동구",
    ("인천", "서해구"): "서구",
    ("인천", "검단구"): "서구",
    ("충북", "청원군"): "청주시",
}


def normalize_sido(name):
    """시도 표기를 약칭으로 바꾼다. 모르는 표기는 ValueError."""
    name = str(name).strip()
    if name in _SIDO_LOOKUP:
        return _SIDO_LOOKUP[name]
    raise ValueError(f"알 수 없는 시도 표기: {name!r}")


def normalize_sigungu(sigungu):
    """일반구를 떼고 시 단위로 만든다. '성남시 분당구' → '성남시'."""
    sigungu = re.sub(r"\s+", " ", str(sigungu).strip())
    parts = sigungu.split(" ")
    if len(parts) >= 2 and parts[0].endswith("시") and parts[1].endswith("구"):
        return parts[0]
    return sigungu


def region_key(sido, sigungu):
    """(시도 약칭, 시 단위 시군구) 키를 만든다."""
    s = normalize_sido(sido)
    if s == "세종":
        return ("세종", "세종시")
    g = normalize_sigungu(sigungu)
    s = SIDO_MOVES.get((s, g), s)
    g = SIGUNGU_MERGES.get((s, g), g)
    return (s, g)


def key_from_joined(text):
    """'서울 강남구', '경기 성남시 분당구', '경상북도 성주군' 같은 한 줄 표기를 키로 바꾼다."""
    sido, _, rest = str(text).strip().partition(" ")
    return region_key(sido, rest)


def key_from_address(address):
    """'서울특별시 강남구 봉은사로 130' 같은 주소 앞부분으로 키를 만든다 (오피넷 등)."""
    tokens = re.sub(r"\s+", " ", str(address).strip()).split(" ")
    sido = normalize_sido(tokens[0])
    if sido == "세종":
        return ("세종", "세종시")
    sigungu = tokens[1]
    # 띄어쓰기 누락: '영암군삼호읍' → '영암군'
    glued = re.match(r"^(\S+?[시군구])\S+[읍면동가리로길]$", sigungu)
    if glued:
        sigungu = glued.group(1)
    if len(tokens) > 2 and sigungu.endswith("시") and tokens[2].endswith("구"):
        sigungu = f"{sigungu} {tokens[2]}"
    return region_key(sido, sigungu)


GWANGJU_GU = {"광산구", "남구", "동구", "북구", "서구"}


def split_merged_sido(label, sigungu):
    """'전남광주' 아래 시군구를 광주/전남으로 되돌린다. 광주는 자치구 5곳뿐이고 전남에는 구가 없다."""
    label = str(label).strip()
    if label not in MERGED_SIDO:
        return label
    return "광주" if str(sigungu).strip() in GWANGJU_GU else "전남"


def sido_group(sido_short):
    """보정용 그룹. 2026년 통계가 광주·전남을 합쳐서 내므로 둘을 한 그룹으로 묶는다."""
    return "전남광주" if sido_short in ("광주", "전남") else sido_short


def normalize_group_label(label):
    """국토부 시도 표기를 보정 그룹 이름으로 바꾼다 ('전남광주' 포함)."""
    label = str(label).strip()
    if label in MERGED_SIDO:
        return "전남광주"
    return sido_group(normalize_sido(label))
