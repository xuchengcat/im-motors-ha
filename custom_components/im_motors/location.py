"""Validate cached vehicle coordinates and convert the observed GCJ-02 format.

App 3.2.4 passes period coordinates directly to AMap's autonavi reverse geocoder.
Only the observed coOdntSysFmt=1 is supported; its GCJ-02 interpretation is based
on that call chain, not an independently documented manufacturer enum.
"""
import math
import re

_DECIMAL = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")


def _number(value):
    if isinstance(value, str):
        if len(value) > 32 or not _DECIMAL.fullmatch(value):
            return None
        value = float(value)
    if type(value) not in (float, int):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def location_status(snapshot):
    if snapshot is None:
        return "missing"
    fields = snapshot.location_fields
    values = [fields.get(k) for k in ("latitude", "longitude", "coOdntSysFmt")]
    if any(v is None or v.presence.value in ("missing", "null") for v in values):
        return "missing"
    lat, lon = (_number(v.value) for v in values[:2])
    if (lat is None or lon is None or not -90 <= lat <= 90 or
            not -180 <= lon <= 180 or lat == 0 or lon == 0):
        return "invalid"
    code = values[2].value
    if type(code) is not int or code != 1:
        return "unsupported_coordinate_system"
    return "available"


def coordinates(snapshot):
    if location_status(snapshot) != "available":
        return None
    lat = _number(snapshot.location_fields["latitude"].value)
    lon = _number(snapshot.location_fields["longitude"].value)
    return gcj02_to_wgs84(lat, lon)


def _offset(lat, lon):
    x, y = lon - 105.0, lat - 35.0
    base = (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    dlat = (-100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x)) + base
            + (20*math.sin(y*math.pi) + 40*math.sin(y*math.pi/3))*2/3
            + (160*math.sin(y*math.pi/12) + 320*math.sin(y*math.pi/30))*2/3)
    dlon = (300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x)) + base
            + (20*math.sin(x*math.pi) + 40*math.sin(x*math.pi/3))*2/3
            + (150*math.sin(x*math.pi/12) + 300*math.sin(x*math.pi/30))*2/3)
    rad = lat * math.pi / 180
    eccentricity = 0.00669342162296594323
    magic = 1 - eccentricity * math.sin(rad)**2
    scale = math.sqrt(magic)
    dlat *= 180 / ((6378245.0*(1-eccentricity)/(magic*scale))*math.pi)
    dlon *= 180 / ((6378245.0/scale*math.cos(rad))*math.pi)
    return dlat, dlon


def gcj02_to_wgs84(lat, lon):
    """Invert the usual GCJ offset locally; do not claim GPS accuracy."""
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return lat, lon
    wlat, wlon = lat, lon
    for _ in range(8):
        dlat, dlon = _offset(wlat, wlon)
        error_lat, error_lon = wlat+dlat-lat, wlon+dlon-lon
        wlat -= error_lat
        wlon -= error_lon
        if max(abs(error_lat), abs(error_lon)) < 1e-8:
            break
    return wlat, wlon
