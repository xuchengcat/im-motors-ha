"""Initialize local HA storage; common protocol parameters contain no accounts."""
import hashlib
import hmac
from pathlib import Path

from .credential_store import VaultError, key_context, vault_for_path
from .ha_sms_login import HaSmsLogin
from .local_production_config import load_local_production_config
from .portable_store import create_key_file, load_key_file
from .runtime_lock import AccountFileLock


def managed_paths(root, phone):
    """Offline only. Keep key and account directories stable across restarts."""
    root = Path(root)
    if not root.is_absolute() or not HaSmsLogin.valid_phone(phone):
        raise VaultError("Invalid local account storage request")
    if root.is_symlink():
        raise VaultError("Managed storage must not be a symbolic link")
    load_local_production_config()
    with AccountFileLock(root):
        accounts = root / "accounts"
        key_path = root / "vault.key"
        marker_path = root / "storage.imvault"
        if accounts.is_symlink():
            raise VaultError("Managed accounts must not be a symbolic link")
        if not key_path.exists() and not key_path.is_symlink():
            if marker_path.exists() or marker_path.is_symlink() or (accounts.exists() and any(accounts.iterdir())):
                # A new key would make retained accounts unreadable and change IDs.
                raise VaultError("Local key missing; restore the matching backup")
            create_key_file(key_path)
        key = load_key_file(key_path)
        with key_context(key_path):
            marker = vault_for_path(marker_path)
            if marker.path.exists() or marker.path.is_symlink():
                state = marker._load_payload()
                if state != {"schema_version": 1, "kind": "ha-managed-storage"}:
                    raise VaultError("Invalid managed storage identity")
            else:
                if accounts.exists() and any(accounts.iterdir()):
                    raise VaultError("Storage marker missing; restore the matching backup")
                marker._save_payload({"schema_version": 1, "kind": "ha-managed-storage"})
        account_id = hmac.new(key, b"im-motors-phone-v1:" + phone.encode("ascii"), hashlib.sha256).hexdigest()
        data_dir = accounts / account_id
        if data_dir.is_symlink():
            raise VaultError("Managed account must not be a symbolic link")
        data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return {"data_dir": str(data_dir), "key_file": str(key_path)}
