"""Persistent user config (favorite colors) stored under ~/.config/keyboardcolors."""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "keyboardcolors"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load() -> dict:
    if not CONFIG_FILE.exists():
        return {"favorites": []}
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"favorites": []}
    data.setdefault("favorites", [])
    return data


def _save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def load_favorites() -> list[dict]:
    return _load()["favorites"]


def add_favorite(hex_color: str, name: str | None = None) -> list[dict]:
    hex_color = hex_color.lstrip("#").upper()
    data = _load()
    if not any(f["hex"] == hex_color for f in data["favorites"]):
        data["favorites"].append({"name": name or f"#{hex_color}", "hex": hex_color})
        _save(data)
    return data["favorites"]


def remove_favorite(hex_color: str) -> list[dict]:
    hex_color = hex_color.lstrip("#").upper()
    data = _load()
    data["favorites"] = [f for f in data["favorites"] if f["hex"] != hex_color]
    _save(data)
    return data["favorites"]
