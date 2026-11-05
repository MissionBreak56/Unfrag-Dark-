"""Garante uma única janela do programa por sessão.

Windows: mutex nomeado (detecta a outra instância) + evento nomeado (a segunda instância
acorda a primeira para ela se mostrar, mesmo se estiver escondida na bandeja).
Outros sistemas: trava de arquivo (flock) — só para desenvolvimento/testes."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from typing import Callable, Optional

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0


def _load_k32():
    import ctypes
    from ctypes import wintypes as wt

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    k32.CreateEventW.restype = ctypes.c_void_p
    k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
    k32.SetEvent.argtypes = [ctypes.c_void_p]
    k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wt.DWORD]
    k32.WaitForSingleObject.restype = wt.DWORD
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


class SingleInstance:
    def __init__(self, key: str = "main"):
        self.key = key
        self._k32 = None
        self._mutex = None
        self._event = None
        self._fd = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # --- aquisição -----------------------------------------------------------
    def acquire(self) -> bool:
        """True se esta é a única instância; False se já existe outra aberta."""
        if sys.platform == "win32":
            return self._acquire_windows()
        return self._acquire_file()

    def _acquire_windows(self) -> bool:
        import ctypes
        self._k32 = _load_k32()
        self._mutex = self._k32.CreateMutexW(None, False, "Local\\UnfragDark.%s.Single" % self.key)
        already = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        self._event = self._k32.CreateEventW(None, False, False, "Local\\UnfragDark.%s.Show" % self.key)
        if already:
            return False
        return bool(self._mutex)  # sem mutex (erro raro): não bloqueia o usuário

    def _acquire_file(self) -> bool:
        try:
            import fcntl
        except ImportError:  # sistema sem fcntl: não restringe
            return True
        path = os.path.join(tempfile.gettempdir(), "unfrag-dark-%s.lock" % self.key)
        self._fd = open(path, "a+")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self._fd.close()
            self._fd = None
            return False

    # --- avisar / ouvir -----------------------------------------------------------
    def signal_existing(self) -> None:
        """Pede à instância já aberta que se mostre (chamado pela segunda instância)."""
        if self._k32 is not None and self._event:
            self._k32.SetEvent(self._event)

    def listen(self, callback: Callable[[], None]) -> None:
        """Chama `callback` (em outra thread) sempre que uma segunda instância pedir para aparecer."""
        if self._k32 is None or not self._event:
            return

        def loop() -> None:
            while not self._stop.is_set():
                if self._k32.WaitForSingleObject(self._event, 400) == WAIT_OBJECT_0:
                    try:
                        callback()
                    except Exception:
                        pass

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._k32 is not None:
            for h in (self._event, self._mutex):
                if h:
                    try:
                        self._k32.CloseHandle(h)
                    except Exception:
                        pass
            self._event = self._mutex = None
        if self._fd is not None:
            try:
                self._fd.close()
            except OSError:
                pass
            self._fd = None


def show_already_running() -> None:
    """Aviso ao usuário (a segunda instância ainda não tem janela própria)."""
    text = "O Unfrag Dark já está aberto.\n\nA janela que já estava em execução foi trazida para a frente."
    if sys.platform == "win32":
        try:
            import ctypes
            # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
            ctypes.windll.user32.MessageBoxW(0, text, "Unfrag Dark", 0x0 | 0x40 | 0x10000 | 0x40000)
            return
        except Exception:
            pass
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("Unfrag Dark", text)
        r.destroy()
    except Exception:
        print(text, file=sys.stderr)
