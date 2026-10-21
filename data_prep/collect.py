"""공공 API 수집 배치. Agent는 이 스크립트가 만든 스냅샷만 읽는다.

사용법 (키는 환경변수에서 읽음: DATA_GO_KR_SERVICE_KEY, OPINET_API_KEY):
    python3 -I collect.py h2   <스냅샷 폴더>
    python3 -I collect.py ev   <스냅샷 폴더> [zcode=11]
    python3 -I collect.py opinet-around <스냅샷 폴더> <prodcd> <katec_x> <katec_y> [radius=5000]

- 공공데이터포털 키는 Encoding 키를 그대로 URL에 붙인다(다시 인코딩하면 인증 실패).
- 오피넷 키 파라미터 이름은 certkey.
- 오피넷은 총 300건 한도로 보고, 호출할 때마다 data/opinet_calls.tsv에 한 줄씩 남긴다.
"""

import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

OPINET_BUDGET = 300
LEDGER = Path(__file__).resolve().parent.parent / "data" / "opinet_calls.tsv"  # *.log는 .gitignore 대상이라 tsv로 둔다


def _get(url, timeout=30, retries=2):
    """타임아웃만 지수 백오프로 재시도한다."""
    delay = 1.0
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=timeout) as r:
                return r.read().decode("utf-8")
        except TimeoutError:
            if attempt == retries:
                raise
            time.sleep(delay)
            delay *= 3


def _now():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ---------- 석유관리원 수소 대기 ----------

def collect_h2(out_dir):
    key = os.environ["DATA_GO_KR_SERVICE_KEY"]
    url = f"https://apis.data.go.kr/B552532/h2nbiz_5/waitVhcleInfo?serviceKey={key}"
    try:
        body = _get(url)
    except Exception as e:
        raise SystemExit(f"수소 API 호출 실패: {type(e).__name__}: {str(e).replace(key, '[KEY]')}") from None
    data = json.loads(body)
    items = data.get("body", {}).get("items", {}).get("item", data.get("response", {}).get("body", {}).get("items", {}).get("item"))
    if items is None:
        raise SystemExit("수소 API 응답에서 item을 찾지 못함: " + body[:300])
    if isinstance(items, dict):
        items = [items]
    path = Path(out_dir) / "h2_wait.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["collected_at", *items[0].keys()])
        w.writeheader()
        for it in items:
            w.writerow({"collected_at": _now(), **it})
    print(f"수소 대기: {len(items)}개 충전소 → {path}")


# ---------- 환경공단 충전기 ----------

EV_FIELDS = ["statId", "chgerId", "statNm", "chgerType", "addr", "lat", "lng", "busiId", "busiNm", "stat", "statUpdDt",
             "lastTsdt", "lastTedt", "nowTsdt", "output", "method", "zcode", "zscode", "kind", "kindDetail", "limitYn", "delYn", "year"]


def collect_ev(out_dir, zcode="11"):
    key = os.environ["DATA_GO_KR_SERVICE_KEY"]
    rows, page, total, calls = [], 1, None, 0
    while True:
        url = (f"https://apis.data.go.kr/B552584/EvCharger/getChargerInfo?serviceKey={key}"
               f"&pageNo={page}&numOfRows=9999&zcode={zcode}&dataType=JSON")
        try:
            data = json.loads(_get(url, timeout=60))
        except Exception as e:
            raise SystemExit(f"충전기 API 호출 실패: {type(e).__name__}: {str(e).replace(key, '[KEY]')}") from None
        calls += 1
        total = int(data.get("totalCount", 0))
        items = data.get("items", {}).get("item", [])
        if isinstance(items, dict):
            items = [items]
        rows.extend({k: it.get(k, "") for k in EV_FIELDS} for it in items)
        print(f"  page {page}: {len(items)}건 (누적 {len(rows)}/{total})")
        if not items or len(rows) >= total:
            break
        page += 1
    path = Path(out_dir) / f"ev_chargers_z{zcode}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["collected_at", *EV_FIELDS])
        w.writeheader()
        stamp = _now()
        for r in rows:
            w.writerow({"collected_at": stamp, **r})
    print(f"충전기: {len(rows)}건, API 호출 {calls}회 → {path}")


# ---------- 오피넷 ----------

def opinet_used():
    if not LEDGER.exists():
        return 0
    return sum(1 for line in LEDGER.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))


def opinet_call(endpoint, params, note):
    used = opinet_used()
    if used >= OPINET_BUDGET:
        raise SystemExit(f"오피넷 호출 한도 도달 ({used}/{OPINET_BUDGET})")
    key = os.environ["OPINET_API_KEY"]
    for k, v in params.items():
        if not str(v).replace(".", "").replace("-", "").isalnum():
            raise SystemExit(f"오피넷 파라미터 형식 오류: {k}={v!r} (호출하지 않음)")
    query = "&".join(f"{k}={v}" for k, v in params.items())
    try:
        body = _get(f"https://www.opinet.co.kr/api/{endpoint}?out=json&certkey={key}&{query}")
    except Exception as e:  # 오류 메시지에 키가 찍히지 않게 한다
        raise SystemExit(f"오피넷 호출 실패: {type(e).__name__}: {str(e).replace(key, '[KEY]')}") from None
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(f"{_now()}\t{endpoint}\t{query}\t{note}\n")
    return json.loads(body)


def collect_opinet_around(out_dir, prodcd, x, y, radius="5000"):
    data = opinet_call("aroundAll.do", {"x": x, "y": y, "radius": radius, "sort": "2", "prodcd": prodcd}, "around")
    oils = data.get("RESULT", {}).get("OIL", [])
    path = Path(out_dir) / f"opinet_around_{prodcd}_{x}_{y}_{radius}.json"
    path.write_text(json.dumps({"collected_at": _now(), "params": {"prodcd": prodcd, "x": x, "y": y, "radius": radius},
                                "OIL": oils}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"오피넷 {prodcd} ({x},{y}) r={radius}: {len(oils)}곳 → {path.name} | 누적 호출 {opinet_used()}/{OPINET_BUDGET}")
    return oils


if __name__ == "__main__":
    cmd, out = sys.argv[1], sys.argv[2]
    Path(out).mkdir(parents=True, exist_ok=True)
    if cmd == "h2":
        collect_h2(out)
    elif cmd == "ev":
        collect_ev(out, *(sys.argv[3:4] or ["11"]))
    elif cmd == "opinet-around":
        collect_opinet_around(out, *sys.argv[3:])
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
