"""Agendamento via Agendador de Tarefas do Windows (schtasks)."""
from __future__ import annotations

import re
import subprocess
import sys
from typing import List

from .system import NO_WINDOW

TASK_FOLDER = "UnfragDark"
FREQUENCIES = {"diária": "DAILY", "semanal": "WEEKLY", "mensal": "MONTHLY"}
MODES = ("quick", "defrag", "freespace", "trim")  # trim = otimização de SSD (sem mover arquivos)
_LETTER = re.compile(r"^[A-Za-z]$")
_TIME = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


def task_name(letter: str, frequency: str) -> str:
    return "%s\\%s-%s" % (TASK_FOLDER, _check_letter(letter), FREQUENCIES.get(frequency, frequency).lower())


def _app_command() -> str:
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    return '"%s" -m unfrag' % sys.executable


def _check_letter(letter: str) -> str:
    drive = letter.strip().rstrip(":\\/")
    if not _LETTER.match(drive):
        raise ValueError("unidade inválida: %r (use uma letra, como C:)" % letter)
    return drive.upper()


def build_create_args(letter: str, mode: str, frequency: str, time_hhmm: str,
                      extra: str = "") -> List[str]:
    drive = _check_letter(letter)
    if mode not in MODES:
        raise ValueError("modo inválido: %r (use %s)" % (mode, ", ".join(MODES)))
    if frequency not in FREQUENCIES and frequency.upper() not in FREQUENCIES.values():
        raise ValueError("frequência inválida: %r" % frequency)
    if not _TIME.match(time_hhmm.strip()):
        raise ValueError("horário inválido: %r (use HH:MM, por exemplo 03:00)" % time_hhmm)
    time_hhmm = time_hhmm.strip()
    sc = FREQUENCIES.get(frequency, frequency).upper()
    if mode == "trim":
        cmd = "%s trim %s: --skip-battery --notify" % (_app_command(), drive)
    else:
        cmd = "%s defrag %s: --mode %s --skip-battery --notify %s" % (_app_command(), drive, mode, extra)
    return ["schtasks", "/Create", "/F", "/TN", task_name(letter, frequency), "/SC", sc,
            "/ST", time_hhmm, "/RL", "HIGHEST", "/TR", cmd.strip()]


def build_delete_args(letter: str, frequency: str) -> List[str]:
    return ["schtasks", "/Delete", "/F", "/TN", task_name(letter, frequency)]


def run(args: List[str]) -> str:
    res = subprocess.run(args, capture_output=True, text=True, creationflags=NO_WINDOW)
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        raise RuntimeError(out.strip() or "schtasks falhou")
    return out.strip()
