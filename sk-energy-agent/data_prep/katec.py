"""WGS84 ↔ KATEC 좌표 변환 (오피넷 API용, 외부 라이브러리 없이).

KATEC 정의 (proj4):
  +proj=tmerc +lat_0=38 +lon_0=128 +k=0.9999 +x_0=400000 +y_0=600000 +ellps=bessel
  +towgs84=-115.80,474.99,674.11,1.16,-2.31,-1.63,6.43 +units=m
"""

import math

# 타원체
WGS84 = (6378137.0, 1 / 298.257223563)
BESSEL = (6377397.155, 1 / 299.1528128)

# Bessel → WGS84 7-파라미터 (position vector, proj4 towgs84와 같은 부호)
TOWGS84 = (-115.80, 474.99, 674.11, 1.16, -2.31, -1.63, 6.43)

# KATEC 투영 파라미터
LAT0, LON0, K0, X0, Y0 = 38.0, 128.0, 0.9999, 400000.0, 600000.0


def _geo_to_ecef(lat, lon, h, ell):
    a, f = ell
    e2 = f * (2 - f)
    la, lo = math.radians(lat), math.radians(lon)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    return ((n + h) * math.cos(la) * math.cos(lo), (n + h) * math.cos(la) * math.sin(lo), (n * (1 - e2) + h) * math.sin(la))


def _ecef_to_geo(x, y, z, ell):
    a, f = ell
    e2 = f * (2 - f)
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    for _ in range(10):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = p / math.cos(lat) - n
        lat = math.atan2(z, p * (1 - e2 * n / (n + h)))
    return math.degrees(lat), math.degrees(lon)


def _helmert(x, y, z, params, inverse=False):
    dx, dy, dz, rx, ry, rz, ds = params
    rx, ry, rz = (math.radians(v / 3600) for v in (rx, ry, rz))
    s = ds * 1e-6
    if inverse:
        dx, dy, dz, rx, ry, rz, s = -dx, -dy, -dz, -rx, -ry, -rz, -s
    m = 1 + s
    return (
        dx + m * (x - rz * y + ry * z),
        dy + m * (rz * x + y - rx * z),
        dz + m * (-ry * x + rx * y + z),
    )


def _meridian_arc(lat_rad, a, e2):
    e4, e6 = e2 * e2, e2 * e2 * e2
    return a * (
        (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * lat_rad
        - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * lat_rad)
        + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * lat_rad)
        - (35 * e6 / 3072) * math.sin(6 * lat_rad)
    )


def _tm_forward(lat, lon):
    a, f = BESSEL
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    la, lo, la0 = math.radians(lat), math.radians(lon), math.radians(LAT0)
    n = a / math.sqrt(1 - e2 * math.sin(la) ** 2)
    t = math.tan(la) ** 2
    c = ep2 * math.cos(la) ** 2
    A = (lo - math.radians(LON0)) * math.cos(la)
    m, m0 = _meridian_arc(la, a, e2), _meridian_arc(la0, a, e2)
    x = X0 + K0 * n * (A + (1 - t + c) * A ** 3 / 6 + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * A ** 5 / 120)
    y = Y0 + K0 * (m - m0 + n * math.tan(la) * (A * A / 2 + (5 - t + 9 * c + 4 * c * c) * A ** 4 / 24
                                              + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * A ** 6 / 720))
    return x, y


def _tm_inverse(x, y):
    a, f = BESSEL
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    m0 = _meridian_arc(math.radians(LAT0), a, e2)
    m = m0 + (y - Y0) / K0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    t1 = math.tan(phi1) ** 2
    c1 = ep2 * math.cos(phi1) ** 2
    d = (x - X0) / (n1 * K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d * d / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * ep2) * d ** 4 / 24
                                                + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * ep2 - 3 * c1 * c1) * d ** 6 / 720)
    lon = math.radians(LON0) + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                                + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * ep2 + 24 * t1 * t1) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


def wgs84_to_katec(lat, lon):
    x, y, z = _geo_to_ecef(lat, lon, 0.0, WGS84)
    x, y, z = _helmert(x, y, z, TOWGS84, inverse=True)
    blat, blon = _ecef_to_geo(x, y, z, BESSEL)
    return _tm_forward(blat, blon)


def katec_to_wgs84(kx, ky):
    blat, blon = _tm_inverse(kx, ky)
    x, y, z = _geo_to_ecef(blat, blon, 0.0, BESSEL)
    x, y, z = _helmert(x, y, z, TOWGS84)
    return _ecef_to_geo(x, y, z, WGS84)


if __name__ == "__main__":
    lat, lon = 37.5663, 126.9779  # 서울시청 부근
    kx, ky = wgs84_to_katec(lat, lon)
    back = katec_to_wgs84(kx, ky)
    print(f"WGS84 {lat},{lon} → KATEC {kx:.1f},{ky:.1f} → WGS84 {back[0]:.6f},{back[1]:.6f}")
