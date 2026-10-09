"""Cross-process lock for operations on one portable account volume."""
import os
from pathlib import Path

from .credential_store import VaultError


class AccountFileLock:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / "account.lock"
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            self.stream = os.fdopen(descriptor, "r+b")
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b"\0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except OSError:
            if self.stream:
                self.stream.close()
                self.stream = None
            raise VaultError("Another account operation is active; no concurrent refresh or login allowed") from None

    def __exit__(self, *_):
        if self.stream:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
