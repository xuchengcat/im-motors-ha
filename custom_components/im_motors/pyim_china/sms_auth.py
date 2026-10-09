"""Confirmed phone/SMS protocol, with injected transport and device context.

Does not discover credentials, generate push IDs, bind accounts, solve captcha,
or emulate risk tokens. No password endpoint has been established for this sample.
"""
from dataclasses import dataclass
import json
import re
from types import MappingProxyType
from urllib.parse import urlsplit

from .account_auth import (AccountCredentials, CredentialError, PreparedAuthRequest,
                          nonce, validate_headers, _secret, parse_millisecond_time)
from .offline_encryption import encrypted_request_body, decrypted_response_body
from .offline_read_response import _load, _field, ResponseError, DataUnavailable, Presence
from .offline_signature import signature


CAPTCHA_PATH = "/app/login/v3/user/captcha"
SMS_SEND_PATH = "/app/login/v4/user/mobileSMSSend"
SMS_LOGIN_PATH = "/app/login/v4/user/mobileSMSLogin"
_CAPTCHA_CODES = frozenset(("20003", "20009", "22567", "20007"))


class CaptchaRequired(ResponseError):
    pass


class AccountBindingRequired(ResponseError):
    pass


class SmsRejected(ResponseError):
    pass


@dataclass(frozen=True, repr=False)
class SmsDeviceContext:
    baseline_headers: object
    push_device_id: str

    def __post_init__(self):
        object.__setattr__(self, "baseline_headers", validate_headers(self.baseline_headers))
        _secret(self.push_device_id, optional=True)

    def __repr__(self):
        return "SmsDeviceContext(<redacted>)"

    @property
    def login_device_id(self):
        # This is the traced login fallback, not a guessed refresh fallback.
        return self.push_device_id or self.baseline_headers["deviceId"]


@dataclass(frozen=True, repr=False)
class SmsChallenge:
    state: str

    def __repr__(self):
        return "SmsChallenge(<redacted>)"


@dataclass(frozen=True, repr=False)
class ImageChallenge:
    state: str
    png_data: str

    def __repr__(self):
        return "ImageChallenge(<redacted>)"


@dataclass(frozen=True, repr=False)
class SmsLoginResult:
    access_token: str
    refresh_token: str
    expiration_time: int
    suggest_refresh_time: int

    def __repr__(self):
        return "SmsLoginResult(<redacted>)"

    def account_credentials(self, context: SmsDeviceContext, *, vin=""):
        if not context.push_device_id:
            raise CredentialError("Login succeeded but refresh push deviceID is unavailable")
        if not self.refresh_token:
            raise CredentialError("Login succeeded but refresh token is unavailable")
        return AccountCredentials(self.access_token, self.refresh_token,
                                  context.push_device_id, context.baseline_headers,
                                  self.expiration_time, self.suggest_refresh_time, vin)


def _phone(phone):
    if not isinstance(phone, str) or not re.fullmatch(r"1[0-9]{10}", phone):
        raise CredentialError("Expected mainland mobile number")


def _prepare(config, context, path, data, now_ms, request_nonce=None):
    origin = urlsplit(config.base_url)
    if (origin.scheme != "https" or origin.netloc not in ("mh.immotors.com", "mh.immotors.com:443")
            or origin.path not in ("", "/") or origin.query or origin.fragment):
        raise CredentialError("Unexpected login origin")
    if type(now_ms) is not int or now_ms <= 0:
        raise CredentialError("Invalid login time")
    signed = {"x-app-key": "android", "x-nonce": request_nonce or nonce(now_ms),
              "x-timestamp": str(now_ms)}
    _secret(signed["x-nonce"])
    body = None
    if data is not None:
        # Gson serializeNulls + disableHtmlEscaping; encrypt before signing.
        plain = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        body = encrypted_request_body(plain, config.encrypt_key)
        signed["x-encrypt"] = "true"
    method = "GET" if path == CAPTCHA_PATH else "POST"
    headers = dict(context.baseline_headers)
    headers.update({"Content-Type": "application/json;charset=utf-8",
                    "x-transaction-id": nonce(now_ms)})
    headers.update(signed)
    headers["x-signature"] = signature(method, path, signed, config.app_secret, body=body)
    return PreparedAuthRequest(method, config.base_url.rstrip("/") + path,
                               MappingProxyType(headers), body.encode("utf-8") if body is not None else None)


def prepare_sms_send(config, context, phone, *, now_ms, captcha="", state="", request_nonce=None):
    _phone(phone)
    _secret(captcha, optional=True)
    _secret(state, optional=True)
    return _prepare(config, context, SMS_SEND_PATH,
                    {"phoneNumber": phone, "state": state, "captcha": captcha, "smsType": "1"},
                    now_ms, request_nonce)


def prepare_captcha(config, context, *, now_ms):
    return _prepare(config, context, CAPTCHA_PATH, None, now_ms)


def prepare_sms_login(config, context, phone, code, challenge: SmsChallenge, *, now_ms,
                      risk_check=None, request_nonce=None):
    _phone(phone)
    _secret(code)
    _secret(challenge.state)
    if risk_check is not None:
        if not isinstance(risk_check, dict) or set(risk_check) != {"businessId", "token"}:
            raise CredentialError("Invalid legitimate risk context")
        for value in risk_check.values():
            _secret(value)
        risk_check = {**risk_check, "ip": None, "phone": None}
    headers = context.baseline_headers
    data = {"aliDeviceId": context.push_device_id or None, "clientType": "APP",
            "deviceID": context.login_device_id, "deviceType": "Android",
            "openid": "", "phoneManufacturer": (headers["x-brand"] + "_" + headers["x-machine-model"]).replace(" ", ""),
            "phoneNumber": phone, "smsCode": code, "smsStateCode": challenge.state,
            "wxAccessToken": "", "wxUnionid": "", "yidunRiskCheck": risk_check}
    return _prepare(config, context, SMS_LOGIN_PATH, data, now_ms, request_nonce)


def _data(body, headers=None, encrypt_key=None):
    normalized = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not isinstance(value, str) or name.lower() in normalized:
            raise ResponseError("Invalid SMS response headers")
        normalized[name.lower()] = value
    if normalized.get("encrypt", "") or normalized.get("x-encrypt", "") not in ("", "false", "true"):
        raise ResponseError("Unsupported SMS response encryption")
    if normalized.get("x-encrypt") == "true":
        if not isinstance(encrypt_key, str) or not encrypt_key:
            raise ResponseError("SMS response encryption key required")
        _load(body)
        try:
            body = decrypted_response_body(body, encrypt_key)
        except (ValueError, TypeError, UnicodeError):
            raise ResponseError("Invalid encrypted SMS response") from None
    envelope = _load(body)
    code = envelope.get("resultCode")
    if isinstance(code, str) and code in _CAPTCHA_CODES:
        raise CaptchaRequired("Server requires manual image verification")
    if code == "10004":
        raise AccountBindingRequired("Account binding action required; flow stopped")
    if code != "200":
        raise SmsRejected("SMS operation rejected; no automatic retry")
    data = envelope.get("data")
    if data is None:
        raise DataUnavailable("SMS response data unavailable")
    if not isinstance(data, dict):
        raise ResponseError("Invalid SMS response data")
    return data


def parse_sms_challenge(body, *, headers=None, encrypt_key=None):
    value = _field(_data(body, headers, encrypt_key), "smsStateCode", str)
    if value.presence != Presence.VALUE or not value.value:
        raise ResponseError("SMS transaction state required")
    _secret(value.value)
    return SmsChallenge(value.value)


def parse_image_challenge(body, *, headers=None, encrypt_key=None):
    data = _data(body, headers, encrypt_key)
    state, image = _field(data, "state", str), _field(data, "png-data", str)
    if not state.value or not image.value:
        raise ResponseError("Image verification challenge incomplete")
    _secret(state.value)
    return ImageChallenge(state.value, image.value)


def parse_sms_login(body, *, headers=None, encrypt_key=None):
    data = _data(body, headers, encrypt_key)
    def login_field(name, kind):
        try:
            return _field(data, name, kind).value
        except ResponseError:
            # Names are fixed local model keys, never server contents or values.
            raise ResponseError("Invalid login field type: " + name) from None
    # Actual existing token provider reads UserInfoGetBean.token, not accessToken.
    token = login_field("token", str)
    if not token:
        raise ResponseError("Login token required")
    _secret(token)
    refresh = login_field("refreshToken", str) or ""
    _secret(refresh, optional=True)
    expiry = parse_millisecond_time(data, "expirationTime")
    suggestion = parse_millisecond_time(data, "suggestRefreshTime")
    if expiry < 0 or suggestion < 0 or (expiry and suggestion and suggestion >= expiry):
        raise ResponseError("Invalid login time metadata")
    return SmsLoginResult(token, refresh, expiry, suggestion)


class SmsLoginSession:
    """One SMS and one login attempt; captcha may be solved manually by caller."""
    def __init__(self, config, context, phone, transport):
        _phone(phone)
        self.config, self.context, self.phone, self.transport = config, context, phone, transport
        self.challenge = None
        self._send_attempted = False
        self._login_attempted = False
        self.login_response_received = False

    def __repr__(self):
        return "SmsLoginSession(<redacted>)"

    def send_sms(self, now_ms, *, captcha="", state=""):
        if self._send_attempted:
            raise CredentialError("SMS already attempted; no repeat send")
        self._send_attempted = True
        response = self.transport.send(prepare_sms_send(self.config, self.context, self.phone,
            now_ms=now_ms, captcha=captcha, state=state))
        self.challenge = parse_sms_challenge(response.body, headers=response.headers,
                                              encrypt_key=self.config.encrypt_key)
        return self.challenge

    def login(self, code, now_ms, *, risk_check=None, capture_response=None):
        if self.challenge is None or self._login_attempted:
            raise CredentialError("SMS state unavailable or login already attempted")
        self._login_attempted = True
        response = self.transport.send(prepare_sms_login(self.config, self.context, self.phone,
            code, self.challenge, now_ms=now_ms, risk_check=risk_check))
        self.login_response_received = True
        if capture_response is not None:
            capture_response(response)
        return parse_sms_login(response.body, headers=response.headers,
                               encrypt_key=self.config.encrypt_key)
