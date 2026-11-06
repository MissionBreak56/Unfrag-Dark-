"""Validação em um Windows de verdade (roda no CI do GitHub: NTFS real, como administrador).

1. Análise clássica x leitura da MFT: mesmo volume, comparação arquivo a arquivo (extents idênticos?)
   e tempo de cada método.
2. Desfragmentação real de uma pasta de teste com arquivos fragmentados de propósito (conteúdo
   conferido por hash antes e depois).
3. Tarefa agendada de início com o Windows (criar / consultar / apagar), verificação de versão e aviso.

Os resultados saem no log e como anotações (::notice / ::error), para serem lidos pela API do GitHub."""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from unfrag import startup, updater  # noqa: E402
from unfrag.backends import open_volume  # noqa: E402
from unfrag.backends.windows import is_admin  # noqa: E402
from unfrag.engine import Engine, Options  # noqa: E402

LETTER = os.environ.get("VALIDATE_DRIVE", "C")
TESTDIR = "%s:\\unfrag_validate" % LETTER
FAILURES = []
SUMMARY = []


def esc(msg: str) -> str:
    return msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def notice(title: str, msg: str) -> None:
    """Só registra e imprime: o GitHub limita as anotações (10 por passo), então o resumo sai todo junto no fim."""
    print("%s: %s" % (title, msg), flush=True)
    SUMMARY.append("%s: %s" % (title, msg))


def fail(title: str, msg: str) -> None:
    print("::error title=%s::%s" % (title, esc(msg)), flush=True)
    FAILURES.append("%s: %s" % (title, msg))


def group(name: str) -> None:
    print("::group::%s" % name, flush=True)


def endgroup() -> None:
    print("::endgroup::", flush=True)


def make_fragmented_files(n: int = 12, rounds: int = 60, chunk: int = 64 * 1024) -> dict:
    """Escreve n arquivos em revezamento (anexando um pedaço de cada vez): o NTFS intercala os clusters."""
    shutil.rmtree(TESTDIR, ignore_errors=True)
    os.makedirs(TESTDIR)
    handles = [open(os.path.join(TESTDIR, "frag%02d.bin" % i), "wb", buffering=0) for i in range(n)]
    digests = [hashlib.sha256() for _ in range(n)]
    for r in range(rounds):
        for i, fh in enumerate(handles):
            data = bytes([(i * 31 + r) % 251]) * chunk
            fh.write(data)
            os.fsync(fh.fileno())
            digests[i].update(data)
    for fh in handles:
        fh.close()
    return {os.path.join(TESTDIR, "frag%02d.bin" % i): d.hexdigest() for i, d in enumerate(digests)}


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def ext_key(info) -> tuple:
    return tuple((e.vcn, e.lcn, e.length) for e in info.extents)


def analyze(use_mft: bool, root=None):
    vol = open_volume(LETTER, demo=False)
    vol.threads = 0
    vol.use_mft = use_mft
    try:
        eng = Engine(vol)
        t = time.perf_counter()
        res = eng.analyze(root, True)
        dt = time.perf_counter() - t
        return eng, res, dt, getattr(vol, "mft_used", False), getattr(vol, "mft_error", None)
    finally:
        vol.close()


def step_compare() -> None:
    group("1. Análise clássica x MFT (volume inteiro)")
    try:
        files = make_fragmented_files()
        notice("Pasta de teste", "%d arquivos criados em %s" % (len(files), TESTDIR))
        eng_c, res_c, t_c, _used, _err = analyze(False)
        eng_m, res_m, t_m, used, err = analyze(True)
        notice("Tempo da análise (volume %s:)" % LETTER,
               "clássico %.1f s | MFT %.1f s (%.1fx) | MFT usada: %s%s" % (
                   t_c, t_m, t_c / max(t_m, 0.001), used, "" if used else " (motivo: %s)" % err))
        notice("Totais", "clássico: %d arquivos, %d fragmentados, %d fragmentos | MFT: %d arquivos, %d fragmentados, %d fragmentos" % (
            res_c.files, res_c.fragmented_files, res_c.fragments, res_m.files, res_m.fragmented_files, res_m.fragments))
        if not used:
            fail("MFT não foi usada", str(err))
            return
        fc = {p.lower(): f for p, f in eng_c.files.items()}
        fm = {p.lower(): f for p, f in eng_m.files.items()}
        only_c = sorted(set(fc) - set(fm))
        only_m = sorted(set(fm) - set(fc))
        common = set(fc) & set(fm)
        mism = sorted(p for p in common if ext_key(fc[p]) != ext_key(fm[p]))
        notice("Comparação", "em comum: %d | só no clássico: %d | só na MFT: %d | extents diferentes: %d" % (
            len(common), len(only_c), len(only_m), len(mism)))
        for title, lst in (("só no clássico", only_c), ("só na MFT", only_m)):
            if lst:
                notice("Amostra " + title, "\n".join(lst[:25]))
        if mism:
            lines = ["%s\n   clássico=%s\n   MFT=%s" % (p, ext_key(fc[p])[:6], ext_key(fm[p])[:6]) for p in mism[:12]]
            notice("Amostra extents diferentes", "\n".join(lines))
        # a pasta de teste foi criada antes das duas análises e não mudou: tem que ser idêntica
        pref = TESTDIR.lower() + "\\"
        in_c = {p: ext_key(f) for p, f in fc.items() if p.startswith(pref)}
        in_m = {p: ext_key(f) for p, f in fm.items() if p.startswith(pref)}
        frag = sum(1 for k in in_c.values() if len(k) > 1)
        notice("Pasta de teste", "%d arquivos; %d com mais de 1 extent (fragmentados)" % (len(in_c), frag))
        if in_c != in_m:
            fail("Pasta de teste difere entre clássico e MFT", "clássico=%s\nMFT=%s" % (
                {k[-12:]: v[:4] for k, v in in_c.items()}, {k[-12:]: v[:4] for k, v in in_m.items()}))
        elif not in_c:
            fail("Pasta de teste vazia", "nenhum arquivo da pasta de teste apareceu na análise")
        total = max(1, len(common))
        if len(mism) / total > 0.01:
            fail("Muitos extents diferentes", "%.2f%% dos arquivos em comum" % (100.0 * len(mism) / total))
        if (len(only_c) + len(only_m)) / max(1, len(fc)) > 0.05:
            fail("Muitos arquivos só em um método", "clássico=%d MFT=%d" % (len(only_c), len(only_m)))
    except Exception:
        fail("Comparação falhou com exceção", traceback.format_exc()[-1500:])
    finally:
        endgroup()


def step_defrag() -> None:
    group("2. Desfragmentação real da pasta de teste")
    try:
        before_hash = {os.path.join(TESTDIR, n): sha(os.path.join(TESTDIR, n)) for n in os.listdir(TESTDIR)}
        eng0, res0, _t, _u, _e = analyze(False, TESTDIR)
        f0 = {p.lower(): len(ext_key(f)) for p, f in eng0.files.items()}
        vol = open_volume(LETTER, demo=False)
        vol.threads = 0
        try:
            rep = Engine(vol).defragment(Options(mode="defrag", root=TESTDIR, force_ssd=True))
        finally:
            vol.close()
        notice("Desfragmentação", "desfragmentados=%d | falhas=%d | ignorados=%d | clusters movidos=%d | tempo=%.1fs" % (
            rep.defragmented, len(rep.failed), len(rep.skipped), rep.clusters_moved, rep.seconds))
        if rep.failed:
            fail("Falhas na desfragmentação", "\n".join("%s: %s" % x for x in rep.failed[:10]))
        eng1, res1, _t, _u, _e = analyze(False, TESTDIR)
        f1 = {p.lower(): len(ext_key(f)) for p, f in eng1.files.items()}
        notice("Fragmentos na pasta de teste", "antes=%d depois=%d (arquivos fragmentados: %d -> %d)" % (
            sum(f0.values()), sum(f1.values()), sum(1 for v in f0.values() if v > 1), sum(1 for v in f1.values() if v > 1)))
        if sum(f1.values()) > sum(f0.values()):
            fail("A desfragmentação aumentou os fragmentos", "%d -> %d" % (sum(f0.values()), sum(f1.values())))
        bad = [os.path.basename(p) for p, h in before_hash.items() if sha(p) != h]
        if bad:
            fail("CONTEÚDO ALTERADO pela desfragmentação", ", ".join(bad))
        else:
            notice("Integridade", "o conteúdo (SHA-256) dos %d arquivos continua idêntico" % len(before_hash))
    except Exception:
        fail("Desfragmentação falhou com exceção", traceback.format_exc()[-1500:])
    finally:
        endgroup()


def step_misc() -> None:
    group("3. Tarefa de início, atualização e aviso")
    try:
        err = startup.enable([sys.executable, "-m", "unfrag", "gui", "--tray"])
        on = startup.is_enabled()
        notice("Início com o Windows", "criar: %s | consulta após criar: %s" % (err or "ok", on))
        if err or not on:
            fail("Tarefa de início não foi criada", str(err))
        err = startup.disable()
        off = not startup.is_enabled()
        notice("Início com o Windows", "apagar: %s | consulta após apagar (ausente): %s" % (err or "ok", off))
        if err or not off:
            fail("Tarefa de início não foi apagada", str(err))
    except Exception:
        fail("Teste da tarefa de início falhou", traceback.format_exc()[-1500:])
    try:
        kind, tag, url = updater.status("0.0.1")
        notice("Verificação de atualização", "%s %s %s" % (kind, tag, url))
        if kind != "newer":
            fail("Verificação de versão não funcionou", "%s %s" % (kind, tag))
    except Exception:
        fail("Verificação de versão falhou", traceback.format_exc()[-1500:])
    try:
        from unfrag import notify
        t = time.perf_counter()
        ok = notify.toast("Teste de notificação do Unfrag Dark (CI)", seconds=2.0)
        notice("Aviso do Windows (toast)", "resultado=%s em %.1fs (sem sessão gráfica no CI, False é esperado)" % (ok, time.perf_counter() - t))
    except Exception:
        notice("Aviso do Windows (toast)", "exceção (esperada no CI):\n" + traceback.format_exc()[-500:])
    finally:
        endgroup()


def main() -> int:
    notice("Ambiente", "Python %s | administrador: %s | unidade %s:" % (sys.version.split()[0], is_admin(), LETTER))
    if not is_admin():
        fail("Sem administrador", "a validação precisa de administrador")
        return 1
    step_compare()
    if os.path.isdir(TESTDIR):
        step_defrag()
    step_misc()
    shutil.rmtree(TESTDIR, ignore_errors=True)
    print("\n===== RESUMO =====")
    print("\n".join(SUMMARY))
    print("::notice title=Resumo da validacao::%s" % esc("\n".join(l[:600] for l in SUMMARY)), flush=True)
    if FAILURES:
        print("\n===== FALHAS =====")
        print("\n".join(FAILURES))
        return 1
    print("\nTudo validado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
