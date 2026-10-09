"""Offline reproduction of SignUtils.g; no HTTP calls or stored credentials.

The caller must supply the app-secret bytes and the exact wire query/body.
This does not establish whether a particular endpoint is safe to replay.
"""
import base64
import hashlib
import hmac
from urllib.parse import quote


def canonical_request(method, encoded_path, signed_headers, encoded_query=None, body=None):
    if not method.isascii() or not method or not encoded_path.startswith("/"):
        raise ValueError("Expected ASCII HTTP method and encoded absolute path")
    parts = [method.upper(), encoded_path]
    # Java String ordering compares UTF-16 code units, not Unicode code points.
    for name in sorted(signed_headers, key=lambda value: value.encode("utf-16-be")):
        parts.append(f"{name}={signed_headers[name]}")
    if encoded_query:
        parts.append(encoded_query)
    if body:
        parts.append(body)
    return "&".join(parts)


def signature(method, encoded_path, signed_headers, secret, encoded_query=None, body=None):
    canonical = canonical_request(method, encoded_path, signed_headers, encoded_query, body)
    # Equivalent to URLEncoder UTF-8 followed by +/*/%7E substitutions.
    encoded = quote(canonical, safe="-._~", encoding="utf-8", errors="strict")
    digest = hmac.new(secret, encoded.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")
