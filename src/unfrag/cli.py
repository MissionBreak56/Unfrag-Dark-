"""Interface de linha de comando (equivalente funcional ao utilitário de console
de desfragmentadores clássicos: listar, analisar, desfragmentar, agendar)."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .backends import IS_WINDOWS, list_drives, open_volume
from .engine import Engine, Options, Report, SsdSkipped
from .model import AnalysisResult
from .system import MAX_THREADS, auto_threads, fmt_bytes, guard_reason, shutdown_soon

PHASES = {"scan": "Analisando", "defrag": "Desfragmentando", "compact": "Compactando espaço livre",
          "toend": "Movendo arquivos grandes para o fim"}


def _progress(phase: str, done: int, total: int, msg: str) -> None:
    label = PHASES.get(phase, phase)
    tail = msg[-48:] if msg else ""
    cnt = "%d/%d" % (done, total) if total else str(done)
    sys.stdout.write("\r%-34s %-12s %-48s" % (label, cnt, tail))
    sys.stdout.flush()


def print_analysis(r: AnalysisResult, title: str = "Análise") -> None:
    cs = r.cluster_size
    print("\n== %s ==" % title)
    print("Arquivos analisados     : %d (%d não movíveis)" % (r.files, r.unmovable_files))
    print("Arquivos fragmentados   : %d (%d fragmentos no total)" % (r.fragmented_files, r.fragments))
    print("Fragmentação            : %.1f%%" % r.fragmentation_percent)
    print("Tempo da análise        : %.1f s" % r.seconds)
    print("Espaço livre            : %s em %d trechos (maior: %s)" % (
        fmt_bytes(r.free_clusters * cs), r.free_runs, fmt_bytes(r.largest_free_run * cs)))


def print_report(rep: Report) -> None:
    if rep.before:
        print_analysis(rep.before, "Antes")
    if rep.after:
        print_analysis(rep.after, "Depois")
    print("\nDesfragmentados: %d | compactados: %d | movidos para o fim: %d | clusters movidos: %d | tempo: %.1fs"
          % (rep.defragmented, rep.compacted, rep.moved_to_end, rep.clusters_moved, rep.seconds))
    for n in rep.notes:
        print("Nota:", n)
    if rep.cancelled:
        print("Operação cancelada pelo usuário.")
    if rep.skipped:
        print("Ignorados (%d):" % len(rep.skipped))
        for p, why in rep.skipped[:10]:
            print("  - %s: %s" % (p, why))
    if rep.failed:
        print("Falhas (%d):" % len(rep.failed))
        for p, why in rep.failed[:10]:
            print("  - %s: %s" % (p, why))


def _fmt_drive_row(d) -> str:
    pct_free = 100.0 * d.free_bytes / d.total_bytes if d.total_bytes else 0
    return "%s:  %-14s %-6s %-9s %10s  livre %5.1f%%  %s" % (
        d.letter, d.label[:14], d.fs, d.kind, fmt_bytes(d.total_bytes), pct_free, d.note)


def cmd_list(args) -> int:
    for d in list_drives(args.demo):
        print(_fmt_drive_row(d))
    return 0


def _open(args):
    letter = args.drive
    vol = open_volume(letter, demo=args.demo, delay=0.0)
    vol.threads = getattr(args, "threads", 0) or 0
    return vol


def cmd_analyze(args) -> int:
    with _open(args) as vol:
        eng = Engine(vol, progress=_progress)
        r = eng.analyze(args.path, not args.no_recurse)
    print_analysis(r)
    return 0


def cmd_defrag(args) -> int:
    reason = guard_reason(args.skip_battery, args.exclude or [])
    if reason:
        print("Não executado: %s." % reason)
        return 3
    if IS_WINDOWS and not args.demo:
        from .backends.windows import is_admin
        if not is_admin():
            print("Execute como Administrador (a API de desfragmentação exige).")
            return 2
    exts = [e.strip() for e in (args.move_ext or "").split(",") if e.strip()]
    opts = Options(mode=args.mode, root=args.path, recurse=not args.no_recurse,
                   move_large_mb=args.move_large, move_exts=exts, force_ssd=args.force_ssd,
                   min_fragmentation=args.min_frag)
    with _open(args) as vol:
        eng = Engine(vol, progress=_progress)
        try:
            rep = eng.defragment(opts)
        except SsdSkipped as exc:
            print("\n%s." % exc)
            return 4
        except KeyboardInterrupt:
            eng.cancel.set()
            print("\nCancelado.")
            return 130
    print()
    print_report(rep)
    if args.shutdown and not rep.cancelled:
        shutdown_soon(60)
    return 1 if rep.failed else 0


def cmd_schedule(args) -> int:
    from . import scheduler
    if args.action == "create":
        parts = []
        if args.move_large:
            parts.append("--move-large %d" % args.move_large)
        if args.threads:
            parts.append("--threads %d" % args.threads)
        extra = " ".join(parts)
        argv = scheduler.build_create_args(args.drive, args.mode, args.frequency, args.time, extra)
    else:
        argv = scheduler.build_delete_args(args.drive, args.frequency)
    if args.dry_run or not IS_WINDOWS:
        print(" ".join(argv))
        return 0
    print(scheduler.run(argv))
    return 0


def cmd_gui(args) -> int:
    from .gui import run_gui
    return run_gui(demo=args.demo)


def _threads_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--threads", type=int, default=0, metavar="N",
                        help="threads de leitura na análise, 1-%d (0 = automático: %d neste PC)"
                        % (MAX_THREADS, auto_threads()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="unfrag", description="Unfrag Dark - desfragmentador NTFS com tema escuro.")
    p.add_argument("--version", action="version", version="unfrag-dark " + __version__)
    p.add_argument("--demo", action="store_true", help="usa volumes simulados (não toca em discos reais)")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("gui", help="abre a interface gráfica").set_defaults(fn=cmd_gui)
    sub.add_parser("list", help="lista as unidades").set_defaults(fn=cmd_list)

    a = sub.add_parser("analyze", help="analisa a fragmentação (não altera nada)")
    a.add_argument("drive", help="unidade, ex.: C:")
    a.add_argument("--path", help="analisa só uma pasta ou arquivo")
    a.add_argument("--no-recurse", action="store_true")
    _threads_arg(a)
    a.set_defaults(fn=cmd_analyze)

    d = sub.add_parser("defrag", help="desfragmenta")
    d.add_argument("drive")
    d.add_argument("--mode", choices=["defrag", "quick", "freespace"], default="defrag",
                   help="defrag = completo; quick = rápido; freespace = também consolida o espaço livre")
    d.add_argument("--path", help="desfragmenta só uma pasta ou arquivo")
    d.add_argument("--no-recurse", action="store_true")
    d.add_argument("--move-large", type=int, default=0, metavar="MB",
                   help="move arquivos maiores que MB para o fim da unidade")
    d.add_argument("--move-ext", default="", help="extensões para mover ao fim, ex.: .iso,.vhdx")
    d.add_argument("--min-frag", type=float, default=0.0, metavar="PCT",
                   help="só desfragmenta se a fragmentação for pelo menos PCT%%")
    d.add_argument("--force-ssd", action="store_true", help="desfragmenta mesmo sendo SSD (não recomendado)")
    d.add_argument("--skip-battery", action="store_true", help="não executa se estiver na bateria")
    d.add_argument("--exclude", action="append", metavar="PROC.EXE",
                   help="não executa se este processo estiver rodando (repetível)")
    d.add_argument("--shutdown", action="store_true", help="desliga o PC ao terminar")
    _threads_arg(d)
    d.set_defaults(fn=cmd_defrag)

    s = sub.add_parser("schedule", help="cria/remove tarefa agendada (Windows)")
    s.add_argument("action", choices=["create", "delete"])
    s.add_argument("drive")
    s.add_argument("--frequency", choices=["diária", "semanal", "mensal"], default="semanal")
    s.add_argument("--time", default="03:00", help="HH:MM")
    s.add_argument("--mode", choices=["defrag", "quick", "freespace"], default="quick")
    s.add_argument("--move-large", type=int, default=0)
    _threads_arg(s)
    s.add_argument("--dry-run", action="store_true", help="só mostra o comando do schtasks")
    s.set_defaults(fn=cmd_schedule)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        args = parser.parse_args((argv or []) + ["gui"])
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
