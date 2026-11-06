"""Caminho dos arquivos de dados (ícones), no código-fonte e no .exe empacotado."""
import os
import sys


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "unfrag", "assets", name)
    return os.path.join(os.path.dirname(__file__), "assets", name)
