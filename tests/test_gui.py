"""Fumaça da interface (só roda se houver display) e do comportamento de fechar -> bandeja."""
import os
import time

import pytest

os.environ.setdefault("UNFRAG_TRAY", "0")  # nunca cria ícone real nos testes
tk = pytest.importorskip("tkinter")

from unfrag import gui  # noqa: E402


class FakeTray:
    def __init__(self, ok=True):
        self.ok, self.started, self.stopped, self.notes, self.titles = ok, 0, 0, [], []

    def start(self):
        self.started += 1
        return self.ok

    def stop(self):
        self.stopped += 1

    def notify(self, msg, title="Unfrag Dark"):
        self.notes.append(msg)

    def set_title(self, t):
        self.titles.append(t)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))  # configurações isoladas: o teste não mexe nas do usuário
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem display")
    a = gui.App(root, demo=True)
    root.update()
    yield a
    try:
        root.destroy()
    except tk.TclError:
        pass


def pump(a, cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end and not cond():
        a.root.update()
        time.sleep(0.02)
    a.root.update()
    return cond()


def test_close_hides_to_tray_and_restore(app):
    app.tray = FakeTray()
    app.on_close()
    assert app._hidden and app.root.state() == "withdrawn"
    app._tray_action("show")
    assert not app._hidden and app.root.state() == "normal"
    assert app.tray.stopped == 1


def test_close_quits_when_tray_unavailable(app):
    app.tray = FakeTray(ok=False)
    app.on_close()
    with pytest.raises(tk.TclError):  # janela destruída: nunca fica sem janela e sem ícone
        app.root.state()


def test_close_quits_when_option_disabled(app):
    app.tray = FakeTray()
    app.v_tray.set(False)
    app.on_close()
    assert app.tray.started == 0
    with pytest.raises(tk.TclError):
        app.root.state()


def test_tray_quit_action_exits(app):
    app.tray = FakeTray()
    app.on_close()
    app._tray_action("quit")
    with pytest.raises(tk.TclError):
        app.root.state()


def test_operation_keeps_running_while_hidden_and_notifies(app):
    app.tray = FakeTray()
    app.root.after(10, lambda: app.start("analyze"))
    assert pump(app, lambda: app.worker is not None)
    app.on_close()
    assert app._hidden
    assert pump(app, lambda: not app._is_busy())
    pump(app, lambda: app.tray.notes, timeout=3)
    assert app.tray.notes, "deveria avisar na bandeja que a operação terminou"
    assert app.stats["frag"].get() != "—"


def test_tray_stop_action_cancels(app):
    app.cancel.clear()
    app._tray_action("stop")
    assert app.cancel.is_set()


def test_map_draws_all_states(app):
    app.start("analyze")
    assert pump(app, lambda: bool(app.v_last.get()))
    app._draw_map()
    assert app.cells and set(app.cells) <= {0, 1, 2, 3, 4}
    assert app.v_last.get().startswith("Última análise: ")
