import gc

import pytest


@pytest.fixture(autouse=True)
def _collect_tk_garbage_on_main_thread():
    """Os testes de GUI criam e destroem várias janelas Tk no mesmo processo. Objetos Tk
    (Font, Variable...) de janelas já destruídas têm __del__ que chama o Tcl; se o GC os
    recolher numa thread de trabalho (como a da análise), a chamada fica presa para sempre
    no Windows. Coletar aqui, na thread principal, antes e depois de cada teste evita isso."""
    gc.collect()
    yield
    gc.collect()
