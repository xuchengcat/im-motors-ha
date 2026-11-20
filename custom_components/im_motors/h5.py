"""Shared App H5 canonical signing and response decoding; no account I/O."""
import base64
import hashlib
import hmac
from urllib.parse import quote
from .trip_api import TripError
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


def signing(method, url, signed, secret, query='', body=None):
    # ImJsCommonImpl2.getApiSignature -> SignUtils.g: full URL, sorted headers,
    # exact wire query, then JSON body (the check call has an empty object).
    parts = [method, url, *[f'{k}={signed[k]}' for k in sorted(signed)]]
    if query:
        parts.append(query)
    if body:
        parts.append(body)
    return base64.b64encode(hmac.new(secret, quote('&'.join(parts), safe='-._~').encode(), hashlib.sha256).digest()).decode()
