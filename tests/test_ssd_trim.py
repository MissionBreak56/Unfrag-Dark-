"""SSD/TRIM, validações do agendador e da CLI, configurações, MFT corrompida e campos numéricos."""
import json
import struct
import threading

import pytest

from unfrag import mft, scheduler, system
from unfrag.backends.base import root_error
from unfrag.backends.fake import FakeVolume
from unfrag.cli import main
from unfrag.engine import Engine, Options, SsdSkipped, TrimNotApplicable
from unfrag.freespace import runs_from_bitmap

from test_mft import data_resident, file_name, record, std_info


# ---- SSD: checagem antes da análise ------------------------------------------------------
def test_ssd_is_refused_before_any_analysis(monkeypatch):
    vol = FakeVolume.demo(seed=3, ssd=True)

    def boom():
        raise AssertionError("não deveria analisar um SSD")

    monkeypatch.setattr(vol, "read_bitmap", boom)
    with pytest.raises(SsdSkipped) as exc:
        Engine(vol).defragment(Options())
    assert "TRIM" in str(exc.value)


def test_unknown_media_proceeds_with_a_warning_note():
    vol = FakeVolume.demo(seed=3, ssd=None)
    rep = Engine(vol).defragment(Options(mode="quick"))
    assert any("SSD ou HDD" in n for n in rep.notes)
    assert rep.defragmented > 0


def test_unknown_mode_is_rejected():
    with pytest.raises(ValueError):
        Engine(FakeVolume.demo(seed=3)).defragment(Options(mode="turbo"))


# ---- TRIM ---------------------------------------------------------------------------------
def test_trim_on_ssd_does_not_move_files():
    vol = FakeVolume.demo(seed=3, ssd=True)
    rep = Engine(vol).trim()
    assert rep.trimmed and not rep.failed and not rep.cancelled
    assert vol.trims == 1 and vol.moves == 0


def test_trim_refused_on_hdd():
    vol = FakeVolume.demo(seed=1, ssd=False)
    with pytest.raises(TrimNotApplicable):
        Engine(vol).trim()
    assert vol.trims == 0


def test_trim_on_unknown_media_notes_it():
    vol = FakeVolume.demo(seed=1, ssd=None)
    rep = Engine(vol).trim()
    assert rep.trimmed and any("tipo de mídia" in n for n in rep.notes)


def test_trim_cancelled_and_failed():
    cancel = threading.Event()
    cancel.set()
    rep = Engine(FakeVolume.demo(seed=3, ssd=True), cancel=cancel).trim()
    assert rep.cancelled and not rep.trimmed

    vol = FakeVolume.demo(seed=3, ssd=True)
    vol.retrim = lambda cancel=None: (False, "erro 0x8900002A")
    rep = Engine(vol).trim()
    assert rep.failed and "0x8900002A" in rep.failed[0][1] and not rep.trimmed


def test_backend_without_trim_is_not_applicable(monkeypatch):
    monkeypatch.setattr(FakeVolume, "supports_trim", property(lambda self: False))
    with pytest.raises(TrimNotApplicable):
        Engine(FakeVolume.demo(seed=3, ssd=True)).trim()


def test_cli_trim(capsys):
    assert main(["--demo", "trim", "E:"]) == 0
    assert "concluída" in capsys.readouterr().out
    assert main(["--demo", "trim", "C:"]) == 4  # HDD
    assert "HDD" in capsys.readouterr().out


# ---- agendador -----------------------------------------------------------------------------
def test_scheduler_trim_mode_runs_the_trim_command():
    a = scheduler.build_create_args("e:", "trim", "semanal", "03:00")
    assert " trim E: --skip-battery --notify" in a[-1] and "defrag" not in a[-1]


@pytest.mark.parametrize("letter,mode,freq,when", [
    ("C:\\x", "quick", "semanal", "03:00"),
    ("CD", "quick", "semanal", "03:00"),
    ("C:", "turbo", "semanal", "03:00"),
    ("C:", "quick", "quinzenal", "03:00"),
    ("C:", "quick", "semanal", "25:00"),
    ("C:", "quick", "semanal", "3h"),
])
def test_scheduler_rejects_bad_input(letter, mode, freq, when):
    with pytest.raises(ValueError):
        scheduler.build_create_args(letter, mode, freq, when)


def test_cli_schedule_reports_bad_time(capsys):
    assert main(["schedule", "create", "C:", "--time", "99:99", "--dry-run"]) == 2
    assert "horário inválido" in capsys.readouterr().out
    assert main(["schedule", "create", "C:", "--mode", "trim", "--dry-run"]) == 0


# ---- análise parcial: só caminhos da própria unidade -------------------------------------------
def test_root_must_be_on_the_same_drive():
    assert root_error("C:\\Users", "C") is None
    assert root_error("c:\\Users", "C:") is None
    assert "não está na unidade C:" in root_error("D:\\dados", "C")
    assert root_error("\\\\?\\D:\\dados", "C")
    assert root_error("\\\\?\\C:\\dados", "C") is None
    assert "rede" in root_error("\\\\servidor\\pasta", "C")


# ---- configurações -----------------------------------------------------------------------------
def test_settings_roundtrip_is_atomic_and_tolerates_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    system.save_settings({"threads": 4})
    assert system.load_settings() == {"threads": 4}
    assert not list(tmp_path.rglob("*.tmp"))  # nada de arquivo temporário sobrando
    with open(system.settings_path(), "w", encoding="utf-8") as fh:
        json.dump([1, 2, 3], fh)  # JSON válido, mas não é um dicionário
    assert system.load_settings() == {}
    with open(system.settings_path(), "w", encoding="utf-8") as fh:
        fh.write("{cortado no meio")
    assert system.load_settings() == {}
    system.save_last_run({"ok": True, "message": "ação concluída"})
    assert system.load_last_run()["message"] == "ação concluída"


# ---- MFT: um registro corrompido não derruba a análise ---------------------------------------------
def _corrupt_filename_offset(rec: bytes) -> bytearray:
    out = bytearray(rec)
    pos = struct.unpack_from("<H", out, 20)[0]
    while True:
        atype, alen = struct.unpack_from("<II", out, pos)
        if atype == mft.ATTR_FILE_NAME:
            struct.pack_into("<H", out, pos + 20, 0x3F0)  # o conteúdo "fica" quase no fim do registro
            return out
        pos += alen


def test_corrupt_filename_attribute_is_skipped_not_fatal():
    good = record([std_info(), file_name(5, "ok.txt"), data_resident(b"x")])
    rec = _corrupt_filename_offset(good)
    assert mft.parse_record(bytearray(rec)) is None
    scanner = mft.MftScanner("C:\\")
    scanner.feed(20, bytearray(rec))          # não pode levantar exceção
    scanner.feed(21, bytearray(b"FILE" + b"\xff" * 1020))
    assert list(scanner.results()) == []


# ---- espaço livre: bitmap mais curto que o volume --------------------------------------------------
def test_short_bitmap_never_invents_free_space():
    assert runs_from_bitmap(b"\x00", 20) == [(0, 8)]
    assert runs_from_bitmap(b"\x00\x00\x00", 20) == [(0, 20)]
    assert runs_from_bitmap(b"\xff", 20) == []
