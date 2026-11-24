"""Reentrant local-file transactions across threads and native app processes.

The lock has a stable sibling path, so atomic replacement of the JSON file
does not replace the locked inode. OS locks are released when a process exits.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_registry_guard = threading.Lock()
_registry = {}


class _TransactionState:
    def __init__(self):
        self.mutex = threading.RLock()
        self.local = threading.local()


@contextmanager
def file_transaction(path):
    """Hold the same exclusive lock for a path across nested local calls."""
    target = Path(path).resolve()
    key = os.path.normcase(str(target))
    with _registry_guard:
        state = _registry.setdefault(key, _TransactionState())
    with state.mutex:
        if getattr(state.local, 'locked', False):
            yield
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_name(target.name + '.lock')
        with lock_path.open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            state.local.locked = True
            try:
                yield
            finally:
                state.local.locked = False
                lock.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock, fcntl.LOCK_UN)
