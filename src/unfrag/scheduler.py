"""Agendamento via Agendador de Tarefas do Windows (schtasks)."""
from __future__ import annotations

import subprocess
import sys
from typing import List

TASK_FOLDER = "UnfragDark"
FREQUENCIES = {"diária": "DAILY", "semanal": "WEEKLY", "mensal": "MONTHLY"}


def task_name(letter: str, frequency: str) -> str:
    return "%s\\%s-%s" % (TASK_FOLDER, letter.upper().rstrip(":"), FREQUENCIES.get(frequency, frequency).lower())


def _app_command() -> str:
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    return '"%s" -m unfrag' % sys.executable


def build_create_args(letter: str, mode: str, frequency: str, time_hhmm: str,
                      extra: str = "") -> List[str]:
    sc = FREQUENCIES.get(frequency, frequency).upper()
    cmd = "%s defrag %s: --mode %s --skip-battery --notify %s" % (_app_command(), letter.upper().rstrip(":"), mode, extra)
    return ["schtasks", "/Create", "/F", "/TN", task_name(letter, frequency), "/SC", sc,
            "/ST", time_hhmm, "/RL", "HIGHEST", "/TR", cmd.strip()]


def build_delete_args(letter: str, frequency: str) -> List[str]:
    return ["schtasks", "/Delete", "/F", "/TN", task_name(letter, frequency)]


def run(args: List[str]) -> str:
    res = subprocess.run(args, capture_output=True, text=True)
    out = (res.stdout or "") + (res.stderr or "")
    if res.returncode != 0:
        raise RuntimeError(out.strip() or "schtasks falhou")
    return out.strip()
