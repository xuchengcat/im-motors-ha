"""Prepare and parse statically confirmed GET operations without I/O.

This is the request-building portion of the future read SDK. Caller-supplied
baseline headers still need device/version provenance checks. A prepared request
does not establish server-side no-wake behavior or authentication success.
"""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
from urllib.parse import quote, urlsplit

from .local_production_config import LocalProductionConfig
from .offline_signature import signature
from .offline_read_response import parse_read_response


@dataclass(frozen=True, repr=False)
class PreparedReadRequest:
    method: str
    url: str
    headers: Mapping[str, str]

    def __repr__(self):
        return "PreparedReadRequest(<redacted>)"


class OfflineReadClient:
    _paths = {
        "vehicles": "/app/capp-vus/v3/vehicle/info/allVehicle",
        "last_used": "/app/capp-vus/v3/vehicle/info/lastUse",
        "management": "/app/vus/v4/vehicle/management",
        "isc_vehicles": "/app/vus/v3/isc/scene/vehicleList",
        "category": "/app/capp-vus/v3/vehicle/category/all",
    }

    def __init__(self, config: LocalProductionConfig):
        self._config = config
        origin = urlsplit(config.base_url)
        if origin.scheme != "https" or origin.hostname != "mh.immotors.com" or origin.username or origin.password or origin.query or origin.fragment or origin.path not in ("", "/"):
            raise ValueError("Unexpected service origin")

    def __repr__(self):
        return "OfflineReadClient(<redacted>)"

    def parse_response(self, operation: str, wire_body: str, *,
                       response_headers: Mapping[str, str] | None = None):
        """Parse an already supplied body with the in-memory configured key.

        The future transport must handle HTTP status and authorization itself.
        This method does not send a request or refresh credentials.
        """
        return parse_read_response(operation, wire_body,
                                   response_headers=response_headers,
                                   encrypt_key=self._config.encrypt_key)

    def prepare(self, operation: str, *, nonce: str, timestamp_ms: int,
                baseline_headers: Mapping[str, str], vin: str | None = None) -> PreparedReadRequest:
        if operation not in self._paths:
            raise ValueError("Unsupported read operation")
        if not isinstance(nonce, str) or not nonce or "\r" in nonce or "\n" in nonce:
            raise ValueError("Invalid nonce")
        if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int) or timestamp_ms <= 0:
            raise ValueError("Invalid timestamp")
        if operation == "category":
            if not isinstance(vin, str) or not vin:
                raise ValueError("VIN required for category read")
            query = "vin=" + quote(vin, safe="", encoding="utf-8")
        else:
            if vin is not None:
                raise ValueError("Vehicle lists do not accept a VIN parameter")
            query = None
        headers = {}
        seen = set()
        reserved = {"x-app-key", "x-nonce", "x-timestamp", "x-signature", "encrypt"}
        for name, value in baseline_headers.items():
            if not isinstance(name, str) or not isinstance(value, str) or not name or any(c in name + value for c in "\r\n"):
                raise ValueError("Invalid baseline header")
            lower = name.lower()
            if lower in seen or lower in reserved:
                raise ValueError("Duplicate or reserved baseline header")
            seen.add(lower)
            headers["x-encrypt" if lower == "x-encrypt" else name] = value
        signed = {"x-app-key": "android", "x-nonce": nonce,
                  "x-timestamp": str(timestamp_ms)}
        for name, value in headers.items():
            if name.lower() == "x-encrypt":
                signed["x-encrypt"] = value
        headers.update(signed)
        path = self._paths[operation]
        headers["x-signature"] = signature("GET", path, signed,
                                             self._config.app_secret, query)
        url = self._config.base_url.rstrip("/") + path
        if query:
            url += "?" + query
        return PreparedReadRequest("GET", url, MappingProxyType(headers))
