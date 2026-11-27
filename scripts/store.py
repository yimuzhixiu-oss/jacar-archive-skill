"""manifest：幂等记录、原子写、进程锁。

放在 {base}/.jacar/ 下，不污染史料目录。
"""

from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime
from pathlib import Path

SCHEMA = 1


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
        self._fh = None

    # ── 生命周期 ────────────────────────────────────────
    def __enter__(self) -> "Manifest":
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.lock_path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            raise AlreadyRunning("已有一个 jacar 任务在跑（.jacar/.lock 被占用）")
        self.load()
        return self

    def __exit__(self, *exc) -> None:
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()

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
