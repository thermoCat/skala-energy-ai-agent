"""서울 전체를 덮는 최소 개수의 반경 검색 중심점을 고른다 (오피넷 호출 절약).

커버 대상: 서울 충전기 위치(전역에 퍼져 있음). 각 점이 어떤 중심점에서 COVER_M 이내여야 한다.
후보 중심점: 1km 격자. 탐욕적 집합 덮개(greedy set cover)로 최소에 가깝게 고른다.
"""
import csv, json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from katec import wgs84_to_katec

COVER_M = 4700
SEOUL_LAT, SEOUL_LNG = (37.41, 37.72), (126.76, 127.19)  # 서울 경계 사각형  # 반경 5000m보다 조금 작게 잡아 경계 누락을 막는다

def main(ev_csv, out_json, fixed=()):
    pts = set()
    with open(ev_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["delYn"] == "Y" or not r["lat"]:
                continue
            lat, lng = float(r["lat"]), float(r["lng"])
            if not (SEOUL_LAT[0] <= lat <= SEOUL_LAT[1] and SEOUL_LNG[0] <= lng <= SEOUL_LNG[1]):
                continue  # 좌표가 서울 밖으로 잘못 입력된 행 제외
            x, y = wgs84_to_katec(lat, lng)
            pts.add((round(x, -2), round(y, -2)))  # 100m 단위로 묶어 계산량 축소
    pts = list(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cands = [(x, y) for x in range(int(min(xs)), int(max(xs)) + 1000, 1000) for y in range(int(min(ys)), int(max(ys)) + 1000, 1000)]
    uncovered = set(range(len(pts)))
    def covers(c):
        return {i for i in uncovered if math.hypot(pts[i][0] - c[0], pts[i][1] - c[1]) <= COVER_M}
    chosen = []
    for c in fixed:
        uncovered -= covers(c); chosen.append(c)
    while uncovered:
        best = max(cands, key=lambda c: len(covers(c)))
        got = covers(best)
        if not got:
            break
        uncovered -= got; chosen.append(best)
    Path(out_json).write_text(json.dumps({"cover_m": COVER_M, "points": len(pts), "centers": chosen}, indent=1))
    print(f"커버 대상 {len(pts)}개 위치, 중심점 {len(chosen)}개 (고정 {len(fixed)}개 포함)")
    for c in chosen: print(c)

if __name__ == "__main__":
    fixed = [tuple(map(float, a.split(","))) for a in sys.argv[3:]]
    main(sys.argv[1], sys.argv[2], fixed)


def hex_plan(ev_csv, out_json, fixed=(), steps=12):
    """육각 격자 배치를 여러 오프셋으로 시도하고, 중복 원을 제거해 가장 적은 배치를 고른다."""
    pts = set()
    with open(ev_csv, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["delYn"] == "Y" or not r["lat"]:
                continue
            lat, lng = float(r["lat"]), float(r["lng"])
            if not (SEOUL_LAT[0] <= lat <= SEOUL_LAT[1] and SEOUL_LNG[0] <= lng <= SEOUL_LNG[1]):
                continue
            pts.add(tuple(round(v, -2) for v in wgs84_to_katec(lat, lng)))
    pts = list(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    dx = math.sqrt(3) * COVER_M; dy = 1.5 * COVER_M
    best = None
    for i in range(steps):
        for j in range(steps):
            ox, oy = dx * i / steps, dy * j / steps
            centers = list(fixed)
            row = 0
            y = min(ys) - dy + oy
            while y <= max(ys) + dy:
                x = min(xs) - dx + ox + (dx / 2 if row % 2 else 0)
                while x <= max(xs) + dx:
                    centers.append((round(x), round(y)))
                    x += dx
                y += dy; row += 1
            cov = [{k for k, p in enumerate(pts) if math.hypot(p[0] - c[0], p[1] - c[1]) <= COVER_M} for c in centers]
            keep = [k for k in range(len(centers)) if cov[k]]
            # 다른 원들이 이미 다 덮는 원은 제거 (작게 덮는 원부터)
            for k in sorted([k for k in keep if k >= len(fixed)], key=lambda k: len(cov[k])):
                others = set().union(*(cov[m] for m in keep if m != k)) if len(keep) > 1 else set()
                if cov[k] <= others:
                    keep.remove(k)
            covered = set().union(*(cov[k] for k in keep))
            if len(covered) < len(pts):
                continue
            if best is None or len(keep) < len(best):
                best = [centers[k] for k in keep]
    Path(out_json).write_text(json.dumps({"cover_m": COVER_M, "points": len(pts), "centers": best}, indent=1))
    print(f"[육각 배치] 커버 대상 {len(pts)}개 위치, 중심점 {len(best)}개 (고정 {len(fixed)}개 포함)")
    for c in best:
        print(c)
