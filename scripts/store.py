"""manifest：幂等记录、原子写、进程锁。

放在 {base}/.jacar/ 下，不污染史料目录。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 进程锁：POSIX 用 fcntl.flock，Windows 用 msvcrt.locking。
# 两者都是"非阻塞 + 冲突即报 AlreadyRunning"的语义。
if sys.platform == "win32":                     # pragma: no cover - 平台分支
    import msvcrt
    fcntl = None
else:
    import fcntl
    msvcrt = None

SCHEMA = 1

LOCK_TIP = "已有一个 jacar 任务在跑（.jacar/.lock 被占用）"


class AlreadyRunning(RuntimeError):
    pass


class _FileLock:
    """跨平台非阻塞文件锁。

    POSIX：`flock(LOCK_EX | LOCK_NB)`，用 "w" 打开（锁文件内容无意义）。
    Windows：`msvcrt.locking(LK_NBLCK)`，必须用 "a+b" 打开且锁住 1 个字节，
    因此文件里要先保证有 1 字节可锁。
    """

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        if sys.platform == "win32":             # pragma: no cover - 平台分支
            fh = open(self.path, "a+b")
            try:
                fh.seek(0)
                if not fh.read(1):
                    fh.write(b"\0")
                    fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                fh.close()
                raise AlreadyRunning(LOCK_TIP)
            self._fh = fh
            return

        fh = open(self.path, "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunning(LOCK_TIP)
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":         # pragma: no cover - 平台分支
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AlreadyRunning(RuntimeError):
    pass


class Manifest:
    def __init__(self, base: Path | str):
        self.base = Path(base)
        self.dir = self.base / ".jacar"
        self.path = self.dir / "manifest.json"
        self.lock_path = self.dir / ".lock"
        self.data: dict = {}
        self._lock: _FileLock | None = None

    # ── 生命周期 ────────────────────────────────────────
    def __enter__(self) -> "Manifest":
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = _FileLock(self.lock_path)
        self._lock.acquire()
        self.load()
        return self

    def __exit__(self, *exc) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    # ── 读写 ────────────────────────────────────────────
    def load(self) -> None:
        if self.path.is_file():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}
        self.data.setdefault("schema", SCHEMA)
        self.data.setdefault("books", {})
        self.data.setdefault("items", {})
        self.data.setdefault("created_at", now())

    def save(self) -> None:
        self.data["updated_at"] = now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    # ── 记录 ────────────────────────────────────────────
    def book(self, fond: str) -> dict:
        return self.data["books"].setdefault(fond, {})

    def item(self, refcode: str) -> dict:
        return self.data["items"].setdefault(refcode, {})

    def mark_item(self, refcode: str, **fields) -> None:
        rec = self.item(refcode)
        rec.update(fields)
        rec["updated_at"] = now()
        self.save()

    def done_items(self) -> dict[str, dict]:
        return {k: v for k, v in self.data["items"].items() if v.get("state") == "done"}
