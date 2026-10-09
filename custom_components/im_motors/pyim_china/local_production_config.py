"""Load protected production configuration; no native or APK dependency."""
from dataclasses import dataclass
import base64
from urllib.parse import urlsplit

from .credential_store import vault_for_path, VaultError


@dataclass(frozen=True, repr=False)
class LocalProductionConfig:
    app_secret: bytes
    encrypt_key: str
    base_url: str

    def __repr__(self):
        return "LocalProductionConfig(<redacted>)"


def load_local_production_config(path=None):
    if path is None:
        raise VaultError("Explicit protected production configuration required")
    payload = vault_for_path(path)._load_payload()
    try:
        if payload.get("kind") != "production-config" or payload.get("schema_version") != 1:
            raise ValueError()
        secret = base64.b64decode(payload["app_secret_b64"], validate=True)
        key, base_url = payload["encrypt_key"], payload["base_url"]
        origin = urlsplit(base_url)
        if (not secret or not isinstance(key, str) or not key or
                origin.scheme != "https" or origin.netloc not in ("mh.immotors.com", "mh.immotors.com:443") or
                origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError()
        return LocalProductionConfig(secret, key, base_url)
    except (KeyError, ValueError, TypeError):
        raise VaultError("Invalid protected production configuration") from None
