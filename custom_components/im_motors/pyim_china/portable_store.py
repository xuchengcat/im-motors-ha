"""Portable authenticated credential files with a separate 256-bit key.

The key is supplied by a mounted secret, never embedded in the data or image.
Writes replace atomically; Linux files use private permissions. No networking.
"""
import json
import os
from pathlib import Path
import stat
import tempfile

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from .credential_store import CredentialVault, VaultError
from .offline_read_response import _load, ResponseError


_MAGIC = b"IMHA-GCM-1\0"
_MAX_BYTES = 2 * 1024 * 1024
_NONCE_BYTES = 12
_TAG_BYTES = 16


def _private_file(path):
    if path.is_symlink():
        raise VaultError("Secret and vault files must not be symbolic links")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise VaultError("Secret and vault must be regular files")
    if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise VaultError("Secret and vault must be owned by the current user with private permissions")


def create_key_file(path):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(get_random_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError:
        raise VaultError("Unable to create a new secret key file") from None


def load_key_file(path):
    """Read a private local 256-bit key; never create or replace it."""
    try:
        path = Path(path)
        _private_file(path)
        with path.open("rb") as stream:
            key = stream.read(33)
        if len(key) != 32:
            raise VaultError("Expected a 32-byte secret key file")
        return key
    except OSError:
        raise VaultError("Unable to read secret key file") from None


class PortableCredentialVault(CredentialVault):
    def __init__(self, path, key_file):
        super().__init__(path)
        self.key_file = Path(key_file)
        if self.path.resolve() == self.key_file.resolve():
            raise VaultError("Secret key must be separate from credential data")

    def __repr__(self):
        return "PortableCredentialVault(<redacted>)"

    def _key(self):
        return load_key_file(self.key_file)

    def _load_payload(self):
        try:
            _private_file(self.path)
            if self.path.stat().st_size > _MAX_BYTES + len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES:
                raise VaultError("Portable credential size limit exceeded")
            blob = self.path.read_bytes()
            if not blob.startswith(_MAGIC) or len(blob) <= len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES:
                raise VaultError("Invalid portable credential format")
            offset = len(_MAGIC)
            cipher = AES.new(self._key(), AES.MODE_GCM, nonce=blob[offset:offset + _NONCE_BYTES])
            cipher.update(_MAGIC)
            raw = cipher.decrypt_and_verify(blob[offset + _NONCE_BYTES + _TAG_BYTES:],
                                           blob[offset + _NONCE_BYTES:offset + _NONCE_BYTES + _TAG_BYTES])
            return _load(raw.decode("utf-8"))
        except (OSError, ValueError, TypeError, UnicodeError, ResponseError):
            raise VaultError("Unable to authenticate or load portable credentials") from None

    def _save_payload(self, payload):
        try:
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeError):
            raise VaultError("Invalid portable credential payload") from None
        if not raw or len(raw) > _MAX_BYTES:
            raise VaultError("Portable credential size limit exceeded")
        cipher = AES.new(self._key(), AES.MODE_GCM, nonce=get_random_bytes(_NONCE_BYTES))
        cipher.update(_MAGIC)
        ciphertext, tag = cipher.encrypt_and_digest(raw)
        blob = _MAGIC + cipher.nonce + tag + ciphertext
        temporary = None
        try:
            if self.path.is_symlink():
                raise VaultError("Credential target must not be a symbolic link")
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(prefix=".imha-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            if os.name == "posix":
                descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        except OSError:
            raise VaultError("Unable to persist portable credentials") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
