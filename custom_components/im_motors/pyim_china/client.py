"""Persistent account client with cached telemetry and opt-in scoped controls.

Blocking I/O is called from HA's executor. Never log request/response values.
Unsafe failures stop on disk; transient GET failures use durable backoff.
"""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import re
import time
import uuid

from .account_auth import CredentialError, RefreshDecision, nonce, validate_headers
from .credential_store import key_context, vault_for_path, VaultError
from .local_production_config import load_local_production_config
from .offline_read_request import OfflineReadClient
from .offline_read_response import AuthenticationExpired, ResponseError, Presence, CategorySnapshot
from .restricted_transport import RestrictedHttpsTransport, TransportError, RetryableReadError
from .runtime_lock import AccountFileLock
from .standalone_refresh_probe import load_session, refresh_once

METADATA_INTERVAL_MS = 30 * 60 * 1000
TELEMETRY_INTERVAL_MS = 60 * 60 * 1000
_VIN = re.compile(r"[A-HJ-NPR-Z0-9]{17}\Z")


class ClientFailure(RuntimeError):
    """Sanitized error; persistent failure requires deliberate manual resume."""


class LoginRequired(ClientFailure):
    pass


class TemporaryReadFailure(ClientFailure):
    """Read polling will resume after a durable, bounded backoff."""


@dataclass(frozen=True, repr=False)
class Vehicle:
    identifier: str
    name: str
    project_code: str | None
    has_setting: bool | None
    has_support: bool | None

    def __repr__(self):
        return "Vehicle(<redacted>)"


@dataclass(frozen=True, repr=False)
class AccountSnapshot:
    vehicles: tuple[Vehicle, ...]
    metadata_time_ms: int
    expiration_time_ms: int
    refresh_time_ms: int
    telemetry: Mapping[str, CategorySnapshot] = field(default_factory=lambda: MappingProxyType({}))
    telemetry_time_ms: int = 0
    control_status: Mapping = field(default_factory=lambda: MappingProxyType({}))

    def __repr__(self):
        return "AccountSnapshot(<redacted>)"


class AccountClient:
    def __init__(self, data_dir, key_file, *, transport_factory=RestrictedHttpsTransport,
                 clock=lambda: time.time_ns() // 1_000_000):
        self.data_dir = Path(data_dir)
        self.key_file = Path(key_file)
        self.transport_factory = transport_factory
        self.clock = clock
        self.telemetry_interval_ms = TELEMETRY_INTERVAL_MS
        self.control_enabled = False

    def set_control_enabled(self, enabled):
        self.control_enabled = enabled is True

    def set_telemetry_interval(self, minutes):
        """Enforce the five-minute lower bound even outside the HA UI."""
        if type(minutes) is not int or minutes < 5:
            raise ValueError("Vehicle polling interval must be an integer of at least five minutes")
        self.telemetry_interval_ms = minutes * 60 * 1000

    def __repr__(self):
        return "AccountClient(<redacted>)"

    def _vault(self, filename):
        return vault_for_path(self.data_dir / filename)

    def _validate(self):
        login = self._vault("ha-login.imvault")
        if login.path.exists():
            from .ha_sms_login import LOGIN_BLOCKING_STATUSES, CHALLENGE_LIFETIME_MS
            state = login._load_payload()
            if state.get("kind") != "ha-sms-login" or state.get("schema_version") != 1:
                raise ClientFailure("Invalid retained SMS authentication state")
            sent = state.get("sent_time_ms")
            if (state.get("status") == "awaiting_code" and type(sent) is int
                    and self.clock() - sent >= CHALLENGE_LIFETIME_MS):
                # No login POST has occurred in this state. Keep the evidence,
                # but an abandoned SMS challenge must not disable a valid session.
                state["status"] = "challenge_expired"
                login._save_payload(state)
            if state.get("status") in LOGIN_BLOCKING_STATUSES:
                raise ClientFailure("SMS authentication is pending; finish or review the login attempt")
        session, credentials = load_session(self.data_dir / "session.imvault")
        device = self._vault("device.imvault")._load_payload()
        if (device.get("kind") != "sms-device" or device.get("schema_version") != 1 or
                device.get("push_device_id") != "" or
                dict(validate_headers(device["baseline_headers"])) != dict(credentials.baseline_headers)):
            raise CredentialError("Persistent identity does not match session")
        if session.get("pending_refresh_result"):
            raise ClientFailure("Refresh outcome unresolved; recover offline before resuming")
        decision = credentials.refresh_decision(self.clock())
        if decision in (RefreshDecision.REAUTH_REQUIRED, RefreshDecision.METADATA_UNKNOWN):
            raise LoginRequired("Manual account login or credential metadata review required")
        config = load_local_production_config(self.data_dir / "production.imvault")
        return session, credentials, config

    def validate(self):
        """Offline config-flow validation; never refresh or query."""
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                _, credentials, _ = self._validate()
                # Stable instance identity is hashed before entering HA storage.
                return hashlib.sha256(credentials.refresh_device_id.encode("ascii")).hexdigest()
        except ClientFailure:
            raise
        except Exception:
            raise ClientFailure("Protected configuration unavailable or invalid") from None

    def resume(self, *, review_control=False):
        """Explicit user recovery after offline review; unresolved refresh stays blocked."""
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                self._validate()
                fault = self.data_dir / "ha-fault.imvault"
                if fault.is_symlink():
                    raise VaultError("Invalid failure marker")
                fault.unlink(missing_ok=True)
                control = self._vault("ha-control.imvault")
                if review_control and control.path.exists():
                    state = control._load_payload()
                    if state.get("status") in ("pending", "unknown"):
                        state.update(status="reviewed", reviewed_time_ms=self.clock())
                        control._save_payload(state)
        except ClientFailure:
            raise
        except Exception:
            raise ClientFailure("Unable to resume protected account") from None

    @staticmethod
    def _vehicles(result):
        vehicles, seen = [], set()
        if result.presence != Presence.VALUE:
            raise ResponseError("Scene metadata unavailable")
        for item in result.vehicles:
            fields = item.fields
            vin = fields["vin"].value
            if not isinstance(vin, str) or not _VIN.fullmatch(vin):
                raise ResponseError("Invalid associated vehicle identity")
            identifier = hashlib.sha256(vin.encode("ascii")).hexdigest()
            if identifier in seen:
                raise ResponseError("Duplicate associated vehicle identity")
            seen.add(identifier)
            # User-assigned names can contain plate/phone data; use a generic label.
            vehicles.append(Vehicle(identifier, "智己汽车", fields["projectCode"].value,
                                    fields["hasSetting"].value, fields["hasSupport"].value))
        return tuple(vehicles)

    def update(self, *, include_telemetry=False, force_telemetry=False):
        """Check auth each minute; metadata/telemetry have separate durable caches."""
        read_failures = 0
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                fault_path = self.data_dir / "ha-fault.imvault"
                fault = self._vault("ha-fault.imvault")._load_payload() if fault_path.exists() else {}
                transient = (fault.get("reason") == "temporary_read_failure"
                    and type(fault.get("retry_at_ms")) is int
                    and type(fault.get("read_failures")) is int
                    and 1 <= fault["read_failures"] <= 16)
                if fault and not transient:
                    raise ClientFailure("Account requests paused; review retained state and resume manually")
                if transient:
                    read_failures = fault["read_failures"]
                _, credentials, config = self._validate()
                now_ms = self.clock()
                if credentials.refresh_decision(now_ms) == RefreshDecision.DUE:
                    result = self.data_dir / "attempts" / ("refresh-" + uuid.uuid4().hex + ".imvault")
                    refresh_once(self.data_dir / "session.imvault", result,
                                 load_config=lambda: config, transport_factory=self.transport_factory)
                    _, credentials = load_session(self.data_dir / "session.imvault")
                    if credentials.refresh_decision(self.clock()) != RefreshDecision.CURRENT:
                        raise LoginRequired("Refresh returned no future schedule")
                # Token maintenance runs even while vehicle reads are backing off.
                if transient and self.clock() < fault["retry_at_ms"]:
                    raise TemporaryReadFailure("Temporary cloud read failure; retry scheduled")
                cache = self._vault("ha-metadata.imvault")
                saved = cache._load_payload() if cache.path.exists() else None
                if (saved is None or (include_telemetry and "vins" not in saved) or now_ms < saved["time_ms"] or
                        now_ms - saved["time_ms"] >= METADATA_INTERVAL_MS):
                    reader = OfflineReadClient(config)
                    request = reader.prepare("isc_vehicles", nonce=nonce(now_ms), timestamp_ms=now_ms,
                                             baseline_headers=credentials.request_headers(now_ms))
                    # Persist BEFORE HTTP: a hard crash cannot silently retry the query.
                    self._vault("ha-fault.imvault")._save_payload(
                        {"kind": "ha-fault", "reason": "metadata_request_pending"})
                    response = self.transport_factory(enable_isc_vehicles=True).send(request)
                    result = reader.parse_response("isc_vehicles", response.body, response_headers=response.headers)
                    vehicles = self._vehicles(result)
                    saved = {"kind": "ha-metadata", "time_ms": now_ms,
                             "vins": {hashlib.sha256(item.fields["vin"].value.encode("ascii")).hexdigest():
                                      item.fields["vin"].value for item in result.vehicles},
                             "vehicles": [dict(identifier=v.identifier, name=v.name, project_code=v.project_code,
                                               has_setting=v.has_setting, has_support=v.has_support) for v in vehicles]}
                    cache._save_payload(saved)
                    (self.data_dir / "ha-fault.imvault").unlink()
                vehicles = tuple(Vehicle(**item) for item in saved["vehicles"])
                telemetry, telemetry_time = {}, 0
                if include_telemetry:
                    telemetry, telemetry_time = self._telemetry(
                        saved, credentials, config, now_ms, force=force_telemetry)
                if transient:
                    fault_path.unlink(missing_ok=True)
                control_status = {}
                control_vault = self._vault("ha-control.imvault")
                if control_vault.path.exists():
                    state = control_vault._load_payload()
                    if state.get("vehicle_id") in {v.identifier for v in vehicles}:
                        control_status[state["vehicle_id"]] = {key: state.get(key) for key in ("status", "action")}
                return AccountSnapshot(vehicles, saved["time_ms"], credentials.expiration_time,
                    credentials.suggest_refresh_time, MappingProxyType(telemetry), telemetry_time,
                    MappingProxyType(control_status))
        except ClientFailure:
            raise
        except AuthenticationExpired:
            self._record_fault("authentication_expired")
            raise LoginRequired("Account authentication expired; manual login required") from None
        except RetryableReadError:
            failures = min(read_failures + 1, 16)
            delay_ms = min(60 * 2 ** min(failures - 1, 4), 900) * 1000
            self._record_fault("temporary_read_failure", read_failures=failures,
                               retry_at_ms=self.clock() + delay_ms)
            raise TemporaryReadFailure("Temporary cloud read failure; retry scheduled") from None
        except (CredentialError, VaultError, ResponseError, TransportError, OSError, KeyError, TypeError, ValueError) as error:
            self._record_fault("account_operation_failed", failure_type=type(error).__name__)
            raise ClientFailure("Account operation failed; retained state requires manual review") from None

    def _telemetry(self, metadata, credentials, config, now_ms, *, force=False):
        """Scoped v6 reads only. Encrypted cache and durable pre-request stop marker."""
        reader = OfflineReadClient(config)
        cache = self._vault("ha-telemetry.imvault")
        saved = cache._load_payload() if cache.path.exists() else None
        vins = metadata["vins"]
        if set(vins) != {item["identifier"] for item in metadata["vehicles"]}:
            raise ResponseError("Cached vehicle associations unavailable")
        for identifier, vin in vins.items():
            if (not isinstance(vin, str) or not _VIN.fullmatch(vin) or
                    hashlib.sha256(vin.encode("ascii")).hexdigest() != identifier):
                raise ResponseError("Cached vehicle association identity invalid")
        if (force or saved is None or set(saved["responses"]) != set(vins) or
                now_ms < saved["time_ms"] or now_ms - saved["time_ms"] >= self.telemetry_interval_ms):
            responses = {}
            for identifier, vin in vins.items():
                request = reader.prepare("vehicle_tab", vin=vin,
                    terminal=credentials.baseline_headers["x-machine-model"],
                    nonce=nonce(now_ms), timestamp_ms=now_ms,
                    baseline_headers=credentials.request_headers(now_ms))
                self._vault("ha-fault.imvault")._save_payload(
                    {"kind": "ha-fault", "reason": "telemetry_request_pending"})
                response = self.transport_factory(enable_vehicle_tab=True, allowed_vin=vin).send(request)
                retained = {"body": response.body, "headers": dict(response.headers)}
                # Retain the response encrypted before parsing; no raw responses in HA.
                self._vault("ha-telemetry-response.imvault")._save_payload(retained)
                result = reader.parse_response("vehicle_tab", response.body, response_headers=response.headers)
                if not self._telemetry_matches_vehicle(result, vin, len(vins)):
                    raise ResponseError("Vehicle telemetry identity does not match association")
                responses[identifier] = retained
            saved = {"kind": "ha-telemetry", "time_ms": now_ms, "responses": responses}
            cache._save_payload(saved)
            (self.data_dir / "ha-fault.imvault").unlink(missing_ok=True)
        parsed = {}
        for identifier, vin in vins.items():
            response = saved["responses"][identifier]
            result = reader.parse_response("vehicle_tab", response["body"], response_headers=response["headers"])
            if not self._telemetry_matches_vehicle(result, vin, len(vins)):
                raise ResponseError("Cached telemetry identity does not match association")
            parsed[identifier] = result
        return parsed, saved["time_ms"]

    def control(self, vehicle_id, action, parameters=None):
        """One explicit POST; durable pre-send marker, no resend or fallback."""
        from .vehicle_control import prepare_control, parse_control_response, ControlError
        from .offline_read_response import BusinessResponseError
        if not self.control_enabled:
            raise ClientFailure("Vehicle control disabled in read-only mode")
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                if not self.control_enabled:
                    raise ClientFailure("Vehicle control disabled in read-only mode")
                if (self.data_dir / "ha-fault.imvault").exists():
                    raise ClientFailure("Account requests paused; review before controlling vehicle")
                session, credentials, config = self._validate()
                if credentials.refresh_decision(self.clock()) != RefreshDecision.CURRENT:
                    raise LoginRequired("Refresh account before controlling vehicle")
                metadata = self._vault("ha-metadata.imvault")._load_payload()
                vin = metadata.get("vins", {}).get(vehicle_id)
                if not isinstance(vin, str) or hashlib.sha256(vin.encode("ascii")).hexdigest() != vehicle_id:
                    raise ControlError("Vehicle is not associated with this account")
                telemetry = self._vault("ha-telemetry.imvault")._load_payload()["responses"][vehicle_id]
                snapshot = OfflineReadClient(config).parse_response("vehicle_tab", telemetry["body"],
                                                                   response_headers=telemetry["headers"])
                if not self._telemetry_matches_vehicle(snapshot, vin, len(metadata["vins"])):
                    raise ControlError("Vehicle telemetry identity mismatch")
                uid = session.get("user_account_id", "")
                # Older managed sessions retained the encrypted login response.
                # Only recover UID from the attempt that created this session.
                login = self._vault("ha-login.imvault")
                if not uid and login.path.exists() and session.get("last_login_attempt"):
                    retained = login._load_payload()
                    if (retained.get("status") == "committed" and
                            retained.get("attempt_id") == session["last_login_attempt"]):
                        from .sms_auth import parse_sms_login
                        uid = parse_sms_login(retained["login_response_body"],
                            headers=retained["login_response_headers"], encrypt_key=config.encrypt_key).uid
                now_ms = self.clock()
                request = prepare_control(config, vin=vin, uid=uid, action=action,
                    parameters={} if parameters is None else parameters,
                    v3=snapshot.supports_feature("support_3.0"), nonce=nonce(now_ms), timestamp_ms=now_ms,
                    baseline_headers=credentials.request_headers(now_ms))
                status_vault = self._vault("ha-control.imvault")
                previous = status_vault._load_payload() if status_vault.path.exists() else {}
                if previous.get("status") in ("pending", "unknown"):
                    raise ClientFailure("Previous control outcome unknown; inspect vehicle and reconfigure to resume")
                if type(previous.get("time_ms")) is int and now_ms - previous["time_ms"] < 3000:
                    raise ClientFailure("Please wait three seconds between vehicle controls")
                attempt = self._vault("controls/" + uuid.uuid4().hex + ".imvault")
                state = {"kind": "vehicle-control", "status": "pending", "time_ms": now_ms,
                         "vehicle_id": vehicle_id, "action": action}
                attempt._save_payload(state)
                status_vault._save_payload(state)
                # Capability is scoped to the one constructed request and owned VIN.
                # No automatic retry even after auth errors, timeout or restart.
                try:
                    if not self.control_enabled:
                        state["status"] = "cancelled"
                        raise ClientFailure("Vehicle control disabled in read-only mode")
                    response = self.transport_factory(control_request=request, allowed_vin=vin).send(request)
                    state.update(response_body=response.body, response_headers=dict(response.headers))
                    attempt._save_payload(state)
                    try:
                        state["status"] = parse_control_response(response, config)
                    except (BusinessResponseError, AuthenticationExpired):
                        state["status"] = "rejected"
                        raise
                except Exception:
                    if state["status"] == "pending":
                        state["status"] = "unknown"
                    raise
                finally:
                    attempt._save_payload(state)
                    status_vault._save_payload(state)
                return {"status": "accepted", "execution_confirmed": False, "action": action}
        except ClientFailure:
            raise
        except AuthenticationExpired:
            raise LoginRequired("Vehicle control authentication expired; sign in again") from None
        except ControlError as error:
            raise ClientFailure(str(error)) from None
        except BusinessResponseError:
            raise ClientFailure("Vehicle control rejected; no automatic retry") from None
        except Exception:
            raise ClientFailure("Vehicle control failed or outcome unknown; inspect vehicle before another attempt") from None

    @staticmethod
    def _telemetry_matches_vehicle(result, requested_vin, vehicle_count):
        # Single-car responses may omit VIN. The request and cache remain scoped
        # to the VIN from account metadata; never invent a VIN in the response.
        if result.vin.presence in (Presence.MISSING, Presence.NULL):
            return vehicle_count == 1
        return result.vin.value == requested_vin

    def _record_fault(self, reason, **details):
        # Best effort: refresh already has its own durable pending marker.
        try:
            with key_context(self.key_file), AccountFileLock(self.data_dir):
                self._vault("ha-fault.imvault")._save_payload(
                    {"kind": "ha-fault", "reason": reason, "time_ms": self.clock(), **details})
        except Exception:
            pass
