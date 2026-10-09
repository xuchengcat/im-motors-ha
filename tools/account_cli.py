"""Offline check and refresh recovery for an installed portable session."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from custom_components.im_motors.pyim_china import AccountClient, ClientFailure
from custom_components.im_motors.pyim_china.credential_store import key_context
from custom_components.im_motors.pyim_china.local_production_config import load_local_production_config
from custom_components.im_motors.pyim_china.runtime_lock import AccountFileLock
from custom_components.im_motors.pyim_china.standalone_refresh_probe import recover_refresh


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check")
    recovery = commands.add_parser("recover-refresh")
    recovery.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "check":
            AccountClient(args.data_dir, args.key_file).validate()
            print("Protected session and configuration valid. No HTTP request made.")
        else:
            with key_context(args.key_file), AccountFileLock(args.data_dir):
                config = load_local_production_config(args.data_dir / "production.imvault")
                recover_refresh(args.data_dir / "session.imvault", args.result, load_config=lambda: config)
            print("Offline recovery finished. No HTTP request made.")
        return 0
    except Exception:
        print("Offline validation/recovery failed; protected state retained. Details withheld.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
