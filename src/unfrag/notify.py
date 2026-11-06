"""Notificação do Windows para execuções sem janela (tarefas agendadas), via ícone temporário na bandeja."""
from __future__ import annotations

import os
import time


def toast(message: str, title: str = "Unfrag Dark", seconds: float = 8.0) -> bool:
    """Mostra um aviso e some. Devolve False se não deu (sem sessão gráfica, sem pystray...)."""
    if os.name != "nt" or os.environ.get("UNFRAG_TRAY", "1") == "0":
        return False
    try:
        import pystray
        from PIL import Image

        from .resources import resource_path

        icon = pystray.Icon("unfrag-dark-notify", Image.open(resource_path("icon.png")).convert("RGBA"), title)

        def setup(ic) -> None:
            ic.visible = True
            time.sleep(0.5)
            ic.notify(message[:250], title)
            time.sleep(seconds)
            ic.stop()

        icon.run(setup)
        return True
    except Exception:
        return False
