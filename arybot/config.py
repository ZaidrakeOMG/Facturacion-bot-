from __future__ import annotations
from pathlib import Path
import json
import threading


class ConfigManager:
    def __init__(self, base: Path):
        self.base = base
        self.path = base / "config.json"
        self._lock = threading.RLock()
        self.data = self.load()

    def load(self) -> dict:
        with self._lock if hasattr(self, "_lock") else _NullLock():
            with self.path.open("r", encoding="utf-8-sig") as f:
                self.data = json.load(f)
            return self.data

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self) -> dict:
        with self._lock:
            return self.data


class _NullLock:
    def __enter__(self): return self
    def __exit__(self, *args): return False
