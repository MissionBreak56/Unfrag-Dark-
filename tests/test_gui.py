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


def _stacks():
    import sys
    import threading
    import traceback
    names = {t.ident: t.name for t in threading.enumerate()}
    return "\n".join("--- thread %s\n%s" % (names.get(i, i), "".join(traceback.format_stack(f)[-6:]))
                     for i, f in sys._current_frames().items())


def pump(a, cond, timeout=15):
    end = time.time() + timeout
    while time.time() < end and not cond():
        a.root.update()
        time.sleep(0.02)
    a.root.update()
    ok = cond()
    if not ok:  # diagnóstico: mostra onde cada thread está parada
        print("PUMP TIMEOUT\n" + _stacks())
        raise AssertionError("pump expirou\n" + _stacks())
    return ok


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


# --- cores personalizadas e posição da janela ---------------------------------------------

def _new_app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem display")
    a = gui.App(root, demo=True)
    root.update()
    return a


def test_palette_defaults_and_sanitize():
    from unfrag import palette
    full = palette.full(palette.DEFAULT_BASE)
    assert full["edge"] == "#2457c9" and full["free"] == "#6c7589"  # livres em cinza por padrão
    assert palette.sanitize({"free": "vermelho", "used": "#ABCDEF"})["used"] == "#abcdef"
    assert palette.sanitize({"free": "vermelho"})["free"] == palette.DEFAULT_BASE["free"]
    assert palette.sanitize(None) == palette.DEFAULT_BASE
    changed = dict(palette.DEFAULT_BASE, free="#123456")  # trocar os livres não mexe no resto do tema
    assert palette.full(changed)["edge"] == full["edge"]
    assert palette.full(dict(palette.DEFAULT_BASE, accent="#ff0000"))["edge"] != full["edge"]
    assert palette.matching_preset(palette.PRESETS["Grafite"]) == "Grafite"
    assert palette.matching_preset(changed) == "Personalizado"


def test_free_cells_are_gray_by_default(app):
    assert gui.cell_colors()[0] == "#6c7589"
    app.start("analyze")
    assert pump(app, lambda: bool(app.v_last.get()))
    assert "#6c7589" in [app.canvas.itemcget(i, "fill") for i in app.canvas.find_all()]


def test_color_change_rebuilds_ui_and_persists(tmp_path, monkeypatch):
    a = _new_app(tmp_path, monkeypatch)
    try:
        a.start("analyze")
        assert pump(a, lambda: bool(a.v_last.get()))
        a.nb.select(1)
        a._set_base({**a.base, "free": "#00ff00", "accent": "#ff8800"})
        for _ in range(30):
            a.root.update()
            time.sleep(0.01)
        assert a.nb.current == 1  # continua na mesma aba
        assert gui.C["free"] == "#00ff00" and gui.C["accent"] == "#ff8800"
        assert "#00ff00" in [a.canvas.itemcget(i, "fill") for i in a.canvas.find_all()]  # mapa redesenhado
        assert a.v_preset.get() == "Personalizado"
        a.quit_app()
        b = _new_app(tmp_path, monkeypatch)  # "reabre o programa"
        try:
            assert b.base["free"] == "#00ff00" and b.base["accent"] == "#ff8800"
            assert gui.C["free"] == "#00ff00"
        finally:
            b.quit_app()
    finally:
        from unfrag import palette
        palette.apply_palette(gui.C, palette.DEFAULT_BASE)


def test_preset_and_restore_default(app):
    from unfrag import palette
    try:
        app.v_preset.set("Violeta")
        app._pick_preset()
        app.root.update()
        assert gui.C["bg"] == palette.PRESETS["Violeta"]["bg"]
        app._set_base(dict(palette.DEFAULT_BASE))
        app.root.update()
        assert app.v_preset.get() == "Azul (padrão)" and gui.C["free"] == "#6c7589"
    finally:
        palette.apply_palette(gui.C, palette.DEFAULT_BASE)


def test_window_position_saved_and_restored(tmp_path, monkeypatch):
    a = _new_app(tmp_path, monkeypatch)
    try:
        a.root.geometry("1250x700+37+21")
        for _ in range(30):
            a.root.update()
            time.sleep(0.01)
        a.quit_app()
        b = _new_app(tmp_path, monkeypatch)
        try:
            w, h, x, y = b._geom
            assert (w, h) == (1250, 700) and abs(x - 37) <= 20 and abs(y - 21) <= 40
        finally:
            b.quit_app()
    finally:
        pass


def test_saved_window_is_clamped_to_screen(tmp_path, monkeypatch):
    import json
    d = tmp_path / "unfrag-dark"
    d.mkdir()
    (d / "settings.json").write_text(json.dumps({"window": {"w": 5000, "h": 4000, "x": 9000, "y": 9000}}))
    a = _new_app(tmp_path, monkeypatch)
    try:
        sw, sh = a.root.winfo_screenwidth(), a.root.winfo_screenheight()
        w, h, x, y = a._geom
        assert w <= sw and h <= sh and 0 <= x <= sw - 100 and 0 <= y <= sh - 100  # nunca fora da tela
    finally:
        a.quit_app()


def test_default_window_fits_small_screen(app):
    sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
    w, h, _x, _y = app._geom
    assert w <= sw - 40 + 1 and h <= sh - 90 + 1
