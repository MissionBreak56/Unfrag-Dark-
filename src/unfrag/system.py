"""Verificações de ambiente (bateria, processos, desligar) e configurações."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Iterable, Optional

IS_WINDOWS = sys.platform == "win32"
MAX_THREADS = 64
# a interface (sem console) não pode piscar uma janela preta a cada subprocesso
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def auto_threads() -> int:
    """Número automático de threads de leitura: 2 por núcleo, até 16."""
    return min(16, (os.cpu_count() or 4) * 2)


def resolve_threads(requested: int = 0) -> int:
    """Threads de leitura a usar. `requested` > 0 vence; 0 = automático
    (ou a variável de ambiente UNFRAG_THREADS, se definida). Limitado a 1..MAX_THREADS."""
    n = int(requested or 0)
    if n <= 0:
        try:
            n = int(os.environ.get("UNFRAG_THREADS", 0))
        except ValueError:
            n = 0
    if n <= 0:
        n = auto_threads()
    return max(1, min(MAX_THREADS, n))


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
                                 capture_output=True, text=True, timeout=15,
                                 creationflags=NO_WINDOW).stdout
        except Exception:
            continue
        if name.lower() in out.lower():
            return "o processo %s está em execução" % name
    return None


def shutdown_soon(seconds: int = 60) -> None:
    if IS_WINDOWS:
        subprocess.run(["shutdown", "/s", "/t", str(seconds)], check=False, creationflags=NO_WINDOW)
    else:
        print("(desligamento só é executado no Windows)")


def settings_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "unfrag-dark", "settings.json")


def _write_json_atomic(path: str, data: dict, **kw) -> None:
    """Grava em um arquivo temporário e troca pelo definitivo: se o programa for morto (ou faltar
    energia) no meio da gravação, o arquivo antigo continua inteiro em vez de ficar cortado."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, **kw)
    os.replace(tmp, path)


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    try:
        _write_json_atomic(settings_path(), data, indent=2)
    except Exception:
        pass


def fmt_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024
    return "%.1f TB" % n


def last_run_path() -> str:
    return os.path.join(os.path.dirname(settings_path()), "last-run.json")


def save_last_run(info: dict) -> None:
    """Resumo da última execução (a interface mostra, inclusive as feitas por tarefa agendada)."""
    try:
        _write_json_atomic(last_run_path(), info, ensure_ascii=False)
    except OSError:
        pass


def load_last_run() -> dict:
    try:
        with open(last_run_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
