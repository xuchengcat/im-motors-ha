"""Signed, read-only official driving calendar requests; no token logging."""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from urllib.parse import quote, urlencode

from .trip_api import TripError, https_get
from .pyim_china.account_auth import nonce, RefreshDecision
from .pyim_china.credential_store import key_context
from .pyim_china.runtime_lock import AccountFileLock
from .pyim_china.offline_signature import signature
from .pyim_china.offline_read_response import _load
from .pyim_china.offline_encryption import decrypt_text, decrypted_response_body


def h5_signature(url, query, headers, secret):
    # ImJsCommonImpl2.getApiSignature signs the full H5 URL, not native path.
    canonical = "&".join(["GET", url] + [f"{key}={headers[key]}" for key in sorted(headers)] + ([query] if query else []))
    return base64.b64encode(hmac.new(secret, quote(canonical, safe="-._~").encode(), hashlib.sha256).digest()).decode()


def decode_response(body, headers, key, *, h5=False):
    try:
        value = _load(body)
        if headers.get("x-encrypt") == "true":
            if h5:
                envelope = value.get("data", value)
                value = _load(decrypt_text(envelope["cipherText"], key))
            else:
                value = _load(decrypted_response_body(body, key))
        code = value.get("resultCode")
        if code in ("22061", "invalid_token"):
            raise TripError("authentication_failed")
        if code == "22064":
            raise TripError("protocol_rejected")
        if code == "23373":
            raise TripError("not_vehicle_owner")
        if code != "200":
            raise TripError("business_error")
        if not isinstance(value.get("data"), dict):
            raise TripError("invalid_response")
        return value["data"]
    except TripError:
        raise
    except Exception:
        raise TripError("invalid_response") from None


class TripReader:
    def __init__(self, client, *, get=https_get):
        self.client = client
        self.get = get

    def read(self, operation, query, vehicle):
        paths = {"summary": "/app/planet/api/v1/drive/daily/new",
                 "trips": "/app/planet/api/v1/drive/daily/trips",
                 "detail": "/app/vus/v3/drive/daily/trip/detail"}
        expected = {"summary": {"tripDate"}, "trips": {"tripDate", "offset", "limit"},
                    "detail": {"tripId", "tripStartTime", "tripEndTime"}}
        if operation not in paths or not isinstance(query, dict) or set(query) != expected[operation]:
            raise TripError("invalid_request")
        self._validate_query(operation, query)
        try:
            with key_context(self.client.key_file), AccountFileLock(self.client.data_dir):
                if (self.client.data_dir / "ha-fault.imvault").exists():
                    raise TripError("account_paused")
                _, credentials, native = self.client._validate()
                metadata = self.client._vault("ha-metadata.imvault")._load_payload()
                vins = metadata.get("vins", {})
                if len(vins) != 1:
                    raise TripError("multiple_vehicles")
                if vehicle not in vins:
                    raise TripError("vehicle_unavailable")
                now = self.client.clock()
                if credentials.refresh_decision(now) != RefreshDecision.CURRENT:
                    raise TripError("authentication_refresh_required")
                headers = credentials.request_headers(now)
                headers["vin"] = vins[vehicle]
                encoded = urlencode(query)
                path = paths[operation]
                if operation == "detail":
                    signed = {"x-app-key": "android", "x-nonce": nonce(now), "x-timestamp": str(now)}
                    headers.update(signed)
                    headers["x-signature"] = signature("GET", path, signed, native.app_secret, encoded)
                    body, returned_headers = self.get("mh.immotors.com", path + "?" + encoded, headers)
                    return decode_response(body, returned_headers, native.encrypt_key)
                config = json.loads(Path(__file__).with_name("pyim_china").joinpath("h5_protocol.json").read_text())
                url = "https://m.immotors.com" + path
                signed = {"x-app-key": "android-h5", "x-nonce": secrets.token_hex(5), "x-timestamp": str(now), "x-encrypt": "false"}
                headers.update(signed)
                headers.update({"x-original-path": url, "x-h5client-version": config["client_version"],
                                "x-signature": h5_signature(url, encoded, signed, base64.b64decode(config["app_secret_b64"])),
                                "Referer": "https://m.immotors.com/app/vehicle/control/drivingList",
                                "Origin": "https://m.immotors.com", "X-Requested-With": "com.immotors.lapp",
                                "User-Agent": "Mozilla/5.0 (Linux; Android 14; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.0.0 Mobile Safari/537.36 ImmotorsApp/3.2.7",
                                "Accept": "application/json"})
                body, returned_headers = self.get("m.immotors.com", path + "?" + encoded, headers)
                return decode_response(body, returned_headers, config["encrypt_key"], h5=True)
        except TripError:
            raise
        except Exception:
            raise TripError("account_unavailable") from None

    @staticmethod
    def _validate_query(operation, query):
        from datetime import date
        try:
            if operation in ("summary", "trips"):
                if date.fromisoformat(query["tripDate"]).isoformat() != query["tripDate"]:
                    raise ValueError()
            if operation == "trips":
                if type(query["offset"]) is not int or not 1 <= query["offset"] <= 200 or query["limit"] != 5 or type(query["limit"]) is not int:
                    raise ValueError()
            if operation == "detail":
                if not isinstance(query["tripId"], str) or not query["tripId"] or len(query["tripId"]) > 256:
                    raise ValueError()
                times = [query[key] for key in ("tripStartTime", "tripEndTime")]
                if any(not isinstance(t, str) or not t.isascii() or not t.isdecimal() or len(t) > 19 for t in times):
                    raise ValueError()
                if not 0 < int(times[0]) <= int(times[1]):
                    raise ValueError()
        except (TypeError, ValueError, KeyError):
            raise TripError("invalid_request") from None
