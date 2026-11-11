"""Fumaça da interface (só roda se houver display) e do comportamento de fechar -> bandeja."""
import os
import time

import pytest

os.environ.setdefault("UNFRAG_TRAY", "0")  # nunca cria ícone real nos testes
tk = pytest.importorskip("tkinter")

from unfrag import gui  # noqa: E402


class FakeTray:
    def __init__(self, ok=True):
        self.ok, self.started, self.stopped, self.notes, self.titles, self.states = ok, 0, 0, [], [], []
        self.state = "idle"

    def start(self):
        self.started += 1
        return self.ok

    def stop(self):
        self.stopped += 1

    def notify(self, msg, title="Unfrag Dark"):
        self.notes.append(msg)

    def set_title(self, t):
        self.titles.append(t)

    def set_state(self, st):
        self.states.append(st)


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
    amoled = palette.PRESETS["AMOLED"]  # tema AMOLED: fundo preto puro e cores válidas
    assert amoled["bg"] == "#000000" and palette.sanitize(amoled) == amoled
    assert palette.matching_preset(amoled) == "AMOLED" and palette.full(amoled)["bg"] == "#000000"
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
        sw, sh = a.root.winfo_screenwidth(), a.root.winfo_screenheight()
        want_w, want_h = min(1250, sw - 40), min(700, sh - 90)  # cabe em qualquer tela (o CI do Windows é 1024x768)
        a.root.geometry("%dx%d+37+21" % (want_w, want_h))
        for _ in range(30):
            a.root.update()
            time.sleep(0.01)
        a.quit_app()
        b = _new_app(tmp_path, monkeypatch)
        try:
            w, h, x, y = b._geom
            assert (w, h) == (want_w, want_h) and abs(x - 37) <= 20 and abs(y - 21) <= 40
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
    assert w <= sw - 40 and h <= sh - 90


# --- bandeja com estados, atualização, MFT e início escondido ----------------------------------

def test_tray_state_follows_operation(app):
    app.tray = FakeTray()
    app.start("analyze")
    assert app.tray.states and app.tray.states[0] == "busy"
    assert pump(app, lambda: not app._is_busy() and app._tray_state_cur != "busy")
    assert app.tray.states[-1] == "done"
    app.on_close()  # esconde: o ícone nasce já com o estado atual
    assert app.tray.state == "done"
    app._tray_action("show")
    assert app._tray_state_cur == "idle"  # viu o resultado


def test_tray_state_error_and_cancel(app, monkeypatch):
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)  # um diálogo modal travaria o teste
    app.tray = FakeTray()
    app._handle(("error", "falhou"))
    app._handle(("done",))
    assert app.tray.states[-1] == "error"
    app._handle(("info", "Operação cancelada."))
    app._handle(("done",))
    assert app.tray.states[-1] == "idle"


def test_error_while_hidden_notifies_instead_of_dialog(app, monkeypatch):
    shown = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: shown.append(a))
    app.tray = FakeTray()
    app.on_close()
    app._handle(("error", "Erro: algo"))
    assert not shown and app.tray.notes and "algo" in app.tray.notes[-1]


def test_update_banner_ignore_and_rebuild(app):
    app._on_update_result(("newer", "v9.9.9", "https://x/y"), False)
    assert app._update == ("v9.9.9", "https://x/y") and app.update_bar.winfo_exists()
    app._set_base(dict(app.base, accent="#ff8800"))
    for _ in range(20):
        app.root.update()
    assert app.update_bar.winfo_exists()  # sobrevive à troca de cores
    app._ignore_update("v9.9.9")
    assert app._update is None and app.settings["ignored_version"] == "v9.9.9"
    app._on_update_result(("newer", "v9.9.9", "https://x/y"), False)  # versão ignorada não reaparece sozinha
    assert app._update is None
    app._on_update_result(("newer", "v9.9.9", "https://x/y"), True)   # mas "Verificar agora" mostra
    assert app._update is not None
    from unfrag import palette
    palette.apply_palette(gui.C, palette.DEFAULT_BASE)


def test_update_messages(app):
    app._on_update_result(("current", "v0.1.7", None), True)
    assert "mais recente" in app.v_update_msg.get()
    app._on_update_result(("error", None, None), True)
    assert "Não foi possível" in app.v_update_msg.get()
    app._on_update_result(("error", None, None), False)  # verificação automática falha em silêncio
    assert app.v_update_msg.get().startswith("Não foi possível")  # mensagem anterior intacta, sem diálogo


def test_mft_option_persists_and_reaches_backend(tmp_path, monkeypatch):
    a = _new_app(tmp_path, monkeypatch)
    try:
        assert a.v_mft.get() is True  # padrão: ligado
        a.v_mft.set(False)
        seen = {}
        orig = gui.open_volume

        def spy(*args, **kw):
            v = orig(*args, **kw)
            seen["vol"] = v
            return v
        monkeypatch.setattr(gui, "open_volume", spy)
        a.start("analyze")
        assert pump(a, lambda: bool(a.v_last.get()))
        assert seen["vol"].use_mft is False
        a.quit_app()
        b = _new_app(tmp_path, monkeypatch)
        try:
            assert b.v_mft.get() is False
        finally:
            b.quit_app()
    finally:
        pass


def test_start_hidden_goes_to_tray_or_shows_window(app):
    app.tray = FakeTray()
    app.start_hidden()
    assert app._hidden and app.root.state() == "withdrawn" and app._tray_hint_shown
    app._tray_action("show")
    app.tray = FakeTray(ok=False)
    app.root.withdraw()
    app.start_hidden()  # sem bandeja: não pode ficar invisível
    assert not app._hidden and app.root.state() == "normal"


def test_last_scheduled_run_is_logged(tmp_path, monkeypatch):
    import json
    d = tmp_path / "unfrag-dark"
    d.mkdir()
    (d / "last-run.json").write_text(json.dumps({"scheduled": True, "when": "2026-11-06 03:00:00",
                                                 "message": "C: fragmentação 5.0% → 0.1%"}), encoding="utf-8")
    a = _new_app(tmp_path, monkeypatch)
    try:
        pump(a, lambda: "Última execução agendada" in a.log.get("1.0", "end"), timeout=3)
        assert "C: fragmentação 5.0% → 0.1%" in a.log.get("1.0", "end")
    finally:
        a.quit_app()


# ---- campos numéricos, SSD/TRIM e desligar -------------------------------------------------------
def test_num_tolerates_empty_or_invalid_fields():
    class Broken:
        def __init__(self, exc):
            self.exc = exc

        def get(self):
            raise self.exc

    assert gui._num(Broken(tk.TclError("expected integer but got \"\"")), int, 7) == 7
    assert gui._num(Broken(ValueError()), float, 1.5) == 1.5

    class Val:
        def __init__(self, v):
            self.v = v

        def get(self):
            return self.v

    assert gui._num(Val(150), float, 0.0, 0.0, 100.0) == 100.0
    assert gui._num(Val(-5), int, 0, 0) == 0
    assert gui._num(Val(""), int, 3) == 0  # vazio conta como 0


def test_invalid_numeric_field_does_not_break_saving_or_closing(app):
    app.v_minfrag.set("abc")   # o usuário digitou texto no campo
    app.v_large.set("")
    app._persist()             # não pode estourar (era um TclError que impedia fechar o programa)
    assert app.settings is not None
    app.quit_app()


def test_ssd_defrag_offers_trim_instead(app, monkeypatch):
    asked = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda t, m, **k: asked.append(m) or True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    app.tree.selection_set("E")   # SSD da demonstração
    app.root.update()
    app.start("defrag")
    assert asked and "SSD" in asked[0] and "TRIM" in asked[0]
    assert pump(app, lambda: app.worker is not None)
    assert pump(app, lambda: not app._is_busy())
    pump(app, lambda: "TRIM" in app.v_status.get(), timeout=5)
    assert "TRIM" in app.v_status.get()


def test_ssd_defrag_declined_does_nothing(app, monkeypatch):
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
    app.tree.selection_set("E")
    app.root.update()
    app.start("quick")
    assert app.worker is None and not app._is_busy()


def test_trim_on_hdd_is_refused(app, monkeypatch):
    shown = []
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda t, m, **k: shown.append(m))
    app.tree.selection_set("C")   # HDD da demonstração
    app.root.update()
    app.start("trim")
    assert shown and "HDD" in shown[0] and app.worker is None


def test_shutdown_only_after_a_successful_operation(app, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "shutdown_soon", lambda s=60: calls.append(s))
    app.v_shutdown.set(True)
    app._last_mode = "defrag"
    app._result_state = "error"
    app._handle(("done",))
    assert not calls                      # falhou: não desliga
    app._result_state = "idle"
    app._handle(("done",))
    assert not calls                      # foi ignorado/cancelado: não desliga
    app._last_mode = "analyze"
    app._result_state = "done"
    app._handle(("done",))
    assert not calls                      # só análise: não desliga
    app._last_mode = "defrag"
    app._handle(("done",))
    assert calls == [60]


# ---- a janela lembra o jeito exato em que foi deixada (maximizada, posição, tamanho) -------------------
import types as _types


def test_parse_geometry():
    assert gui._parse_geometry("1280x820+100+50") == (1280, 820, 100, 50)
    assert gui._parse_geometry("1280x820+-8+-8") == (1280, 820, -8, -8)   # segundo monitor, à esquerda
    assert gui._parse_geometry("900x700-1920+30") == (900, 700, -1920, 30)
    assert gui._parse_geometry("1x1+0+0") == (1, 1, 0, 0)
    assert gui._parse_geometry("lixo") is None


def _configure(app):
    app._on_root_configure(_types.SimpleNamespace(widget=app.root))


def test_maximized_survives_hiding_to_tray_and_quitting(app, monkeypatch):
    from unfrag import system
    app.tray = FakeTray()
    monkeypatch.setattr(gui, "_is_zoomed", lambda r: True)      # o usuário maximizou a janela
    _configure(app)
    assert app._zoomed
    app.on_close()                                              # X: vai para a bandeja
    assert app._hidden and system.load_settings()["window"]["zoomed"] is True
    # escondida, o Windows diz que o estado é "withdrawn" (não "zoomed"): isso não pode apagar o que já sabemos
    monkeypatch.setattr(gui, "_is_zoomed", lambda r: False)
    _configure(app)
    app._persist()                                              # ex.: gravação adiada de 1,5 s
    assert system.load_settings()["window"]["zoomed"] is True
    app._tray_action("quit")                                    # "Sair" pelo ícone da bandeja
    assert system.load_settings()["window"]["zoomed"] is True


def test_restore_from_tray_goes_back_to_maximized(app, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "_set_zoomed", lambda r, z: calls.append(z))
    app.tray = FakeTray()
    app._zoomed = True
    app.on_close()
    app.restore()
    assert calls[-1] is True       # antes forçava "normal" e a janela voltava pequena
    app._zoomed = False
    app.on_close()
    app.restore()
    assert calls[-1] is False


def test_unmaximize_remembers_the_normal_size_and_position(app, monkeypatch):
    monkeypatch.setattr(gui, "_is_zoomed", lambda r: False)
    w, h = app.root.minsize()    # cabe em qualquer tela (a do CI do Windows é pequena)
    app.root.geometry("%dx%d+23+45" % (w, h))
    app.root.update()
    _configure(app)
    assert app._geom == (w, h, 23, 45) and not app._zoomed
    monkeypatch.setattr(gui, "_is_zoomed", lambda r: True)      # maximiza: o tamanho "normal" fica guardado
    _configure(app)
    assert app._zoomed and app._geom == (w, h, 23, 45)


def test_minimized_window_keeps_last_known_state(app, monkeypatch):
    app._zoomed = True
    monkeypatch.setattr(gui, "_is_zoomed", lambda r: False)
    monkeypatch.setattr(app.root, "state", lambda *a: "iconic")  # minimizada
    _configure(app)
    assert app._zoomed            # minimizar uma janela maximizada não a "desmaximiza"


def test_saved_maximized_state_is_applied_at_startup(tmp_path, monkeypatch):
    from unfrag import system
    monkeypatch.setenv("APPDATA", str(tmp_path))
    system.save_settings({"window": dict(w=1000, h=600, x=50, y=40, zoomed=True, sash=0)})
    calls = []
    monkeypatch.setattr(gui, "_set_zoomed", lambda r, z: calls.append(z))
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem display")
    try:
        a = gui.App(root, demo=True)
        root.update()
        assert calls == [True] and a._zoomed and a._geom[:2] == (1000, 600)
        monkeypatch.setattr(gui, "_is_zoomed", lambda r: False)   # evento transitório "normal" ao abrir
        _configure(a)
        assert a._zoomed        # dentro do período de tolerância: não esquece que estava maximizada
        a._zoom_grace = 0.0     # passado o período, a mudança passa a valer
        _configure(a)
        assert not a._zoomed
    finally:
        root.destroy()
