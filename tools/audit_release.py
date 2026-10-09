"""Check a local ZIP against supplied sensitive values without printing them."""
import argparse
import base64
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path
from custom_components.im_motors.pyim_china.local_production_config import load_local_production_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--phone-env", type=Path)
    parser.add_argument("--allow-bundled-protocol", action="store_true",
                        help="Allow only an exact common protocol JSON; personal secrets remain forbidden")
    args = parser.parse_args()
    local_key = args.key_file.read_bytes()
    secrets = [local_key, local_key.hex().encode(), base64.b64encode(local_key)]
    with key_context(args.key_file):
        session = vault_for_path(args.data_dir / "session.imvault")._load_payload()
        config = load_local_production_config(args.data_dir / "production.imvault")
        production = {"schema_version": 1, "kind": "production-config", "base_url": config.base_url,
                      "app_secret_b64": base64.b64encode(config.app_secret).decode(),
                      "encrypt_key": config.encrypt_key}
        values = [session.get(name) for name in ("access_token", "refresh_token", "vin")]
        values.extend(session.get("baseline_headers", {}).get(name) for name in ("deviceId", "x-device-id"))
        secrets.extend(value.encode() for value in values if isinstance(value, str) and len(value) >= 8)
        protocol_secrets = [production["encrypt_key"].encode(), production["app_secret_b64"].encode(),
                            base64.b64decode(production["app_secret_b64"], validate=True)]
        expected_protocol = {name: production[name] for name in (
            "schema_version", "kind", "app_secret_b64", "encrypt_key", "base_url")}
    if args.phone_env:
        for line in args.phone_env.read_text(encoding="utf-8-sig").splitlines():
            if re.match(r"^\s*IM_MOTORS_PHONE\s*=", line):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    secrets.append(value.encode())
    found = False
    with zipfile.ZipFile(args.archive) as bundle:
        for info in bundle.infolist():
            path = Path(info.filename)
            if (path.suffix in (".imvault", ".key", ".apk", ".dex", ".so", ".dpapi") or
                    any(part in ("private", ".storage", ".venv", "__pycache__") for part in path.parts)):
                found = True
            raw = bundle.read(info)
            if any(secret and secret in raw for secret in secrets):
                found = True
            approved = False
            if args.allow_bundled_protocol and info.filename in (
                    "pyim_china/protocol.json", "custom_components/im_motors/pyim_china/protocol.json"):
                try:
                    approved = json.loads(raw) == expected_protocol
                except (UnicodeError, ValueError):
                    pass
                if not approved:
                    found = True
            if not approved and any(secret and secret in raw for secret in protocol_secrets):
                found = True
    print("Release account-secret, protocol-scope and artifact audit:", "FAILED" if found else "PASS")
    return int(found)


if __name__ == "__main__":
    raise SystemExit(main())
