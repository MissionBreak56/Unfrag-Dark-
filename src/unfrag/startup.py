"""Iniciar com o Windows, minimizado na bandeja, por uma tarefa agendada de logon.

Uma entrada na chave Run não serve: o programa pede administrador e o Windows ignora em silêncio
programas elevados que iniciam por ali. A tarefa agendada com "privilégios mais altos" inicia sem
pedir confirmação a cada logon, e o XML permite rodar também na bateria."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import List, Optional
from xml.sax.saxutils import escape

TASK_NAME = "UnfragDark\\Iniciar com o Windows"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def program_command() -> List[str]:
    """Comando que inicia o app na bandeja (o .exe empacotado ou `python -m unfrag`)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "gui", "--tray"]
    return [sys.executable, "-m", "unfrag", "gui", "--tray"]


def build_task_xml(command: List[str], delay: str = "PT20S") -> str:
    exe, args = command[0], " ".join('"%s"' % a if " " in a else a for a in command[1:])
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Inicia o Unfrag Dark minimizado na bandeja do sistema.</Description></RegistrationInfo>
  <Triggers><LogonTrigger><Enabled>true</Enabled><Delay>%s</Delay></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author"><Exec><Command>%s</Command><Arguments>%s</Arguments></Exec></Actions>
</Task>
""" % (delay, escape(exe), escape(args))


def _schtasks(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks"] + args, capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW)


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        return _schtasks(["/Query", "/TN", TASK_NAME]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def enable(command: Optional[List[str]] = None) -> Optional[str]:
    """Cria a tarefa. Devolve None se deu certo, ou a mensagem de erro."""
    if os.name != "nt":
        return "disponível só no Windows"
    xml = build_task_xml(command or program_command())
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as fh:
            fh.write(xml)
            path = fh.name
        res = _schtasks(["/Create", "/F", "/TN", TASK_NAME, "/XML", path])
        if res.returncode != 0:
            return (res.stderr or res.stdout or "erro desconhecido").strip()
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


def disable() -> Optional[str]:
    if os.name != "nt":
        return "disponível só no Windows"
    try:
        res = _schtasks(["/Delete", "/F", "/TN", TASK_NAME])
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if res.returncode != 0 and is_enabled():
        return (res.stderr or res.stdout or "erro desconhecido").strip()
    return None
