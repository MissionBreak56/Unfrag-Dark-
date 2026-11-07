"""Notificação do Windows para execuções sem janela (tarefas agendadas), via ícone temporário na bandeja."""
from __future__ import annotations

import os
import threading
import time


def _is_windows() -> bool:
    return os.name == "nt"


def toast(message: str, title: str = "Unfrag Dark", seconds: float = 8.0, grace: float = 10.0) -> bool:
    """Mostra um aviso e some. Devolve False se não deu (sem sessão gráfica, sem pystray...).

    Nunca trava: o ícone roda em uma thread à parte e, se a bandeja não responder a tempo, desistimos
    (uma tarefa agendada não pode ficar presa esperando um aviso)."""
    if not _is_windows() or os.environ.get("UNFRAG_TRAY", "1") == "0":
        return False
    try:
        import pystray
        from PIL import Image

        from .resources import resource_path

        icon = pystray.Icon("unfrag-dark-notify", Image.open(resource_path("icon.png")).convert("RGBA"), title)
    except Exception:
        return False
    state = {"shown": False}

    def setup(ic) -> None:
        try:
            ic.visible = True
            time.sleep(0.5)
            ic.notify(message[:250], title)
            state["shown"] = True
            time.sleep(seconds)
        except Exception:
            pass
        finally:
            try:
                ic.stop()
            except Exception:
                pass

    def run() -> None:
        try:
            icon.run(setup)
        except Exception:
            pass

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=seconds + grace)
    if t.is_alive():
        try:
            icon.stop()
        except Exception:
            pass
        return False
    return state["shown"]
