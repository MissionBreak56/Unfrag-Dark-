"""Ícone na bandeja do sistema (área de notificação) via pystray.

Os callbacks do menu rodam na thread do pystray; por isso só enviam uma ação para a fila da
interface (thread-safe), nunca mexem no Tk diretamente. Se pystray/Pillow não estiverem
instalados (ou não houver bandeja), `start()` devolve False e o app fecha normalmente."""
from __future__ import annotations

import os
from typing import Callable, Optional


class Tray:
    def __init__(self, title: str, icon_png: str, post: Callable[[str], None],
                 is_busy: Callable[[], bool]):
        self.title, self.icon_png, self.post, self.is_busy = title, icon_png, post, is_busy
        self.icon = None

    def start(self) -> bool:
        if os.environ.get("UNFRAG_TRAY", "1") == "0":
            return False
        try:
            import pystray
            from PIL import Image

            image = Image.open(self.icon_png).convert("RGBA")
            menu = pystray.Menu(
                pystray.MenuItem("Abrir Unfrag Dark", lambda icon, item: self.post("show"), default=True),
                pystray.MenuItem("Parar operação", lambda icon, item: self.post("stop"),
                                 enabled=lambda item: bool(self.is_busy())),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Sair", lambda icon, item: self.post("quit")),
            )
            self.icon = pystray.Icon("unfrag-dark", image, self.title, menu)
            self.icon.run_detached()
            return True
        except Exception:
            self.icon = None
            return False

    def set_title(self, title: str) -> None:
        if self.icon is not None:
            try:
                self.icon.title = title[:120]  # limite do Windows: 127 caracteres
            except Exception:
                pass

    def notify(self, message: str, title: str = "Unfrag Dark") -> None:
        if self.icon is not None:
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
