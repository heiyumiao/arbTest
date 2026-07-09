from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


def today_text() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def write_json_atomic(path: Path, payload: Any, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists; pass --force to overwrite")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def normalize_share_symbol(code: object) -> str | None:
    text = str(code or "").strip().lower()
    if not text:
        return None
    text = text.replace("sz", "").replace("sh", "")
    if not text.isdigit():
        return None
    text = text.zfill(6)
    if text.startswith("5"):
        return f"sh{text}"
    return text
