"""One standalone refresh experiment with retained response and offline recovery.

Uses the accepted login deviceID as an explicit SDK candidate, never a push ID.
Supports DPAPI or portable vaults. No SMS, vehicle requests or automatic retries.
"""
import argparse
from dataclasses import dataclass
from pathlib import Path
import time

from .account_auth import (AccountCredentials, CredentialError, RefreshDecision,
                          _secret, validate_headers, prepare_refresh, parse_refresh_response)
from .credential_store import vault_for_path as CredentialVault, VaultError
from .local_production_config import load_local_production_config
from .offline_read_response import AuthenticationExpired, ResponseError
from .restricted_transport import RestrictedHttpsTransport, TransportError


PRIVATE = Path(__file__).resolve().parent / "private"
DEFAULT_SESSION = PRIVATE / "sdk-login-02.dpapi"
DEFAULT_RESULT = PRIVATE / "refresh-01.dpapi"


@dataclass(frozen=True, repr=False)
class StandaloneCredentials:
    access_token: str
    refresh_token: str
    refresh_device_id: str
    baseline_headers: object
    expiration_time: int = 0
    suggest_refresh_time: int = 0

    def __post_init__(self):
        for value in (self.access_token, self.refresh_token, self.refresh_device_id):
            _secret(value)
        if self.access_token.lower().startswith("bearer "):
            raise CredentialError("Supply access token without Bearer prefix")
        for value in (self.expiration_time, self.suggest_refresh_time):
            if type(value) is not int or value < 0:
                raise CredentialError("Invalid credential time metadata")
        if (self.expiration_time and self.suggest_refresh_time and
                self.suggest_refresh_time >= self.expiration_time):
            raise CredentialError("Inconsistent credential time metadata")
        object.__setattr__(self, "baseline_headers", validate_headers(self.baseline_headers))
        if self.refresh_device_id != self.baseline_headers["deviceId"]:
            raise CredentialError("Standalone refresh must use the accepted login identity")

    def __repr__(self):
        return "StandaloneCredentials(<redacted>)"

    def refresh_decision(self, now_ms):
        return AccountCredentials.refresh_decision(self, now_ms)

    def request_headers(self, now_ms):
        return AccountCredentials.request_headers(self, now_ms)


def load_session(path):
    state = CredentialVault(path)._load_payload()
    if (state.get("kind") != "standalone-sms-attempt" or state.get("status") not in
            ("login_accepted_refresh_unverified", "login_accepted_refresh_verified")):
        raise CredentialError("Validated standalone login required")
    if state.get("push_device_id") != "":
        raise CredentialError("Standalone profile must not contain a push registration")
    headers = validate_headers(state["baseline_headers"])
    return state, StandaloneCredentials(state["access_token"], state["refresh_token"],
        headers["deviceId"], headers, state.get("expiration_time", 0), state.get("suggest_refresh_time", 0))


def commit_response(session_vault, result_vault, attempt, config):
    """Offline commit; verify source session before replacing rotated credentials."""
    session, previous = load_session(session_vault.path)
    source = StandaloneCredentials(attempt["source_access_token"], attempt["source_refresh_token"],
        attempt["source_headers"]["deviceId"], attempt["source_headers"],
        attempt["source_expiration_time"], attempt["source_suggest_refresh_time"])
    updated = parse_refresh_response(source, attempt["response_body"],
        headers=attempt["response_headers"], encrypt_key=config.encrypt_key)
    unchanged = (previous.access_token == source.access_token and previous.refresh_token == source.refresh_token)
    already_saved = (previous.access_token == updated.access_token and previous.refresh_token == updated.refresh_token
                     and session.get("last_refresh_attempt_time_ms") == attempt["attempt_time_ms"])
    if (dict(previous.baseline_headers) != attempt["source_headers"] or not (unchanged or already_saved)):
        raise CredentialError("Session changed since refresh; retained response must be reviewed")
    if updated.refresh_decision(time.time_ns() // 1_000_000) == RefreshDecision.REAUTH_REQUIRED:
        raise AuthenticationExpired("Refreshed credential metadata expired")
    session.update(status="login_accepted_refresh_verified", access_token=updated.access_token,
        refresh_token=updated.refresh_token, expiration_time=updated.expiration_time,
        suggest_refresh_time=updated.suggest_refresh_time,
        refresh_device_id_strategy="accepted_login_device_id",
        last_refresh_attempt_time_ms=attempt["attempt_time_ms"])
    session.pop("pending_refresh_result", None)
    # A retained response remains available if the session write fails.
    session_vault._save_payload(session)
    attempt["status"] = "committed"
    result_vault._save_payload(attempt)
    print("Standalone refresh accepted; rotated credentials protected.")
    print("No SMS or vehicle request made. No credential values printed.")
    return updated


def refresh_once(session_path=DEFAULT_SESSION, result_path=DEFAULT_RESULT, *,
                 load_config=load_local_production_config, transport_factory=RestrictedHttpsTransport):
    session_vault, result_vault = CredentialVault(session_path), CredentialVault(result_path)
    if session_vault.path.resolve() == result_vault.path.resolve():
        raise VaultError("Refresh result must not overwrite the login session")
    if result_vault.path.exists():
        raise VaultError("Refresh attempt exists; inspect or recover it offline before another request")
    session, credentials = load_session(session_path)
    if session.get("pending_refresh_result"):
        raise CredentialError("Unresolved refresh attempt; recover or review before another request")
    now_ms = time.time_ns() // 1_000_000
    if credentials.refresh_decision(now_ms) == RefreshDecision.REAUTH_REQUIRED:
        raise AuthenticationExpired("Credential metadata expired; no refresh made")
    config = load_config()
    request = prepare_refresh(config, credentials, now_ms=now_ms)
    attempt = {"schema_version": 1, "kind": "standalone-refresh-attempt", "status": "request_attempted",
        "attempt_time_ms": now_ms, "strategy": "accepted_login_device_id",
        "source_access_token": credentials.access_token, "source_refresh_token": credentials.refresh_token,
        "source_headers": dict(credentials.baseline_headers),
        "source_expiration_time": credentials.expiration_time,
        "source_suggest_refresh_time": credentials.suggest_refresh_time}
    result_vault._save_payload(attempt)
    session["pending_refresh_result"] = str(result_vault.path.resolve())
    session_vault._save_payload(session)
    try:
        response = transport_factory(capture_http_errors=True).send(request)
    except TransportError:
        attempt["status"] = "transport_failed_outcome_unknown"
        result_vault._save_payload(attempt)
        raise
    attempt.update(status="response_received", http_status=response.status,
        response_body=response.body, response_headers={key: value for key, value in response.headers.items()
                                                       if key.lower() in ("x-encrypt", "encrypt")})
    result_vault._save_payload(attempt)
    if not 200 <= response.status < 300:
        attempt["status"] = "http_error"
        result_vault._save_payload(attempt)
        raise TransportError("HTTP status " + str(response.status) + "; refresh response protected; no retry")
    try:
        return commit_response(session_vault, result_vault, attempt, config)
    except ResponseError:
        attempt["status"] = "parse_or_business_failed"
        result_vault._save_payload(attempt)
        raise


def recover_refresh(session_path=DEFAULT_SESSION, result_path=DEFAULT_RESULT, *,
                    load_config=load_local_production_config):
    result_vault = CredentialVault(result_path)
    attempt = result_vault._load_payload()
    if (attempt.get("kind") != "standalone-refresh-attempt" or
            attempt.get("status") not in ("response_received", "parse_or_business_failed") or
            not 200 <= attempt.get("http_status", 0) < 300):
        raise VaultError("No protected successful HTTP refresh response available for recovery")
    return commit_response(CredentialVault(session_path), result_vault, attempt, load_config())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()
    try:
        (recover_refresh if args.recover else refresh_once)(args.session, args.result)
        return 0
    except (CredentialError, VaultError, ResponseError, TransportError) as error:
        print(type(error).__name__ + ": " + str(error))
    except Exception:
        print("Refresh failed; details withheld. Inspect retained state; no automatic retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
