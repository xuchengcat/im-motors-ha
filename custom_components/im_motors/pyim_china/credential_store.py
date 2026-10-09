"""Portable-only storage adapter; key context is local to each operation."""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path


class VaultError(RuntimeError):
    pass


class CredentialVault:
    def __init__(self, path):
        self.path = Path(path)

    def __repr__(self):
        return "CredentialVault(<redacted>)"


_key_file = ContextVar("im_motors_key_file", default=None)


@contextmanager
def key_context(path):
    token = _key_file.set(Path(path))
    try:
        yield
    finally:
        _key_file.reset(token)


def vault_for_path(path):
    from .portable_store import PortableCredentialVault
    key = _key_file.get()
    if key is None or Path(path).suffix != ".imvault":
        raise VaultError("Portable vault and explicit key context required")
    return PortableCredentialVault(path, key)
