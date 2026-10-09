"""UI-driven SMS authentication with protected, durable request state.

No phone or code is stored in HA entries. Uncertain requests never replay.
Uses bundled protocol parameters or an existing protected protocol override.
"""
import hashlib
from pathlib import Path
import platform
import re
import time
import uuid

from .account_auth import CredentialError, validate_headers
from .credential_store import key_context, vault_for_path
from .local_production_config import load_local_production_config
from .restricted_transport import RestrictedHttpsTransport
from .runtime_lock import AccountFileLock
from .sms_auth import (SmsDeviceContext, SmsChallenge, prepare_sms_send,
                       prepare_sms_login, parse_sms_challenge, parse_sms_login,
                       CaptchaRequired, AccountBindingRequired, SmsRejected)

CHALLENGE_LIFETIME_MS = 10 * 60 * 1000
RESEND_INTERVAL_MS = 60 * 1000
LOGIN_BLOCKING_STATUSES = frozenset(("send_attempted", "sms_response_received", "awaiting_code",
    "login_attempted", "login_response_received", "login_response_parse_failed", "request_unknown"))


class SmsLoginFailure(RuntimeError):
    def __init__(self, error):
        # Only local fixed error identifiers may be shown by a config flow.
        self.error = error
        super().__init__("SMS authentication stopped")


class HaSmsLogin:
    def __init__(self, data_dir, key_file, *, transport_factory=RestrictedHttpsTransport,
                 clock=lambda: time.time_ns() // 1_000_000):
        self.data_dir, self.key_file = Path(data_dir), Path(key_file)
        self.transport_factory, self.clock = transport_factory, clock
        self.phone = None
        self.attempt_id = None

    def __repr__(self):
        return "HaSmsLogin(<redacted>)"

    def _vault(self, name):
        return vault_for_path(self.data_dir / name)

    def _load_context(self):
        if not self.data_dir.is_absolute() or not self.key_file.is_absolute():
            raise SmsLoginFailure("invalid_vault")
        config = load_local_production_config(self.data_dir / "production.imvault")
        vault = self._vault("device.imvault")
        if vault.path.exists():
            device = vault._load_payload()
            if (device.get("kind") != "sms-device" or device.get("schema_version") != 1
                    or device.get("push_device_id") != ""):
                raise SmsLoginFailure("invalid_vault")
            context = SmsDeviceContext(device["baseline_headers"], "")
        else:
            context = SmsDeviceContext({"versionType": "ANDROID", "deviceType": "android",
                "x-brand": "IMHA", "x-machine-model": "StandaloneSDK",
                "x-system-version": "Python=" + platform.python_version() + ";OS=" + platform.system(),
                "deviceId": "9" + uuid.uuid4().hex, "x-device-id": "",
                "versionNumber": "3.2.4", "versionCode": "353"}, "")
            vault._save_payload({"schema_version": 1, "kind": "sms-device",
                "baseline_headers": dict(context.baseline_headers), "push_device_id": ""})
        session_vault = self._vault("session.imvault")
        if session_vault.path.exists():
            session = session_vault._load_payload()
            if (session.get("pending_refresh_result") or
                    dict(validate_headers(session["baseline_headers"])) != dict(context.baseline_headers)):
                raise SmsLoginFailure("unresolved_request")
        return config, context

    def _state(self):
        vault = self._vault("ha-login.imvault")
        if not vault.path.exists():
            return None
        state = vault._load_payload()
        if state.get("kind") != "ha-sms-login" or state.get("schema_version") != 1:
            raise SmsLoginFailure("invalid_vault")
        return state

    def prepare(self):
        """Offline setup check and stable device creation; never sends SMS."""
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                _, context = self._load_context()
                return hashlib.sha256(context.login_device_id.encode("ascii")).hexdigest()
        except SmsLoginFailure:
            raise
        except Exception:
            raise SmsLoginFailure("invalid_vault") from None

    @staticmethod
    def valid_phone(phone):
        return isinstance(phone, str) and re.fullmatch(r"1[0-9]{10}", phone) is not None

    @staticmethod
    def valid_code(code):
        return isinstance(code, str) and re.fullmatch(r"[0-9]{4,8}", code) is not None

    @staticmethod
    def _fingerprint(phone, context):
        return hashlib.sha256((context.login_device_id + ":" + phone).encode("ascii")).hexdigest()

    def start(self, phone, *, resend=False):
        """Explicit user request; reuse a retained challenge without another SMS."""
        if not self.valid_phone(phone):
            raise SmsLoginFailure("invalid_phone")
        self.phone = phone
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                config, context = self._load_context()
                now = self.clock()
                state = self._state()
                fingerprint = self._fingerprint(phone, context)
                if state:
                    status = state["status"]
                    age = now - state["sent_time_ms"]
                    if status in ("login_response_received", "login_response_parse_failed"):
                        if state["phone_fingerprint"] != fingerprint:
                            raise SmsLoginFailure("unresolved_request")
                        self._commit(state, config, context)
                        return True
                    if status in ("send_attempted", "login_attempted", "request_unknown", "sms_response_received"):
                        raise SmsLoginFailure("unresolved_request")
                    if status == "awaiting_code" and state["phone_fingerprint"] == fingerprint:
                        if not resend and 0 <= age < CHALLENGE_LIFETIME_MS:
                            self.attempt_id = state["attempt_id"]
                            return False
                    if age < RESEND_INTERVAL_MS:
                        raise SmsLoginFailure("sms_cooldown")
                session_path = self.data_dir / "session.imvault"
                state = {"schema_version": 1, "kind": "ha-sms-login", "status": "send_attempted",
                    "attempt_id": uuid.uuid4().hex, "sent_time_ms": now,
                    "phone_fingerprint": fingerprint,
                    "source_session_digest": hashlib.sha256(session_path.read_bytes()).hexdigest()
                        if session_path.exists() else None}
                self.attempt_id = state["attempt_id"]
                self._vault("ha-login.imvault")._save_payload(state)
                request = prepare_sms_send(config, context, phone, now_ms=now)
                try:
                    response = self.transport_factory(enable_sms_login=True).send(request)
                except Exception:
                    state["status"] = "request_unknown"
                    self._vault("ha-login.imvault")._save_payload(state)
                    raise SmsLoginFailure("unresolved_request") from None
                state.update(status="sms_response_received", sms_response_body=response.body,
                    sms_response_headers=self._headers(response))
                self._vault("ha-login.imvault")._save_payload(state)
                try:
                    challenge = parse_sms_challenge(response.body, headers=state["sms_response_headers"],
                        encrypt_key=config.encrypt_key)
                except (CaptchaRequired, AccountBindingRequired, SmsRejected) as error:
                    state["status"] = "sms_rejected"
                    self._vault("ha-login.imvault")._save_payload(state)
                    raise SmsLoginFailure(self._error(error, "sms_rejected")) from None
                state.update(status="awaiting_code", sms_state_code=challenge.state)
                self._vault("ha-login.imvault")._save_payload(state)
                return False
        except SmsLoginFailure:
            raise
        except Exception:
            raise SmsLoginFailure("unresolved_request") from None

    @staticmethod
    def _headers(response):
        return {key.lower(): value for key, value in response.headers.items()
                if key.lower() in ("x-encrypt", "encrypt")}

    @staticmethod
    def _error(error, rejected):
        if isinstance(error, CaptchaRequired):
            return "captcha_required"
        if isinstance(error, AccountBindingRequired):
            return "account_binding_required"
        return rejected

    def login(self, code):
        if not self.valid_code(code):
            raise SmsLoginFailure("invalid_code")
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                config, context = self._load_context()
                state = self._state()
                if (not state or state["status"] != "awaiting_code" or
                        state["attempt_id"] != self.attempt_id or not self.phone or
                        state["phone_fingerprint"] != self._fingerprint(self.phone, context)):
                    raise SmsLoginFailure("unresolved_request")
                age = self.clock() - state["sent_time_ms"]
                if not 0 <= age < CHALLENGE_LIFETIME_MS:
                    raise SmsLoginFailure("code_expired")
                request = prepare_sms_login(config, context, self.phone, code,
                    SmsChallenge(state["sms_state_code"]), now_ms=self.clock())
                state["status"] = "login_attempted"
                self._vault("ha-login.imvault")._save_payload(state)
                try:
                    response = self.transport_factory(enable_sms_login=True).send(request)
                except Exception:
                    state["status"] = "request_unknown"
                    self._vault("ha-login.imvault")._save_payload(state)
                    raise SmsLoginFailure("unresolved_request") from None
                state.update(status="login_response_received", login_response_body=response.body,
                    login_response_headers=self._headers(response))
                self._vault("ha-login.imvault")._save_payload(state)
                try:
                    self._commit(state, config, context)
                except (CaptchaRequired, AccountBindingRequired, SmsRejected) as error:
                    state["status"] = "awaiting_code" if type(error) is SmsRejected else "login_rejected"
                    self._vault("ha-login.imvault")._save_payload(state)
                    raise SmsLoginFailure(self._error(error, "login_rejected")) from None
                except Exception:
                    state["status"] = "login_response_parse_failed"
                    self._vault("ha-login.imvault")._save_payload(state)
                    raise SmsLoginFailure("login_response_invalid") from None
        except SmsLoginFailure:
            raise
        except Exception:
            raise SmsLoginFailure("unresolved_request") from None

    def _commit(self, state, config, context):
        result = parse_sms_login(state["login_response_body"], headers=state["login_response_headers"],
            encrypt_key=config.encrypt_key)
        if (not result.refresh_token or result.expiration_time <= self.clock() or
                not 0 < result.suggest_refresh_time < result.expiration_time):
            raise CredentialError("Incomplete login credentials")
        vault = self._vault("session.imvault")
        current = vault._load_payload() if vault.path.exists() else None
        if not current or current.get("last_login_attempt") != state["attempt_id"]:
            digest = hashlib.sha256(vault.path.read_bytes()).hexdigest() if vault.path.exists() else None
            if digest != state["source_session_digest"]:
                raise SmsLoginFailure("unresolved_request")
            vault._save_payload({"schema_version": 1, "kind": "standalone-sms-attempt",
                "status": "login_accepted_refresh_unverified", "push_device_id": "",
                "baseline_headers": dict(context.baseline_headers), "access_token": result.access_token,
                "refresh_token": result.refresh_token, "expiration_time": result.expiration_time,
                "suggest_refresh_time": result.suggest_refresh_time,
                "last_login_attempt": state["attempt_id"]})
        for name in ("ha-metadata.imvault", "ha-fault.imvault"):
            (self.data_dir / name).unlink(missing_ok=True)
        state.update(status="committed")
        state.pop("sms_state_code", None)
        self._vault("ha-login.imvault")._save_payload(state)
        self.phone = None

    def recover(self):
        """Offline retained-response recovery. Never sends or repeats a request."""
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                config, context = self._load_context()
                state = self._state()
                if not state or state["status"] not in ("login_response_received", "login_response_parse_failed"):
                    raise SmsLoginFailure("unresolved_request")
                self._commit(state, config, context)
        except SmsLoginFailure:
            raise
        except Exception:
            raise SmsLoginFailure("login_response_invalid") from None
