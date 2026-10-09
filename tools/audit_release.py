"""Check a local ZIP against supplied sensitive values without printing them."""
import argparse
import base64
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custom_components.im_motors.pyim_china.credential_store import key_context, vault_for_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--phone-env", type=Path)
    args = parser.parse_args()
    secrets = [args.key_file.read_bytes()]
    with key_context(args.key_file):
        session = vault_for_path(args.data_dir / "session.imvault")._load_payload()
        production = vault_for_path(args.data_dir / "production.imvault")._load_payload()
        values = [session.get(name) for name in ("access_token", "refresh_token", "vin")]
        values.extend(session.get("baseline_headers", {}).get(name) for name in ("deviceId", "x-device-id"))
        values.extend([production.get("encrypt_key"), production.get("app_secret_b64")])
        secrets.extend(value.encode() for value in values if isinstance(value, str) and len(value) >= 8)
        secrets.append(base64.b64decode(production["app_secret_b64"], validate=True))
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
                    any(part in ("private", ".venv", "__pycache__") for part in path.parts)):
                found = True
            raw = bundle.read(info)
            if any(secret and secret in raw for secret in secrets):
                found = True
    print("Release sensitive-value and artifact audit:", "FAILED" if found else "PASS")
    return int(found)


if __name__ == "__main__":
    raise SystemExit(main())
