"""Bundled common protocol parameters, with optional legacy vault override."""
from dataclasses import dataclass
import base64
import json
from pathlib import Path
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
    try:
        if path is not None and (Path(path).exists() or Path(path).is_symlink()):
            # An existing override must authenticate; never mask a damaged vault.
            payload = vault_for_path(path)._load_payload()
        else:
            payload = json.loads(Path(__file__).with_name("protocol.json").read_text(encoding="utf-8"))
        if (not isinstance(payload, dict) or payload.get("kind") != "production-config"
                or payload.get("schema_version") != 1):
            raise ValueError()
        secret = base64.b64decode(payload["app_secret_b64"], validate=True)
        key, base_url = payload["encrypt_key"], payload["base_url"]
        if not isinstance(base_url, str):
            raise ValueError()
        origin = urlsplit(base_url)
        if (not secret or not isinstance(key, str) or not key or
                origin.scheme != "https" or origin.netloc not in ("mh.immotors.com", "mh.immotors.com:443") or
                origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError()
        return LocalProductionConfig(secret, key, base_url)
    except (KeyError, ValueError, TypeError, OSError):
        raise VaultError("Protocol configuration unavailable or invalid") from None
