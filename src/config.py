"""설정 로더. config.yaml + .env 를 하나의 객체로 합친다."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "out"


def _load_dotenv(path: Path) -> None:
    """python-dotenv 의존성 없이 .env를 읽는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and k not in os.environ:
            os.environ[k] = v


class Config(dict):
    """dot 접근을 지원하는 얕은 래퍼."""

    def __getattr__(self, item: str) -> Any:
        try:
            v = self[item]
        except KeyError as e:
            raise AttributeError(item) from e
        return Config(v) if isinstance(v, dict) else v

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def load_config(path: str | Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")
    p = Path(path) if path else ROOT / "config.yaml"
    cfg = Config(yaml.safe_load(p.read_text(encoding="utf-8")))
    DATA_DIR.mkdir(exist_ok=True)
    OUT_DIR.mkdir(exist_ok=True)
    return cfg


def env(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v if v else default
