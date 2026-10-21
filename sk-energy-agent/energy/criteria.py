"""판별 기준 임계값을 한 곳에서 관리하는 모듈 (초안)

임계값을 바꿀 때는 이 파일만 고치고 RULE_VERSION 을 올린다.
"""

RULE_VERSION = "draft-2026-10-20"

# 1년 비교에 쓰는 두 기준월 (서울시 OA-15640 월간 파일)
BASE_MONTH = "2025.09"
CURRENT_MONTH = "2026.09"

# 전기 병목: 순위 합산 점수가 낮은 순 상위 20% (25구 → 5구, 동점 포함)
EV_BOTTLENECK_TOP_RATIO = 0.2

# 경유·LPG 유휴 위험(화면 이름 "차량 감소"): 1년 감소율이 가장 큰 쪽 하위 20% (25구 → 5구, 동점 포함)
# "서울 평균보다 더 감소"로 하면 구조상 절반이 걸려, 판매량 없이 정리 검토를 남발하게 된다
FUEL_IDLE_BOTTOM_RATIO = 0.2

# 전기 유휴 위험: 급속 충전 중 비율이 최근 스냅샷 3개 연속 서울 하위 20%
EV_IDLE_BOTTOM_RATIO = 0.2
EV_IDLE_MIN_SNAPSHOTS = 3

# 수소 병목: 같은 충전소·같은 시간대에 대기 차량이 있는 날이 3일 이상
H2_QUEUE_MIN_DAYS = 3

# 매일 감시: 자사 거점 반경 1km 급속이 최근 3회 수집 연속 고장이고, 고장으로 바뀐 지 24시간 이내면 "새 고장"
# — 오래 멈춰 있던 충전기까지 잡으면 SK 주유소 47곳에 알림이 떠서 아침 알림의 의미가 사라진다
ALERT_STATS = ("1", "4", "5")        # 통신이상 · 운영중지 · 점검중
WATCH_CONSECUTIVE = 3
NEW_FAILURE_HOURS = 24
ALERT_TOP_STATIONS = 3              # 회사당 하루 알림 1건, 위에 올리는 거점 수

# 주간 경쟁 추적: 자사·계열 충전 운영사 (busiNm). 나머지는 타사로 본다
# SK일렉링크는 2025년 SK네트웍스가 경영권을 앵커PE에 넘겨 SK 계열이 아니다 (한국경제 2026.7.24)
# GS차지비는 GS칼텍스의 충전 자회사다
COMPANY_OPERATORS = {
    "SK에너지": ("SK에너지",),
    "GS칼텍스": ("GS칼텍스", "GS차지비"),
}
NEARBY_TOP_OPERATORS = 5            # 주변 경쟁 현황에 보여줄 운영사 수

# 묶음 안 급속 후보: 반경 1km 급속 수가 묶음 중앙값 이하, 적은 순으로 최대 2곳
FAST_CANDIDATE_MAX = 2

# 시나리오: 이 출력 이상이면 고출력 충전 시간 중앙값을 쓴다
HIGH_POWER_KW = 100

# 거점 기준 판정 이름 — 차량 감소는 근거가 자치구 숫자라 거점에 붙이지 않고 묶음 신호로만 보여준다
STATION_VERDICTS = {
    "fast_candidate": "⚡ 급속 후보",
    "keep": "유지",
}
FUEL_IDLE_LABEL = "🔻 차량 감소 (자치구 신호)"

# 전환 유형 — 부지 크기를 몰라 판정하지 않고 선택지로만 보여준다
CONVERSION_TYPES = (
    "유지",
    "소형 추가 (초급속 충전기 1~2대)",
    "복합 확장 (충전 + 발전 + 점포)",
    "재건축 (옥내 주유소 + 물류 + 연료전지)",
    "정리 검토",
)

# 권위 게이트가 기준 판정과 대조하는 항목 (키 → 화면 이름)
GATED_ITEMS = {
    "ev_bottleneck": "충전기 부족",
    "diesel_idle": "경유차 감소",
    "lpg_idle": "LPG차 감소",
    "fast_candidates": "급속 후보",
    "ev_bottleneck_before": "급속 추가 전 충전기 부족",
    "ev_bottleneck_after": "급속 추가 후 충전기 부족",
    "report_needed": "주간 보고에 올릴 변화",
    "watch_competitors": "경쟁 변화를 볼 거점",
    "alert_needed": "알림 필요",
    "alert_stations": "알림 상단 거점",
}

# 모든 진단 카드에 붙는 현장 확인 항목
# 부지·주차면은 토지대장 등이 붙으면 AI가 볼 수 있어, 따로 문의·측정해야 하는 두 가지만 남긴다
SITE_CHECK_ITEMS = (
    "한전 전력 인입 용량",
    "안전거리 (폭발위험장소 밖 설치 가능 여부)",
)
