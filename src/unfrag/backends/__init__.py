"""Fábrica de backends: Windows real ou volume simulado (--demo)."""
from __future__ import annotations

import sys
from typing import List

from ..model import DriveInfo
from .base import MoveError, VolumeBackend  # noqa: F401
from .fake import FakeVolume

IS_WINDOWS = sys.platform == "win32"

_DEMO = {
    "C": dict(seed=1, total_clusters=24000, label="Sistema", kind="HDD", fs="NTFS", gb=476),
    "D": dict(seed=7, total_clusters=16000, label="Dados", kind="HDD", fs="NTFS", gb=931),
    "E": dict(seed=3, total_clusters=12000, label="Rápido", kind="SSD", fs="NTFS", gb=238),
}


def list_drives(demo: bool = False) -> List[DriveInfo]:
    if demo or not IS_WINDOWS:
        out = []
        for letter, d in _DEMO.items():
            total = d["gb"] * 10**9
            out.append(DriveInfo(letter, d["label"], d["fs"], d["kind"], total, int(total * 0.38)))
        return out
    from .windows import list_drives as _list
    return _list()


def open_volume(letter: str, demo: bool = False, delay: float = 0.0) -> VolumeBackend:
    letter = letter.rstrip(":\\/").upper()
    if demo or not IS_WINDOWS:
        d = _DEMO.get(letter, _DEMO["C"])
        return FakeVolume.demo(seed=d["seed"], total_clusters=d["total_clusters"], delay=delay,
                               ssd=(d["kind"] == "SSD"))
    from .windows import WindowsVolume
    return WindowsVolume(letter)
