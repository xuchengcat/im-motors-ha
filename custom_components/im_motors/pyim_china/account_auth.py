"""Account refresh protocol and lifecycle; transport/storage are injected.

No credential discovery, SMS delivery, vehicle calls or automatic failure retry.
Only explicitly supplied account credentials and observed device headers are used.
"""
from dataclasses import dataclass, replace
from enum import Enum
import json
import re
import secrets
from threading import Lock
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from .offline_signature import signature
from .offline_read_response import (
    _load, _field, Presence, ResponseError, AuthenticationExpired,
    BusinessResponseError, DataUnavailable,
)
from .offline_encryption import decrypted_response_body


REFRESH_PATH = "/app/login/user/authorize/refresh-token"
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz1234567890"
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_BASELINE = frozenset(("versionType", "deviceType", "x-brand", "x-machine-model",
                      "x-system-version", "deviceId", "x-device-id",
                      "versionNumber", "versionCode"))


class CredentialError(ValueError):
    pass


class RefreshDecision(str, Enum):
    CURRENT = "current"
    DUE = "due"
    METADATA_UNKNOWN = "metadata_unknown"
    REAUTH_REQUIRED = "reauth_required"


def validate_headers(headers: Mapping[str, str]):
    result = {}
    seen = set()
    for name, value in headers.items():
        if (not isinstance(name, str) or not _HEADER_NAME.fullmatch(name)
                or not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value)):
            raise CredentialError("Invalid account header")
        try:
            value.encode("latin-1")
        except UnicodeError:
            raise CredentialError("Invalid account header encoding") from None
        if name.lower() in seen or name not in _BASELINE:
            raise CredentialError("Duplicate or unsupported account header")
        seen.add(name.lower())
        result[name] = value
    # ju.c.c returns an empty Android ID when unavailable; do not invent one.
    if set(result) != _BASELINE or any(not value for name, value in result.items() if name != "x-device-id"):
        raise CredentialError("Client device and app headers required")
    if result["versionType"] != "ANDROID" or result["deviceType"] != "android":
        raise CredentialError("Unexpected account platform")
    return MappingProxyType(result)


def _secret(value, *, optional=False):
    if not isinstance(value, str) or (not optional and not value) or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise CredentialError("Invalid credential value")
    try:
        value.encode("ascii")
    except UnicodeError:
        raise CredentialError("Invalid credential encoding") from None


def parse_millisecond_time(data, name):
    """Gson long metadata: integer or ASCII decimal string, never bool/float.

    Numeric strings observed in the authorized SMS login response. No dates,
    exponent notation, whitespace, implicit seconds conversion or overflow.
    """
    value = data.get(name)
    if value is None:
        return 0
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        value = int(value)
    if type(value) is not int or not 0 <= value <= 9223372036854775807:
        raise ResponseError("Invalid authentication time metadata: " + name)
    return value


@dataclass(frozen=True, repr=False)
class AccountCredentials:
    access_token: str
    refresh_token: str
    push_device_id: str
    baseline_headers: Mapping[str, str]
    expiration_time: int = 0
    suggest_refresh_time: int = 0
    vin: str = ""

    def __post_init__(self):
        for value in (self.access_token, self.refresh_token, self.push_device_id):
            _secret(value)
        _secret(self.vin, optional=True)
        if self.access_token.lower().startswith("bearer "):
            raise CredentialError("Supply access token without Bearer prefix")
        for value in (self.expiration_time, self.suggest_refresh_time):
            if type(value) is not int or value < 0:
                raise CredentialError("Invalid credential time metadata")
        if (self.expiration_time and self.suggest_refresh_time
                and self.suggest_refresh_time >= self.expiration_time):
            raise CredentialError("Inconsistent credential time metadata")
        object.__setattr__(self, "baseline_headers", validate_headers(self.baseline_headers))

    def __repr__(self):
        return "AccountCredentials(<redacted>)"

    @property
    def refresh_device_id(self):
        """Observed Android profile uses its push registration for this field."""
        return self.push_device_id

    def refresh_decision(self, now_ms: int):
        if type(now_ms) is not int or now_ms <= 0:
            raise CredentialError("Invalid current time")
        if self.expiration_time > 0 and now_ms >= self.expiration_time:
            return RefreshDecision.REAUTH_REQUIRED
        if self.expiration_time <= 0 or self.suggest_refresh_time <= 0:
            return RefreshDecision.METADATA_UNKNOWN
        return RefreshDecision.DUE if now_ms > self.suggest_refresh_time else RefreshDecision.CURRENT

    def request_headers(self, now_ms: int):
        headers = dict(self.baseline_headers)
        headers.update({"Authorization": "Bearer " + self.access_token,
                        "Content-Type": "application/json;charset=utf-8",
                        "x-transaction-id": nonce(now_ms)})
        return headers


def nonce(now_ms: int) -> str:
    return str(now_ms) + "@" + "".join(secrets.choice(_ALPHABET) for _ in range(15))


@dataclass(frozen=True, repr=False)
class PreparedAuthRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: bytes | None

    def __repr__(self):
        return "PreparedAuthRequest(<redacted>)"


def prepare_refresh(config, credentials: AccountCredentials, *, now_ms: int,
                    request_nonce: str | None = None):
    origin = urlsplit(config.base_url)
    if (origin.scheme != "https" or origin.netloc not in ("mh.immotors.com", "mh.immotors.com:443")
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise CredentialError("Unexpected authentication origin")
    credentials.refresh_decision(now_ms)  # Validate the clock even for manual preparation.
    request_nonce = request_nonce or nonce(now_ms)
    _secret(request_nonce)
    body = json.dumps({"refreshToken": credentials.refresh_token,
                       "deviceID": credentials.refresh_device_id, "clientType": "APP"},
                      ensure_ascii=False, separators=(",", ":"))
    signed = {"x-app-key": "android", "x-nonce": request_nonce,
              "x-timestamp": str(now_ms)}
    headers = credentials.request_headers(now_ms)
    headers.update(signed)
    headers["x-signature"] = signature("POST", REFRESH_PATH, signed,
                                       config.app_secret, body=body)
    return PreparedAuthRequest("POST", config.base_url.rstrip("/") + REFRESH_PATH,
                               MappingProxyType(headers), body.encode("utf-8"))


def parse_refresh_response(previous: AccountCredentials, body: str, *, headers=None,
                           encrypt_key: str | None = None):
    normalized = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or name.lower() in normalized:
            raise ResponseError("Invalid refresh response headers")
        normalized[name.lower()] = value
    if normalized.get("encrypt", "") or normalized.get("x-encrypt", "") not in ("", "false", "true"):
        raise ResponseError("Unsupported refresh response encryption")
    if normalized.get("x-encrypt") == "true":
        if not isinstance(encrypt_key, str) or not encrypt_key:
            raise ResponseError("Refresh response encryption key required")
        _load(body)
        try:
            body = decrypted_response_body(body, encrypt_key)
        except (ValueError, TypeError, UnicodeError):
            raise ResponseError("Invalid encrypted refresh response") from None
    envelope = _load(body)
    code = envelope.get("resultCode")
    if code in ("22061", "invalid_token"):
        raise AuthenticationExpired("Authentication expired; sign in required")
    if not isinstance(code, str) or not code:
        raise ResponseError("Invalid refresh result code")
    if code != "200":
        raise BusinessResponseError("Account refresh failed")
    data = envelope.get("data")
    if data is None:
        raise DataUnavailable("Refresh data unavailable")
    if not isinstance(data, dict):
        raise ResponseError("Invalid refresh data")
    access = _field(data, "accessToken", str)
    if access.presence != Presence.VALUE or not access.value:
        raise ResponseError("Refresh access token required")
    refresh = _field(data, "refreshToken", str)
    expiry = parse_millisecond_time(data, "expirationTime")
    suggestion = parse_millisecond_time(data, "suggestRefreshTime")
    # App preserves old nonempty refresh token and positive time metadata.
    return replace(previous, access_token=access.value,
                   refresh_token=refresh.value or previous.refresh_token,
                   expiration_time=(expiry if expiry > 0
                                    else previous.expiration_time),
                   suggest_refresh_time=(suggestion if suggestion > 0
                                         else previous.suggest_refresh_time))


class AccountSession:
    """Serialize refresh, persist rotations, stop after uncertain/failing refresh.

    Conservative SDK policy rather than a claim to reproduce every App flow.
    A failed attempt is latched until a new session is deliberately created.
    """
    def __init__(self, config, credentials, transport, persist):
        self.config = config
        self.credentials = credentials
        self.transport = transport
        self.persist = persist
        self._lock = Lock()
        self._failed = False
        self._last_refresh_ms = None

    def __repr__(self):
        return "AccountSession(<redacted>)"

    def refresh_if_due(self, now_ms: int, *, force=False):
        with self._lock:
            if self._failed:
                raise CredentialError("Previous refresh failed; inspect before retrying")
            decision = self.credentials.refresh_decision(now_ms)
            if decision == RefreshDecision.REAUTH_REQUIRED:
                raise AuthenticationExpired("Credential metadata expired; sign in required")
            if not force and self._last_refresh_ms is not None and now_ms <= self._last_refresh_ms:
                return False
            if not force and decision == RefreshDecision.CURRENT:
                return False
            if not force and decision == RefreshDecision.METADATA_UNKNOWN:
                raise CredentialError("Refresh metadata unknown; explicit refresh required")
            request = prepare_refresh(self.config, self.credentials, now_ms=now_ms)
            try:
                response = self.transport.send(request)
                updated = parse_refresh_response(self.credentials, response.body,
                                                  headers=response.headers,
                                                  encrypt_key=self.config.encrypt_key)
                # Keep rotated values in memory even if persistence fails, but stop.
                self.credentials = updated
                self.persist(updated)
                self._last_refresh_ms = now_ms
                if updated.refresh_decision(now_ms) == RefreshDecision.REAUTH_REQUIRED:
                    raise AuthenticationExpired("Refreshed credential metadata expired")
            except Exception:
                self._failed = True
                raise
            return True
