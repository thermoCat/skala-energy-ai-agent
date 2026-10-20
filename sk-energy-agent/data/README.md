# data — 실데이터 (mock 없음)

모든 파일은 공공 출처에서 실제로 받은 데이터와, 그것을 [data_prep/](../data_prep/README.md) 스크립트로 정리한 결과입니다.

> `monitoring.json`, `runbooks/` 는 베이스코드(인시던트 대응 예제)의 데이터입니다. 우리 서비스로 바꾸는 동안 베이스코드가 동작하도록 그대로 둡니다.

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
| `keco_ev_charger_20221027.csv` | [공공데이터포털 15119741](https://www.data.go.kr/data/15119741/fileData.do) 한국환경공단 충전소 위치 및 운영정보 | 2022.10.27 | 43MB | **제외** (용량, 위 링크에서 다시 받음) |

> ⚠️ 월간 비교를 위해 `seoul_fuel_registration_202608.xlsx` 를 같은 출처에서 받아 추가해야 합니다.

## snapshots/2026-10-19/ — 첫 스냅샷

| 파일 | 내용 | 수집 | git |
|---|---|---|---|
| `ev_chargers_z11.csv` | 서울 충전기 76,203 ID (정보·상태) | `collect.py ev 11` | 커밋 (17MB, 실행에 필요) |
| `ev_chargers_z28.csv` / `ev_chargers_z41.csv` | 인천 / 경기 충전기 (자치구 경계 판정용) | `collect.py ev 28` / `41` | **제외** (8MB / 36MB, 다시 수집 가능) |
| `h2_wait.csv` | 수소충전소 대기 차량, 전국 247곳 | `collect.py h2` | 커밋 |
| `opinet/opinet_around_{B027,K015}_*.json` | 오피넷 반경 검색 원문 (주유소 18원 + LPG 18원) | `collect.py opinet-around` | 커밋 (다시 받으면 한도 소모) |
| `opinet/grid_plan*.json` | 반경 검색 원 배치 계산 결과 | `plan_grid.py` | 커밋 |
| `seoul_stations.csv` | 서울 주유소 397 + LPG 73 = 470곳 (좌표·자치구·상표·주유소 안 충전기) | `build_supply.py` | 커밋 |
| `seoul_fuel_balance.csv` | 자치구 × 5개 연료 수급표 | `build_supply.py` | 커밋 |
| `seoul_ev_bottleneck_rank.csv` | 전기 병목 순위 (예시) | ⚠️ 생성 스크립트 없음 | 커밋 |

## derived/

| 파일 | 내용 | 생성 |
|---|---|---|
| `seoul_fuel_monthly.csv` | 기준월 × 자치구 × 연료 (2025.09, 2026.09) | `seoul_fuel.py` |
| `region_demand.csv` | 전국 시군구 228곳 전기차 수요 추정 (서울 밖 확장용) | `build_demand.py` |
| `coverage_report.md` | 세 데이터 간 시군구 키 일치 점검 | `build_demand.py` |

## 키·호출 주의

- 수집에 필요한 키는 환경변수 `DATA_GO_KR_SERVICE_KEY`, `OPINET_API_KEY` 에서만 읽습니다. 파일에 키를 쓰지 않습니다.
- 오피넷은 누적 300건 한도입니다. `opinet_calls.tsv` 를 지우거나 고치지 마세요(한도 계산이 틀어집니다).
