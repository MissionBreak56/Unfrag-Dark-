"""Ícone na bandeja do sistema (área de notificação) via pystray.

Os callbacks do menu rodam na thread do pystray; por isso só enviam uma ação para a fila da
interface (thread-safe), nunca mexem no Tk diretamente. Se pystray/Pillow não estiverem
instalados (ou não houver bandeja), `start()` devolve False e o app fecha normalmente."""
from __future__ import annotations

import os
from typing import Callable, Optional


# estado -> cor da bolinha no canto do ícone (None = sem bolinha)
STATE_COLORS = {"idle": None, "busy": (245, 165, 36), "done": (62, 207, 142), "error": (239, 68, 68)}


def render_icon(base, state: str):
    """Ícone do app com uma bolinha colorida no canto inferior direito conforme o estado."""
    from PIL import Image, ImageDraw

    img = base.copy().convert("RGBA")
    color = STATE_COLORS.get(state)
    if color:
        w, h = img.size
        r = max(4, w // 4)
        d = ImageDraw.Draw(img)
        box = (w - 2 * r - 1, h - 2 * r - 1, w - 1, h - 1)
        d.ellipse(box, fill=(10, 14, 30, 255))
        d.ellipse((box[0] + 2, box[1] + 2, box[2] - 2, box[3] - 2), fill=color + (255,))
    return img


class Tray:
    def __init__(self, title: str, icon_png: str, post: Callable[[str], None],
                 is_busy: Callable[[], bool]):
        self.title, self.icon_png, self.post, self.is_busy = title, icon_png, post, is_busy
        self.icon = None
        self.state = "idle"
        self._base = None

    def start(self) -> bool:
        if os.environ.get("UNFRAG_TRAY", "1") == "0":
            return False
        try:
            import pystray
            from PIL import Image

            self._base = Image.open(self.icon_png).convert("RGBA")
            image = render_icon(self._base, self.state)
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

    def set_state(self, state: str) -> None:
        """idle / busy / done / error: muda a bolinha do ícone (vale também para a próxima vez que abrir)."""
        if state == self.state:
            return
        self.state = state
        if self.icon is not None and self._base is not None:
            try:
                self.icon.icon = render_icon(self._base, state)
            except Exception:
                pass

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
