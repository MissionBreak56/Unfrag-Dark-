import threading
import time

from unfrag import scheduler
from unfrag.backends.fake import FakeVolume
from unfrag.engine import Engine
from unfrag.system import Eta, fmt_duration


def test_fmt_duration():
    assert fmt_duration(5) == "5 s"
    assert fmt_duration(125) == "2 min 05 s"
    assert fmt_duration(3 * 3600 + 120) == "3 h 02 min"


def test_eta_estimates_and_ignores_pause():
    t = [0.0]
    eta = Eta(clock=lambda: t[0])
    assert eta.text("defrag", 0, 100) == ""          # sem dados ainda
    t[0] = 10.0
    assert eta.text("defrag", 25, 100) == "~30 s restantes"   # 10 s para 25% -> 30 s
    t[0] = 110.0
    eta.shift(100.0)                                 # 100 s de pausa não contam
    assert eta.text("defrag", 25, 100) == "~30 s restantes"
    assert eta.text("compact", 50, 100) == ""        # fase nova recomeça a contagem
    assert eta.text("compact", 100, 100) == ""       # terminou


def test_pause_blocks_then_resumes_and_stop_works_while_paused():
    eng = Engine(FakeVolume(1000))
    eng.pause.set()
    done = threading.Event()

    def run():
        eng._check_cancel()
        done.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    time.sleep(0.3)
    assert not done.is_set()          # pausado: espera
    eng.pause.clear()
    th.join(2)
    assert done.is_set()              # retomado: segue

    eng.pause.set()
    eng.cancel.set()                  # Parar durante a pausa precisa funcionar
    try:
        eng._check_cancel()
        raised = False
    except Exception as exc:
        raised = type(exc).__name__ == "Cancelled"
    assert raised


def test_boot_task_runs_as_system_without_time():
    a = scheduler.build_create_args("c:", "quick", "inicialização", "03:00")
    assert a[a.index("/SC") + 1] == "ONSTART"
    assert "/ST" not in a and a[a.index("/RU") + 1] == "SYSTEM"
    assert a[a.index("/TN") + 1].endswith("C-onstart")
    assert scheduler.build_delete_args("C", "inicialização")[-1].endswith("C-onstart")
