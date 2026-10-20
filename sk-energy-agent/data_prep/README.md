# data_prep — 시군구 지명 정리와 수요 테이블

세 공공데이터의 지명을 하나의 키 `(시도 약칭, 시 단위 시군구)`로 맞추고, 시군구별 전기차 수요 테이블을 만듭니다.

## 원본 데이터 (`data/raw/`, 출처와 커밋 여부는 [data/README.md](../data/README.md))

| 파일 | 출처 | 비고 |
|---|---|---|
| 전국_전기차_차종별_용도별_차량_등록대수(운행차량기준).csv | [공공데이터포털 15142951](https://www.data.go.kr/data/15142951/fileData.do) | 한국교통안전공단, 2025.4.7, cp949 |
| 한국환경공단_전기차 충전소 위치 및 운영정보_20221027.csv | [공공데이터포털 15119741](https://www.data.go.kr/data/15119741/fileData.do) | 2022.10.27, 44.9MB, utf-8 |
| 2026년 09월 자동차 등록자료 통계.xlsx | [국토교통 통계누리](https://stat.molit.go.kr/portal/cate/statMetaView.do?hRsId=58&hFormId=1244) | 국토교통부, 2026.9 |

| 서울시 자치구별 연료별 차종별 용도별 등록현황(26년9월).xlsx 등 월별 파일 | [서울 열린데이터광장 OA-15640](https://data.seoul.go.kr/dataList/OA-15640/F/1/datasetView.do) | 월간, 2024.3~ |

## 파일

| 파일 | 내용 |
|---|---|
| `region.py` | 시군구 지명 정규화 규칙 |
| `build_demand.py` | 전국 시군구 전기차 수요 테이블 생성 |
| `seoul_fuel.py` | 서울 자치구 × 5개 연료(휘발유·경유·LPG·전기·수소) 월간 표 생성 |
| `katec.py` | WGS84 ↔ KATEC 좌표 변환 (오피넷 API용, 외부 라이브러리 없음) |
| `collect.py` | API 수집 배치 (수소 대기, 환경공단 충전기, 오피넷 반경 검색). 오피넷 호출은 `data/opinet_calls.tsv`에 기록하고 300건에서 멈춤 |
| `plan_grid.py` | 서울을 덮는 최소 반경 검색 원 배치 계산 (오피넷 호출 절약) |
| `build_supply.py` | 스냅샷으로 서울 자치구 × 5개 연료 수급표, 주유소 목록 생성 |
| `build_fast_status.py` | 스냅샷 충전기 원본에서 서울 급속 상태만 골라 `fast_status.csv` 생성 (매일 감시용, 매시 원본은 git 제외) |
| `build_border_chargers.py` | 서울 거점 2km 안의 경기·인천 급속충전기만 골라 `ev_fast_border.csv` 생성 (경계 근처 반경 급속 수용) |

### 스냅샷 수집 (키는 `~/.zshrc`의 환경변수)

```bash
python3 -I data_prep/collect.py h2 data/snapshots/<날짜>
python3 -I data_prep/collect.py ev data/snapshots/<날짜> 11
python3 -I data_prep/build_supply.py data/snapshots/<날짜> data/derived/seoul_fuel_monthly.csv 2026.09
python3 -I data_prep/build_border_chargers.py data/snapshots/<날짜>   # 경기·인천 충전기(ev 28, 41) 수집 후
python3 -I data_prep/build_fast_status.py data/snapshots/<날짜>T<시>   # 매시 수집 후 (매일 감시용)
```

- 공공데이터포털 키는 Encoding 키를 그대로 URL에 붙입니다(다시 인코딩하면 인증 실패).
- 오피넷 키 파라미터 이름은 `certkey`입니다.
- 오피넷 주유소·LPG 목록은 자주 바뀌지 않으므로 다시 수집하지 않고 재사용합니다.

```bash
python3 -I data_prep/seoul_fuel.py data/derived/seoul_fuel_monthly.csv data/raw/seoul_fuel_registration_202509.xlsx data/raw/seoul_fuel_registration_202609.xlsx
```

## 실행

```bash
python3 -I data_prep/build_demand.py data/raw/kotsa_ev_registration_202504.csv data/raw/keco_ev_charger_20221027.csv data/raw/molit_vehicle_registration_202609.xlsx data/derived
```

출력:
- `data/derived/region_demand.csv`: 시군구 228곳 × 전기차 수요, 보정 계수, 2022년 충전기 수
- `data/derived/coverage_report.md`: 세 데이터 간 키 일치 점검 결과

## 정리 규칙 (`region.py`)

| # | 규칙 | 예 |
|---|---|---|
| 1 | 시도는 약칭으로 통일 | 서울특별시 → 서울, 강원도·강원특별자치도 → 강원, 전라북도·전북특별자치도 → 전북 |
| 2 | 일반구는 시 단위로 합침 (충전소 데이터가 시 단위) | 성남시 분당구 → 성남시, 화성시 동탄구 → 화성시 |
| 3 | 시도 경계 변경 반영 | 경북 군위군 → 대구 군위군 (2023.7 편입) |
| 4 | 세종은 하나의 키 | (세종, 세종시) |
| 5 | 광주·전남은 키에서 분리, 2026년 "전남광주" 통합 표기는 보정 그룹에서만 사용 | 전남광주 광산구 → (광주, 광산구) |
| 6 | 인천 2026.7 개편 구는 옛 구와 권역으로 묶음 | 중구·동구·제물포구·영종구 → 중구·동구 / 서구·서해구·검단구 → 서구 |
| 7 | 폐지된 시군 정리 | 충북 청원군 → 청주시 |
| 8 | 충전소 데이터는 시도 칸 대신 **주소**에서 키를 만듦 | 시도 "제주" + 주소 "경상남도 창원시…" → (경남, 창원시) |
| 9 | 주소 띄어쓰기 누락 처리 | "영암군삼호읍" → 영암군 |
| 10 | 같은 시도의 약칭·정식 표기 중복을 합침 | "경상북도 성주군" + "경북 성주군" → (경북, 성주군) |

## 점검 결과 (2026-10-19 실행)

- 전기차 원본 254개 표기 → 키 228개
- 충전소 키 228개. 주소 기준으로 바로잡은 행은 139개이고, 그중 137개는 시도가 틀렸던 행입니다(예: 시도 "전남"인데 주소는 전북 전주시인 26개). 나머지 2개는 같은 서울 안에서 군구 칸은 광진구, 주소는 도봉구였던 행이라 주소를 따랐습니다.
- 국토부 2026.9 키 228개
- **세 데이터 모두 228개 키가 완전히 일치**합니다.

## 보정 방법

`ev_nonbiz_est_2026_09` = 시군구 비사업용 전기차(2025.4) × 그 시도의 증가율(2026.9 ÷ 2025.4)

- 증가율은 시도별 1.34(제주) ~ 1.90(울산), 전국 1.69입니다.
- 광주·전남은 2026년 통계가 합쳐져 있어 하나의 증가율(1.573)을 같이 씁니다.
- ⚠️ 시도 안에서 시군구마다 증가 속도가 다르면 오차가 생깁니다. 추정치라는 점을 밝혀야 합니다.
- ⚠️ 교통안전공단 자료는 "운행차량 기준", 국토부는 "등록 기준"이라 정의 차이가 증가율에 섞여 있을 수 있습니다.
