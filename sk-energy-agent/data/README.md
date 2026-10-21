# data — 실데이터 (mock 없음)

모든 파일은 공공 출처에서 실제로 받은 데이터와, 그것을 [data_prep/](../data_prep/README.md) 스크립트로 정리한 결과입니다.

## 폴더 구조

```
data/
  raw/                     원본 파일 (다운로드한 그대로, 이름만 정리)
  snapshots/<날짜>/        API 수집 결과 + 그 시점 파생 테이블 (덮어쓰지 않음)
  derived/                 여러 시점에 걸친 정리 결과
  opinet_calls.tsv         오피넷 호출 원장 (누적 41/300) — collect.py가 자동 기록
```

## raw/ — 원본

| 파일 | 출처 | 기준 | 크기 | git |
|---|---|---|---|---|
| `seoul_fuel_registration_202509.xlsx` | [서울 열린데이터광장 OA-15640](https://data.seoul.go.kr/dataList/OA-15640/F/1/datasetView.do) 자치구별 연료별 차종별 용도별 등록현황 | 2025.9 | 64KB | 커밋 |
| `seoul_fuel_registration_202609.xlsx` | 〃 | 2026.9 | 64KB | 커밋 |
| `molit_vehicle_registration_202609.xlsx` | [국토교통 통계누리](https://stat.molit.go.kr/portal/cate/statMetaView.do?hRsId=58&hFormId=1244) 자동차 등록자료 통계 | 2026.9 | 436KB | 커밋 |
| `kotsa_ev_registration_202504.csv` | [공공데이터포털 15142951](https://www.data.go.kr/data/15142951/fileData.do) 한국교통안전공단 전기차 등록대수 (cp949) | 2025.4 | 24KB | 커밋 |
| `seoul_gu_boundaries_2013.json` | 서울 25개 자치구 경계 GeoJSON — [southkorea/seoul-maps](https://github.com/southkorea/seoul-maps) `kostat/2013/json/seoul_municipalities_geo_simple.json` (원자료 통계청 통계지리정보, 저장소 라이선스 표기 없음). 지도에서 구 신호를 경계 따라 칠할 때만 사용 | 2013 | 58KB | 커밋 |
| `keco_ev_charger_20221027.csv` | [공공데이터포털 15119741](https://www.data.go.kr/data/15119741/fileData.do) 한국환경공단 충전소 위치 및 운영정보 | 2022.10.27 | 43MB | **제외** (용량, 위 링크에서 다시 받음) |

> ⚠️ 월간 비교를 위해 `seoul_fuel_registration_202608.xlsx` 를 같은 출처에서 받아 추가해야 합니다.

## snapshots/2026-10-19/ — 첫 스냅샷

| 파일 | 내용 | 수집 | git |
|---|---|---|---|
| `ev_chargers_z11.csv` | 서울 충전기 76,203 ID (정보·상태) | `collect.py ev 11` | 커밋 (17MB, 실행에 필요) |
| `ev_chargers_z28.csv` / `ev_chargers_z41.csv` | 인천 / 경기 충전기 (자치구 경계 판정용) | `collect.py ev 28` / `41` | **제외** (8MB / 36MB, 다시 수집 가능) |
| `ev_fast_border.csv` | 서울 거점 2km 안의 경기·인천 급속충전기 693 ID (경계 근처 거점의 반경 1km 급속 수용) | `build_border_chargers.py` | 커밋 (119KB) |
| `h2_wait.csv` | 수소충전소 대기 차량, 전국 247곳 | `collect.py h2` | 커밋 |
| `opinet/opinet_around_{B027,K015}_*.json` | 오피넷 반경 검색 원문 (주유소 18원 + LPG 18원) | `collect.py opinet-around` | 커밋 (다시 받으면 한도 소모) |
| `opinet/grid_plan*.json` | 반경 검색 원 배치 계산 결과 | `plan_grid.py` | 커밋 |
| `seoul_stations.csv` | 서울 주유소 397 + LPG 73 = 470곳 (좌표·자치구·상표·주유소 안 충전기). 자치구 배정 신뢰도 컬럼 `판정` 은 코드에서 `자치구_판정` 으로 읽음 (다음 스냅샷부터 파일에도 `자치구_판정`) | `build_supply.py` | 커밋 |
| `seoul_fuel_balance.csv` | 자치구 × 5개 연료 수급표 | `build_supply.py` | 커밋 |
| `seoul_ev_bottleneck_rank.csv` | 전기 병목 순위 (예시) | ⚠️ 생성 스크립트 없음 | 커밋 |

## snapshots/<날짜>T<시>/ — 매시 수집 (일간 추적용)

| 폴더 | 수집 시각 | 내용 |
|---|---|---|
| `2026-10-20T19/` | 2026-10-20 19:54 | `fast_status.csv`, `h2_wait.csv` |
| `2026-10-20T20/` | 2026-10-20 20:55 | `fast_status.csv`, `h2_wait.csv` |
| `2026-10-21T09/` | 2026-10-21 09:02 | `fast_status.csv`, `h2_wait.csv` |
| `2026-10-21T10/` | 2026-10-21 10:00 | `fast_status.csv`, `h2_wait.csv` |

- `fast_status.csv`: 서울 급속충전기 상태만 (`collected_at, statId, chgerId, statNm, busiNm, zscode, lat, lng, stat, statUpdDt, output, limitYn`). `statNm`·`limitYn`(이용자 제한)은 주유소 주간 추적에서 버스 전용·이용 제한 충전기를 가르는 데 씁니다 (10-21 전체 스냅샷을 원본에서 다시 만듦, 기존 열 값은 그대로). `build_fast_status.py` 로 만들고 커밋 (약 590KB). `2026-10-19/` 에도 같은 파일이 있습니다.
- 매시 원본 `ev_chargers_z11.csv`(약 18MB)는 **git 제외** — 다시 받을 수 없으므로 로컬에 보관합니다.
- 일간 추적 기준: 직전 수집에서 정상(`stat` 2·3)이던 급속이 그 뒤 3회 수집 연속 고장(`stat` 1·4·5) (`energy/criteria.py`). `statUpdDt` 는 운영사가 같은 상태를 다시 보고할 때마다 바뀌어 고장 시작 시각으로 쓰지 않습니다.
- ⚠️ 간격이 고르지 않습니다(약 32시간 → 1시간 → 12시간 → 1시간). 10-21 11시 수집까지 예약돼 있습니다.

## 급속충전기 숫자 기준

자치구 단위 급속충전기 수는 **서울 자치구가 배정된 급속 ID 6,045개** 한 가지 기준으로 계산합니다 (`energy/store.py`).

| 숫자 | 6,045 기준 값 | 예전 값 (다른 기준) |
|---|---|---|
| 급속 ID | 6,045 | 6,078 — 삭제 표시(`delYn=Y`) 33 ID 포함 |
| 비정상 상태 (통신이상·운영중지·점검중·상태미확인) | 535 ID (8.9%) | 568 ID (약 9%) |
| 마지막 1회 충전 시간이 유효한 ID | 5,203 | 5,234 |
| 충전 시간 중앙값 (전체 / 100kW 이상 / 100kW 미만) | 35.1 / 30.5 / 46.0분 | 35.1 / 30.5 / 45.9분 |

거점 반경 1km 급속 수(`fast_1km`)만 경계 너머 경기·인천 급속(`ev_fast_border.csv`)을 더합니다. 서울 데이터만 쓰면 경계 근처 59곳(SK 13곳)이 적게 세어집니다 (예: 금천구 박미주유소 20 → 45).

## derived/

| 파일 | 내용 | 생성 |
|---|---|---|
| `seoul_fuel_monthly.csv` | 기준월 × 자치구 × 연료 (2025.09, 2026.09) | `seoul_fuel.py` |
| `region_demand.csv` | 전국 시군구 228곳 전기차 수요 추정 (서울 밖 확장용) | `build_demand.py` |
| `coverage_report.md` | 세 데이터 간 시군구 키 일치 점검 | `build_demand.py` |

## 키·호출 주의

- 수집에 필요한 키는 환경변수 `DATA_GO_KR_SERVICE_KEY`, `OPINET_API_KEY` 에서만 읽습니다. 파일에 키를 쓰지 않습니다.
- 오피넷은 누적 300건 한도입니다. `opinet_calls.tsv` 를 지우거나 고치지 마세요(한도 계산이 틀어집니다).
