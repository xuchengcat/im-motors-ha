"""Direct verified HTTPS; only account refresh is enabled by default.

No redirect, proxy environment, retry, cookies, logging or credential discovery.
Account vehicle metadata permissions are separate; telemetry stays disabled by default.
"""
from dataclasses import dataclass
import http.client
import re
import ssl
from types import MappingProxyType
from urllib.parse import parse_qsl, urlsplit

from .account_auth import REFRESH_PATH


class TransportError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class HttpResponse:
    status: int
    headers: object
    body: str

    def __repr__(self):
        return "HttpResponse(<redacted>)"


_READ_PATHS = frozenset(("/app/capp-vus/v3/vehicle/info/allVehicle",
                         "/app/capp-vus/v3/vehicle/info/lastUse",
                         "/app/vus/v4/vehicle/management",
                         "/app/vus/v3/isc/scene/vehicleList",
                         "/app/capp-vus/v3/vehicle/category/all"))
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


class RestrictedHttpsTransport:
    def __init__(self, *, enable_sms_login=False, enable_vehicle_list=False, enable_last_used=False,
                 enable_management=False,
                 enable_isc_vehicles=False,
                 enable_vehicle_reads=False, allowed_vin=None,
                 timeout=15, max_response_bytes=2 * 1024 * 1024, capture_http_errors=False):
        if enable_vehicle_reads and not isinstance(allowed_vin, str):
            raise TransportError("Owned vehicle scope required")
        self.enable_vehicle_reads = enable_vehicle_reads
        self.enable_sms_login = enable_sms_login
        self.enable_vehicle_list = enable_vehicle_list
        self.enable_last_used = enable_last_used
        self.enable_management = enable_management
        self.enable_isc_vehicles = enable_isc_vehicles
        self.capture_http_errors = capture_http_errors
        self.allowed_vin = allowed_vin
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes

    def __repr__(self):
        return "RestrictedHttpsTransport(<redacted>)"

    def _validate(self, request):
        url = urlsplit(request.url)
        if (url.scheme != "https" or url.netloc not in ("mh.immotors.com", "mh.immotors.com:443")
                or url.fragment):
            raise TransportError("Unexpected request origin")
        body = getattr(request, "body", None)
        if request.method == "POST" and url.path == REFRESH_PATH:
            if url.query or not isinstance(body, bytes) or not body:
                raise TransportError("Invalid refresh request")
        elif url.path in ("/app/login/v3/user/captcha", "/app/login/v4/user/mobileSMSSend",
                          "/app/login/v4/user/mobileSMSLogin"):
            if not self.enable_sms_login or url.query:
                raise TransportError("SMS login transport disabled or invalid query")
            if url.path.endswith("/captcha"):
                if request.method != "GET" or body is not None:
                    raise TransportError("Invalid captcha request")
            elif request.method != "POST" or not isinstance(body, bytes) or not body:
                raise TransportError("Invalid SMS request")
        elif request.method == "GET" and url.path in _READ_PATHS:
            if not (self.enable_vehicle_reads or
                    (self.enable_vehicle_list and url.path == "/app/capp-vus/v3/vehicle/info/allVehicle") or
                    (self.enable_management and url.path == "/app/vus/v4/vehicle/management") or
                    (self.enable_isc_vehicles and url.path == "/app/vus/v3/isc/scene/vehicleList") or
                    (self.enable_last_used and url.path == "/app/capp-vus/v3/vehicle/info/lastUse")):
                raise TransportError("Vehicle reads disabled pending no-wake assessment")
            if body is not None:
                raise TransportError("GET body prohibited")
            if url.path.endswith("/category/all"):
                if parse_qsl(url.query, keep_blank_values=True) != [("vin", self.allowed_vin)] or not self.allowed_vin:
                    raise TransportError("Request outside owned vehicle scope")
            elif url.query:
                raise TransportError("Unexpected vehicle list query")
        else:
            raise TransportError("Operation outside allowed protocol")
        seen = set()
        for name, value in request.headers.items():
            if (not isinstance(name, str) or not _HEADER_NAME.fullmatch(name)
                    or not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or name.lower() in seen or name.lower() in ("host", "content-length", "transfer-encoding")):
                raise TransportError("Invalid outgoing header")
            seen.add(name.lower())
            try:
                value.encode("latin-1")
            except UnicodeError:
                raise TransportError("Invalid outgoing header encoding") from None
        return url, body

    def send(self, request):
        url, body = self._validate(request)
        connection = None
        try:
            connection = http.client.HTTPSConnection("mh.immotors.com", 443,
                timeout=self.timeout, context=ssl.create_default_context())
            target = url.path + ("?" + url.query if url.query else "")
            connection.request(request.method, target, body=body, headers=dict(request.headers))
            response = connection.getresponse()
            # Refuse 3xx without issuing another request or forwarding credentials.
            if not 200 <= response.status < 300 and not (self.capture_http_errors and 400 <= response.status < 600):
                raise TransportError(f"HTTP status {response.status}; no automatic retry")
            raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise TransportError("Response size limit exceeded")
            headers = {}
            for name, value in response.getheaders():
                lower = name.lower()
                if lower in headers and lower in ("x-encrypt", "encrypt", "content-encoding"):
                    raise TransportError("Ambiguous response metadata")
                headers[lower] = value
            if headers.get("content-encoding", "identity") != "identity":
                raise TransportError("Unsupported response encoding")
            return HttpResponse(response.status, MappingProxyType(headers), raw.decode("utf-8"))
        except TransportError:
            raise
        except (OSError, http.client.HTTPException, UnicodeError, ValueError):
            raise TransportError("HTTPS request failed; outcome may be unknown; no retry") from None
        finally:
            if connection is not None:
                connection.close()
