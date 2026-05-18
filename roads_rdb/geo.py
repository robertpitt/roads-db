from __future__ import annotations

import math


def wgs84_to_bng(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    """Convert WGS84 latitude/longitude to British National Grid east/north.

    This implements the standard WGS84 -> OSGB36 Helmert transform followed by
    the Airy 1830 Transverse Mercator projection used by EPSG:27700. It is good
    enough for routing snap candidates; production code should still validate
    against a geodetic library.
    """

    x, y, z = _geodetic_to_cartesian(
        math.radians(lat_deg),
        math.radians(lon_deg),
        0.0,
        a=6378137.0,
        b=6356752.3141,
    )
    x, y, z = _helmert_wgs84_to_osgb36(x, y, z)
    lat, lon = _cartesian_to_geodetic(x, y, z, a=6377563.396, b=6356256.909)
    return _osgb36_to_bng(lat, lon)


def parse_lat_lon(text: str) -> tuple[float, float]:
    parts = [float(part.strip()) for part in text.split(",")]
    if len(parts) != 2:
        raise ValueError("coordinate must be lat,lon")
    return parts[0], parts[1]


def _geodetic_to_cartesian(lat: float, lon: float, height: float, *, a: float, b: float) -> tuple[float, float, float]:
    e2 = (a * a - b * b) / (a * a)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    nu = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
    x = (nu + height) * cos_lat * math.cos(lon)
    y = (nu + height) * cos_lat * math.sin(lon)
    z = ((1 - e2) * nu + height) * sin_lat
    return x, y, z


def _helmert_wgs84_to_osgb36(x: float, y: float, z: float) -> tuple[float, float, float]:
    tx = -446.448
    ty = 125.157
    tz = -542.060
    rx = math.radians(-0.1502 / 3600.0)
    ry = math.radians(-0.2470 / 3600.0)
    rz = math.radians(-0.8421 / 3600.0)
    scale = 20.4894e-6

    x2 = tx + (1 + scale) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + scale) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + scale) * z
    return x2, y2, z2


def _cartesian_to_geodetic(x: float, y: float, z: float, *, a: float, b: float) -> tuple[float, float]:
    e2 = (a * a - b * b) / (a * a)
    p = math.hypot(x, y)
    lat = math.atan2(z, p * (1 - e2))
    while True:
        sin_lat = math.sin(lat)
        nu = a / math.sqrt(1 - e2 * sin_lat * sin_lat)
        next_lat = math.atan2(z + e2 * nu * sin_lat, p)
        if abs(next_lat - lat) < 1e-12:
            lat = next_lat
            break
        lat = next_lat
    lon = math.atan2(y, x)
    return lat, lon


def _osgb36_to_bng(lat: float, lon: float) -> tuple[float, float]:
    a = 6377563.396
    b = 6356256.909
    f0 = 0.9996012717
    lat0 = math.radians(49.0)
    lon0 = math.radians(-2.0)
    n0 = -100000.0
    e0 = 400000.0

    e2 = 1 - (b * b) / (a * a)
    n = (a - b) / (a + b)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    tan_lat = math.tan(lat)
    nu = a * f0 / math.sqrt(1 - e2 * sin_lat * sin_lat)
    rho = a * f0 * (1 - e2) / ((1 - e2 * sin_lat * sin_lat) ** 1.5)
    eta2 = nu / rho - 1

    m = b * f0 * (
        (1 + n + 1.25 * n * n + 1.25 * n**3) * (lat - lat0)
        - (3 * n + 3 * n * n + 21 / 8 * n**3) * math.sin(lat - lat0) * math.cos(lat + lat0)
        + (15 / 8 * n * n + 15 / 8 * n**3) * math.sin(2 * (lat - lat0)) * math.cos(2 * (lat + lat0))
        - (35 / 24 * n**3) * math.sin(3 * (lat - lat0)) * math.cos(3 * (lat + lat0))
    )

    dlon = lon - lon0
    i = m + n0
    ii = nu / 2 * sin_lat * cos_lat
    iii = nu / 24 * sin_lat * cos_lat**3 * (5 - tan_lat**2 + 9 * eta2)
    iiia = nu / 720 * sin_lat * cos_lat**5 * (61 - 58 * tan_lat**2 + tan_lat**4)
    iv = nu * cos_lat
    v = nu / 6 * cos_lat**3 * (nu / rho - tan_lat**2)
    vi = nu / 120 * cos_lat**5 * (5 - 18 * tan_lat**2 + tan_lat**4 + 14 * eta2 - 58 * tan_lat**2 * eta2)

    northing = i + ii * dlon**2 + iii * dlon**4 + iiia * dlon**6
    easting = e0 + iv * dlon + v * dlon**3 + vi * dlon**5
    return easting, northing

