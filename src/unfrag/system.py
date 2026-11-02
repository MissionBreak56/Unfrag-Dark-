"""Verificações de ambiente (bateria, processos, desligar) e configurações."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Iterable, Optional

IS_WINDOWS = sys.platform == "win32"


def guard_reason(skip_on_battery: bool = False, exclude_processes: Iterable[str] = ()) -> Optional[str]:
    """Devolve o motivo para NÃO executar agora, ou None se pode prosseguir."""
    if not IS_WINDOWS:
        return None
    if skip_on_battery:
        from .backends.windows import on_battery
        if on_battery():
            return "o computador está usando bateria"
    for name in exclude_processes:
        name = name.strip()
        if not name:
            continue
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/NH"],
                                 capture_output=True, text=True, timeout=15).stdout
        except Exception:
            continue
        if name.lower() in out.lower():
            return "o processo %s está em execução" % name
    return None


def shutdown_soon(seconds: int = 60) -> None:
    if IS_WINDOWS:
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], check=False)
    else:
        print("(desligamento só é executado no Windows)")


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "unfrag-dark", "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        p = settings_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n
