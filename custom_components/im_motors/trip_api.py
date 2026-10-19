"""AMap reverse geocoding; no vehicle-owner login or driving-calendar API."""
import http.client
import ssl
from urllib.parse import urlencode
from .pyim_china.offline_read_response import _load


class TripError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def https_get(host, path, headers=None):
    connection = http.client.HTTPSConnection(host, timeout=25, context=ssl.create_default_context())
    try:
        connection.request("GET", path, headers=dict(headers or {}))
        result = connection.getresponse()
        raw = result.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise TripError("response_too_large")
        if result.status in (401, 403):
            raise TripError("authentication_failed")
        if result.status != 200:
            raise TripError("http_error")
        return raw.decode("utf-8"), {k.lower(): v for k, v in result.getheaders()}
    except TripError:
        raise
    except Exception:
        raise TripError("network_error") from None
    finally:
        connection.close()


class AmapGeocoder:
    def __init__(self, key, *, get=https_get):
        self.key = key
        self.get = get

    def reverse(self, latitude, longitude):
        query = urlencode({"key": self.key, "location": f"{longitude:.6f},{latitude:.6f}",
                           "radius": 200, "extensions": "all", "coordsys": "autonavi", "output": "JSON"})
        body, _ = self.get("restapi.amap.com", "/v3/geocode/regeo?" + query)
        try:
            data = _load(body)
            if data.get("status") != "1":
                raise TripError("amap_rejected")
            result = data["regeocode"]
            address = result.get("formatted_address")
            if not isinstance(address, str) or not address:
                raise TripError("amap_no_address")
            # Keep both full address and nearest POI so comparison is explicit.
            pois = result.get("pois") or []
            poi = next((p.get("name") for p in pois if isinstance(p, dict) and isinstance(p.get("name"), str)), None)
            return {"address": address, "poi": poi}
        except TripError:
            raise
        except Exception:
            raise TripError("amap_invalid_response") from None
