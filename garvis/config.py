"""Load Garvis configuration and project files."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    raw: dict
    root: Path = ROOT

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> Config:
        p = (ROOT / path) if not Path(path).is_absolute() else Path(path)
        return cls(raw=yaml.safe_load(p.read_text()))

    # convenience accessors
    @property
    def dry_run(self) -> bool:
        return bool(self.raw.get("dry_run", True))

    @property
    def llm(self) -> dict:
        return self.raw["llm"]

    @property
    def window_days(self) -> int:
        return int(self.raw.get("window_days", 3))

    @property
    def owner_gmail(self) -> str:
        return self.raw["owner_gmail"]

    @property
    def owner_outlook(self) -> str:
        return self.raw["owner_outlook"]

    @property
    def scan_limits(self) -> dict:
        return self.raw.get("scan_limits", {})

    def server_connections(self) -> dict:
        """Build the MultiServerMCPClient connection dict from config.

        node_bin resolves from config.yaml -> GARVIS_NODE_BIN env -> `node` on PATH.
        """
        node = self.raw.get("node_bin") or os.environ.get("GARVIS_NODE_BIN") or "node"
        conns = {}
        for name, spec in self.raw.get("mcp_servers", {}).items():
            if not spec.get("enabled", True):
                continue
            conns[name] = {
                "command": node,
                "args": spec["args"],
                "env": {**spec.get("env", {})} or None,
                "transport": "stdio",
            }
        return conns

    def path(self, key: str) -> Path:
        return self.root / self.raw["paths"][key]

    def read_text(self, key: str) -> str:
        return self.path(key).read_text()

    def read_state(self) -> dict:
        try:
            return json.loads(self.path("state").read_text())
        except FileNotFoundError:
            return {"last_run_iso": None}

    def write_state(self, last_run_iso: str) -> None:
        self.path("state").write_text(
            json.dumps({"last_run_iso": last_run_iso}, indent=2) + "\n"
        )
