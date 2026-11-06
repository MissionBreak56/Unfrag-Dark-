"""Início com o Windows, verificação de atualização, ícone da bandeja com estados e CLI (--notify, startup)."""
import io
import json
import os
import urllib.error

import pytest

from unfrag import startup, updater


def test_task_xml_runs_elevated_without_battery_limits():
    xml = startup.build_task_xml([r"C:\Program Files\Unfrag\UnfragDark.exe", "gui", "--tray"])
    assert "<RunLevel>HighestAvailable</RunLevel>" in xml and "<LogonTrigger>" in xml
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml
    assert "<Command>C:\\Program Files\\Unfrag\\UnfragDark.exe</Command>" in xml
    assert "<Arguments>gui --tray</Arguments>" in xml
    import xml.dom.minidom as md
    md.parseString(xml.split("\n", 1)[1])  # XML bem formado
    assert "&amp;" in startup.build_task_xml(["C:\\a&b.exe", "gui"])  # caracteres especiais escapados


def test_startup_not_available_off_windows():
    if os.name == "nt":
        pytest.skip("só fora do Windows")
    assert startup.is_enabled() is False
    assert startup.enable() and startup.disable()


def test_version_compare():
    assert updater.is_newer("v0.1.8", "0.1.7") and updater.is_newer("v0.2.0", "0.1.99")
    assert updater.is_newer("v0.1.10", "0.1.9")  # comparação numérica, não de texto
    assert not updater.is_newer("v0.1.7", "0.1.7") and not updater.is_newer("v0.1.6", "0.1.7")
    assert not updater.is_newer("v1.0", "1.0.0") and updater.parse_version("v2.3-beta") == (2, 3)


class _FakeOpener:
    def __init__(self, behavior):
        self.behavior = behavior

    def open(self, req, timeout=0):
        return self.behavior(req)


def _redirect(location):
    def b(req):
        raise urllib.error.HTTPError(req.full_url, 302, "Found", {"Location": location}, io.BytesIO())
    return b


def test_latest_tag_from_redirect(monkeypatch):
    for loc, want in [("https://github.com/MissionBreak56/Unfrag-Dark-/releases/tag/v0.1.9", "v0.1.9"),
                      ("/MissionBreak56/Unfrag-Dark-/releases/tag/v0.2.0?x=1", "v0.2.0"),
                      ("https://github.com/MissionBreak56/Unfrag-Dark-/releases", None)]:
        monkeypatch.setattr(updater.urllib.request, "build_opener", lambda *a, loc=loc: _FakeOpener(_redirect(loc)))
        assert updater.latest_tag() == want


def test_status_newer_current_error(monkeypatch):
    tag = {"v": "v0.9.0"}
    monkeypatch.setattr(updater, "latest_tag", lambda timeout=6.0: tag["v"])
    kind, t, url = updater.status("0.1.7")
    assert (kind, t) == ("newer", "v0.9.0") and url.endswith("/releases/tag/v0.9.0")
    tag["v"] = "v0.1.7"
    assert updater.status("0.1.7") == ("current", "v0.1.7", None)
    tag["v"] = None
    assert updater.status("0.1.7") == ("error", None, None)
    assert updater.check("0.1.7") is None


def test_latest_tag_network_errors(monkeypatch):
    def boom(req):
        raise urllib.error.URLError("sem rede")
    monkeypatch.setattr(updater.urllib.request, "build_opener", lambda *a: _FakeOpener(boom))
    assert updater.latest_tag() is None


def test_tray_icon_states_render():
    PIL = pytest.importorskip("PIL.Image")
    from unfrag.resources import resource_path
    from unfrag.tray import STATE_COLORS, render_icon
    base = PIL.open(resource_path("icon.png")).convert("RGBA")
    idle = render_icon(base, "idle")
    assert idle.tobytes() == base.tobytes()  # sem bolinha
    seen = {render_icon(base, st).tobytes() for st in STATE_COLORS}
    assert len(seen) == len(STATE_COLORS)  # cada estado tem um ícone diferente
    w, h = base.size
    r = max(4, w // 4)
    px = render_icon(base, "error").getpixel((w - r - 1, h - r - 1))  # centro da bolinha
    assert px[0] > 200 and px[1] < 120  # vermelho no canto inferior direito


def test_notify_is_noop_off_windows():
    from unfrag import notify
    if os.name == "nt":
        pytest.skip("só fora do Windows")
    assert notify.toast("x") is False


def test_cli_demo_defrag_notify_writes_last_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from unfrag import cli, notify
    sent = []
    monkeypatch.setattr(notify, "toast", lambda msg, title="Unfrag Dark", seconds=8.0: sent.append((title, msg)) or True)
    rc = cli.main(["--demo", "defrag", "C:", "--mode", "quick", "--notify"])
    assert rc == 0
    last = json.loads((tmp_path / "unfrag-dark" / "last-run.json").read_text(encoding="utf-8"))
    assert last["scheduled"] and last["ok"] and last["drive"] == "C:" and "→" in last["message"]
    assert "C: fragmentação" in last["message"] and "C::" not in last["message"]
    assert sent and sent[0][0] == "Unfrag Dark" and sent[0][1] == last["message"]
    # sem --notify: grava o resumo, mas não avisa
    sent.clear()
    cli.main(["--demo", "defrag", "C:", "--mode", "quick"])
    assert not sent


def test_cli_startup_status(capsys):
    from unfrag import cli
    if os.name == "nt":
        pytest.skip("mexe no agendador de tarefas do Windows")
    assert cli.main(["startup", "status"]) == 0
    assert "desligado" in capsys.readouterr().out
    assert cli.main(["startup", "enable"]) == 1  # fora do Windows


def test_scheduler_command_includes_notify():
    from unfrag import scheduler
    args = scheduler.build_create_args("C", "quick", "weekly", "03:00", [])
    assert "--notify" in " ".join(args)


def test_unopenable_files_are_skipped_not_failed():
    """Com a leitura da MFT aparecem arquivos travados que o método clássico já marcava como inacessíveis:
    na hora de mover, "não abre" tem que virar "ignorado", nunca "falha" (falha muda o código de saída)."""
    from unfrag.backends import open_volume
    from unfrag.engine import Engine, Options
    vol = open_volume("C", demo=True, delay=0.0)
    try:
        eng = Engine(vol)
        vol.get_file = lambda path: None  # nenhum arquivo abre
        rep = eng.defragment(Options(mode="defrag"))
        assert rep.failed == []
        assert rep.skipped and all("inacessível" in why for _p, why in rep.skipped)
        assert rep.defragmented == 0
    finally:
        vol.close()
