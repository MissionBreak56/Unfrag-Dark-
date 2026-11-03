"""Interface gráfica (Tkinter) com tema escuro."""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import __version__, diskmap, scheduler
from .backends import IS_WINDOWS, list_drives, open_volume
from .engine import Cancelled, Engine, Options, Report, SsdSkipped
from .model import AnalysisResult, DriveInfo
from .system import MAX_THREADS, auto_threads, fmt_bytes, guard_reason, load_settings, save_settings, shutdown_soon

C = dict(bg="#101216", panel="#171a20", panel2="#1f232b", line="#2a2f39", fg="#e7e9ee",
         muted="#8d94a5", accent="#4c8dff", accent2="#3a6fd0", ok="#3ecf8e", warn="#f5a524",
         bad="#ef5350", free="#242934", used="#3b6fd6", frag="#ef5350", unmov="#f5a524")
CELL_COLORS = {diskmap.FREE: C["free"], diskmap.USED: C["used"],
               diskmap.FRAGMENTED: C["frag"], diskmap.UNMOVABLE: C["unmov"]}
PHASES = {"scan": "Analisando arquivos", "defrag": "Desfragmentando", "compact": "Compactando espaço livre",
          "toend": "Movendo arquivos grandes para o fim"}
FONT = ("Segoe UI", 10)


def _dark_titlebar(root: tk.Tk) -> None:
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        root.update()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4)
    except Exception:
        pass


def apply_theme(root: tk.Tk) -> None:
    root.configure(bg=C["bg"])
    root.option_add("*Font", FONT)
    root.option_add("*TCombobox*Listbox.background", C["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent2"])
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(".", background=C["bg"], foreground=C["fg"], fieldbackground=C["panel2"],
                bordercolor=C["line"], lightcolor=C["line"], darkcolor=C["line"],
                troughcolor=C["panel"], font=FONT, focuscolor=C["accent"])
    s.configure("TFrame", background=C["bg"])
    s.configure("Panel.TFrame", background=C["panel"])
    s.configure("TLabel", background=C["bg"], foreground=C["fg"])
    s.configure("Panel.TLabel", background=C["panel"])
    s.configure("Muted.TLabel", foreground=C["muted"])
    s.configure("Big.TLabel", font=("Segoe UI Semibold", 20), background=C["panel"])
    s.configure("TButton", background=C["panel2"], foreground=C["fg"], padding=(14, 7),
                borderwidth=1, relief="flat")
    s.map("TButton", background=[("active", C["line"]), ("disabled", C["panel"])],
          foreground=[("disabled", C["muted"])])
    s.configure("Accent.TButton", background=C["accent"], foreground="#ffffff")
    s.map("Accent.TButton", background=[("active", C["accent2"]), ("disabled", C["panel"])])
    s.configure("Danger.TButton", background="#7a2d2d", foreground="#ffffff")
    s.map("Danger.TButton", background=[("active", "#9b3a3a"), ("disabled", C["panel"])])
    s.configure("TCheckbutton", background=C["bg"], foreground=C["fg"], indicatorbackground=C["panel2"])
    s.map("TCheckbutton", background=[("active", C["bg"])], indicatorbackground=[("selected", C["accent"])])
    s.configure("TEntry", fieldbackground=C["panel2"], foreground=C["fg"], insertcolor=C["fg"])
    s.configure("TSpinbox", fieldbackground=C["panel2"], foreground=C["fg"], arrowcolor=C["fg"])
    s.configure("TCombobox", fieldbackground=C["panel2"], foreground=C["fg"], arrowcolor=C["fg"],
                background=C["panel2"])
    s.map("TCombobox", fieldbackground=[("readonly", C["panel2"])], foreground=[("readonly", C["fg"])])
    s.configure("Treeview", background=C["panel"], fieldbackground=C["panel"], foreground=C["fg"],
                rowheight=26, borderwidth=0)
    s.map("Treeview", background=[("selected", C["accent2"])], foreground=[("selected", "#ffffff")])
    s.configure("Treeview.Heading", background=C["panel2"], foreground=C["muted"], relief="flat",
                padding=(8, 6), font=("Segoe UI Semibold", 9))
    s.map("Treeview.Heading", background=[("active", C["line"])])
    s.configure("TNotebook", background=C["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
    s.configure("TNotebook.Tab", background=C["panel"], foreground=C["muted"], padding=(16, 8),
                borderwidth=0)
    s.map("TNotebook.Tab", background=[("selected", C["panel2"])], foreground=[("selected", C["fg"])])
    s.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["panel"],
                borderwidth=0, thickness=8)
    s.configure("TScrollbar", background=C["panel2"], troughcolor=C["panel"], arrowcolor=C["muted"],
                bordercolor=C["panel"])
    s.configure("TPanedwindow", background=C["bg"])
    s.configure("TSeparator", background=C["line"])


MODE_HELP = {
    "analyze": ("Analisar",
                "Só lê o disco e mostra a fragmentação, o mapa e a lista de arquivos fragmentados. "
                "Não altera nada: pode rodar sem risco, e é o melhor primeiro passo."),
    "defrag": ("Desfragmentar",
               "Desfragmentação completa. Cada arquivo fragmentado é regravado em um único trecho "
               "contínuo de espaço livre, dos menores para os maiores. Deixa os arquivos inteiros, "
               "mas não reorganiza o espaço livre. Demora mais em discos grandes e cheios."),
    "quick": ("Rápida",
              "Igual à completa, porém só mexe em arquivos de até 256 MB e não consolida o espaço "
              "livre. Termina bem mais depressa; ideal para a manutenção do dia a dia ou agendada."),
    "freespace": ("Espaço livre",
                  "Faz a desfragmentação completa e depois puxa arquivos do fim do disco para os "
                  "buracos do início (até 4 passadas), juntando o espaço livre em um grande bloco no "
                  "final. Assim os próximos arquivos já são gravados sem fragmentar. É o mais "
                  "demorado e move muitos arquivos."),
}


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "unfrag", "assets", name)
    return os.path.join(os.path.dirname(__file__), "assets", name)


def set_app_icon(root: tk.Tk) -> None:
    try:
        if IS_WINDOWS:
            root.iconbitmap(default=resource_path("icon.ico"))
        else:
            root._icon_img = tk.PhotoImage(file=resource_path("icon.png"))  # mantém referência
            root.iconphoto(True, root._icon_img)
    except Exception:
        pass


class ToolTip:
    """Dica escura que aparece ao passar o mouse sobre um widget."""

    def __init__(self, widget, title: str, text: str, delay: int = 350):
        self.widget, self.title, self.text, self.delay = widget, title, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None) -> None:
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self) -> None:
        if self.tip:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.configure(bg=C["line"])
        box = tk.Frame(tw, bg=C["panel2"], padx=12, pady=9)
        box.pack(padx=1, pady=1)
        tk.Label(box, text=self.title, bg=C["panel2"], fg=C["fg"], font=("Segoe UI Semibold", 10),
                 anchor="w").pack(fill="x")
        tk.Label(box, text=self.text, bg=C["panel2"], fg=C["muted"], justify="left", wraplength=340,
                 anchor="w", font=FONT).pack(fill="x", pady=(3, 0))
        tw.update_idletasks()
        sw = self.widget.winfo_screenwidth()
        x = max(4, min(x, sw - tw.winfo_reqwidth() - 8))
        tw.wm_geometry("+%d+%d" % (x, y))

    def _hide(self, _e=None) -> None:
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


class ScrollFrame(ttk.Frame):
    """Área rolável: use `.inner` como pai dos widgets."""

    def __init__(self, parent, bg=None):
        super().__init__(parent)
        bg = bg or C["bg"]
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas)
        for w in (self.canvas, self.inner):
            w.bind("<Enter>", self._bind_wheel)
            w.bind("<Leave>", self._unbind_wheel)
        self.wrap_widgets = []

    def _on_canvas(self, e) -> None:
        self.canvas.itemconfigure(self._win, width=e.width)
        for lbl in self.wrap_widgets:
            lbl.configure(wraplength=max(200, e.width - 40))

    def _bind_wheel(self, _e=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)
        self.canvas.bind_all("<Button-4>", self._wheel)
        self.canvas.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _wheel(self, e) -> None:
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        if getattr(e, "num", 0) == 4:
            step = -1
        elif getattr(e, "num", 0) == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(step * 2, "units")

    def note(self, parent, text: str, row: int, column: int = 0, columnspan: int = 4, pady=(0, 10)) -> None:
        """Texto explicativo (cinza, quebra de linha automática)."""
        lbl = ttk.Label(parent, text=text, style="Muted.TLabel", justify="left", wraplength=620)
        lbl.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=pady)
        self.wrap_widgets.append(lbl)


class App:
    def __init__(self, root: tk.Tk, demo: bool = False):
        self.root = root
        self.demo = demo or not IS_WINDOWS
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.cancel = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.engine: Optional[Engine] = None
        self.drives: Dict[str, DriveInfo] = {}
        self.cells: List[int] = []
        self.settings = load_settings()

        root.title("Unfrag Dark %s%s" % (__version__, "  [DEMO - discos simulados]" if self.demo else ""))
        root.geometry("1180x800")
        root.minsize(980, 640)
        apply_theme(root)
        set_app_icon(root)
        self._vars()
        self._build()
        self.refresh_drives()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self._poll)
        root.after(50, lambda: _dark_titlebar(root))

    # --- variáveis ---------------------------------------------------------
    def _vars(self) -> None:
        s = self.settings
        self.v_shutdown = tk.BooleanVar(value=False)
        self.v_force_ssd = tk.BooleanVar(value=s.get("force_ssd", False))
        self.v_battery = tk.BooleanVar(value=s.get("skip_battery", True))
        self.v_recurse = tk.BooleanVar(value=True)
        self.v_large = tk.IntVar(value=s.get("move_large_mb", 0))
        self.v_exts = tk.StringVar(value=s.get("move_exts", ""))
        self.v_minfrag = tk.DoubleVar(value=s.get("min_frag", 0.0))
        self.v_threads = tk.IntVar(value=s.get("threads", 0))
        self.v_fullscan = tk.BooleanVar(value=s.get("full_rescan", False))
        self.v_exclude = tk.StringVar(value=s.get("exclude", ""))
        self.v_path = tk.StringVar(value="")
        self.v_status = tk.StringVar(value="Pronto.")
        self.v_sched_freq = tk.StringVar(value="semanal")
        self.v_sched_time = tk.StringVar(value="03:00")
        self.v_sched_mode = tk.StringVar(value="quick")
        self.stats: Dict[str, tk.StringVar] = {k: tk.StringVar(value="—") for k in
                                               ("frag", "files", "fragfiles", "frags", "free", "largest", "moved")}

    # --- layout ------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Unfrag Dark", font=("Segoe UI Semibold", 15)).pack(side="left")
        ttk.Label(top, text="  desfragmentador NTFS", style="Muted.TLabel").pack(side="left", pady=(4, 0))
        self.btn_stop = ttk.Button(top, text="Parar", style="Danger.TButton", command=self.stop, state="disabled")
        self.btn_stop.pack(side="right")
        self.btns = []
        for text, mode, style in (("Espaço livre", "freespace", "TButton"), ("Rápida", "quick", "TButton"),
                                  ("Desfragmentar", "defrag", "Accent.TButton"), ("Analisar", "analyze", "TButton")):
            b = ttk.Button(top, text=text, style=style, command=lambda m=mode: self.start(m))
            b.pack(side="right", padx=(0, 8))
            ToolTip(b, *MODE_HELP[mode])
            self.btns.append(b)

        if IS_WINDOWS and not self.demo:
            from .backends.windows import is_admin
            if not is_admin():
                warn = tk.Label(self.root, bg="#3a2a10", fg=C["warn"], anchor="w", padx=14, pady=6,
                                text="Execute como Administrador para analisar e desfragmentar.")
                warn.pack(fill="x")

        paned = ttk.Panedwindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=14, pady=(0, 8))

        left = ttk.Frame(paned, style="Panel.TFrame")
        paned.add(left, weight=1)
        ttk.Label(left, text="UNIDADES", style="Panel.TLabel", foreground=C["muted"],
                  font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=12, pady=(10, 4))
        cols = ("drive", "label", "kind", "size", "free")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse", height=8)
        for c, t, w in (("drive", "Un.", 40), ("label", "Nome", 84), ("kind", "Tipo", 62),
                        ("size", "Tamanho", 86), ("free", "Livre", 86)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_drive_selected())
        ttk.Button(left, text="Atualizar lista", command=self.refresh_drives).pack(padx=8, pady=(0, 10), anchor="w")

        right = ttk.Frame(paned)
        paned.add(right, weight=4)

        card = ttk.Frame(right, style="Panel.TFrame", padding=12)
        card.pack(fill="x")
        for i, (key, lbl) in enumerate((("frag", "Fragmentação"), ("fragfiles", "Arquivos fragmentados"),
                                        ("frags", "Fragmentos"), ("free", "Espaço livre"),
                                        ("largest", "Maior trecho livre"), ("moved", "Última execução"))):
            f = ttk.Frame(card, style="Panel.TFrame")
            f.grid(row=0, column=i, padx=(0, 18), sticky="w")
            ttk.Label(f, text=lbl.upper(), style="Panel.TLabel", foreground=C["muted"],
                      font=("Segoe UI Semibold", 8)).pack(anchor="w")
            ttk.Label(f, textvariable=self.stats[key], style="Big.TLabel" if i == 0 else "Panel.TLabel",
                      font=("Segoe UI Semibold", 20) if i == 0 else ("Segoe UI Semibold", 12)).pack(anchor="w")

        mapbox = ttk.Frame(right, style="Panel.TFrame", padding=(12, 8))
        mapbox.pack(fill="both", expand=True, pady=(8, 0))
        self.canvas = tk.Canvas(mapbox, bg=C["panel"], highlightthickness=0, height=150)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_map())
        legend = ttk.Frame(mapbox, style="Panel.TFrame")
        legend.pack(fill="x", pady=(6, 0))
        for name, color in (("Livre", C["free"]), ("Em uso", C["used"]), ("Fragmentado", C["frag"]),
                            ("Não movível", C["unmov"])):
            sw = tk.Canvas(legend, width=12, height=12, bg=C["panel"], highlightthickness=0)
            sw.create_rectangle(0, 0, 12, 12, fill=color, outline="")
            sw.pack(side="left", padx=(0, 4))
            ttk.Label(legend, text=name, style="Panel.TLabel", foreground=C["muted"]).pack(side="left", padx=(0, 14))

        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, pady=(8, 0))
        self._tab_files()
        self._tab_options()
        self._tab_schedule()
        self._tab_log()

        bottom = ttk.Frame(self.root, padding=(14, 0, 14, 10))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(bottom, textvariable=self.v_status, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def _tab_files(self) -> None:
        f = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(f, text="Arquivos fragmentados")
        cols = ("frags", "size", "path")
        self.files_tree = ttk.Treeview(f, columns=cols, show="headings", height=6)
        for c, t, w, a in (("frags", "Fragmentos", 90, "e"), ("size", "Tamanho", 90, "e"), ("path", "Caminho", 600, "w")):
            self.files_tree.heading(c, text=t, command=lambda col=c: self._sort_files(col))
            self.files_tree.column(c, width=w, anchor=a)
        sb = ttk.Scrollbar(f, orient="vertical", command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=sb.set)
        self.files_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        menu = tk.Menu(self.root, tearoff=0, bg=C["panel2"], fg=C["fg"], activebackground=C["accent2"],
                       activeforeground="#fff", bd=0)
        menu.add_command(label="Desfragmentar este arquivo", command=self._defrag_selected_file)
        menu.add_command(label="Copiar caminho", command=self._copy_path)
        if IS_WINDOWS:
            menu.add_command(label="Abrir pasta", command=self._open_folder)
        self.file_menu = menu
        self.files_tree.bind("<Button-3>", self._popup)
        self._sort_state = ("frags", True)

    def _tab_options(self) -> None:
        sf = ScrollFrame(self.nb)
        self.nb.add(sf, text="Opções")
        f = ttk.Frame(sf.inner, padding=(14, 12, 14, 12))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        r = 0

        def title(text: str) -> None:
            nonlocal r
            ttk.Label(f, text=text, font=("Segoe UI Semibold", 10)).grid(row=r, column=0, columnspan=4, sticky="w")
            r += 1

        def note(text: str) -> None:
            nonlocal r
            sf.note(f, text, r)
            r += 1

        title("O que cada botão faz")
        for key in ("analyze", "defrag", "quick", "freespace"):
            name, text = MODE_HELP[key]
            sf.note(f, "%s — %s" % (name, text), r, pady=(2, 6))
            r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=4, sticky="we", pady=(4, 12))
        r += 1

        title("Alvo")
        row = ttk.Frame(f)
        row.grid(row=r, column=0, columnspan=4, sticky="we", pady=(2, 2))
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.v_path).grid(row=0, column=0, sticky="we")
        ttk.Button(row, text="Pasta…", command=self._pick_folder).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(row, text="Arquivo…", command=self._pick_file).grid(row=0, column=2, padx=(6, 0))
        r += 1
        note("Limita a análise/desfragmentação a uma pasta ou a um único arquivo. Deixe vazio para "
             "trabalhar na unidade inteira. Uma pasta de teste é a forma mais segura de começar.")

        ttk.Checkbutton(f, text="Incluir subpastas", variable=self.v_recurse).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        note("Quando o alvo é uma pasta, inclui também tudo o que está dentro das subpastas. "
             "Desmarcado, só os arquivos da própria pasta são processados.")

        title("Mover arquivos grandes para o fim do disco (MB)")
        ttk.Spinbox(f, from_=0, to=100000, textvariable=self.v_large, width=10).grid(row=r, column=0, sticky="w", pady=(2, 2))
        r += 1
        note("Arquivos com este tamanho ou maiores são empurrados para o final do disco, liberando o início "
             "(mais rápido em HDD) para arquivos do dia a dia. Use 0 para não mover nada.")

        title("Mover extensões para o fim (ex.: .iso,.vhdx)")
        ttk.Entry(f, textvariable=self.v_exts, width=34).grid(row=r, column=0, sticky="w", pady=(2, 2))
        r += 1
        note("Mesmo efeito da opção acima, mas por tipo de arquivo. Separe as extensões por vírgula. "
             "Bom para imagens ISO, discos virtuais, backups e vídeos que raramente são usados.")

        title("Só desfragmentar se a fragmentação for pelo menos (%)")
        ttk.Spinbox(f, from_=0, to=100, increment=1, textvariable=self.v_minfrag, width=10).grid(row=r, column=0, sticky="w", pady=(2, 2))
        r += 1
        note("Se o disco estiver menos fragmentado que este valor, nada é feito. Evita gastar tempo e "
             "desgaste do disco sem necessidade. 0 = sempre desfragmentar. (Não vale para o modo Espaço livre.)")

        title("Threads de leitura na análise (0 = automático)")
        ttk.Spinbox(f, from_=0, to=MAX_THREADS, increment=1, textvariable=self.v_threads, width=10).grid(row=r, column=0, sticky="w", pady=(2, 2))
        r += 1
        note("Quantos arquivos são lidos ao mesmo tempo durante a análise. Mais threads aceleram em SSD/NVMe; "
             "em HDD, valores altos podem deixar a leitura mais lenta por causa do movimento da cabeça. "
             "0 = automático (neste PC: %d). Máximo: %d." % (auto_threads(), MAX_THREADS))

        title("Não executar se estes programas estiverem abertos")
        ttk.Entry(f, textvariable=self.v_exclude, width=44).grid(row=r, column=0, sticky="w", pady=(2, 2))
        r += 1
        note("Lista separada por vírgula, por exemplo: game.exe, obs64.exe. Se algum deles estiver em "
             "execução, a operação não começa. Útil para não atrapalhar jogos, gravações ou renderizações.")

        ttk.Checkbutton(f, text="Não executar na bateria", variable=self.v_battery).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        note("Em notebooks, cancela o início quando o carregador não está conectado, "
             "porque desfragmentar consome bastante energia.")

        ttk.Checkbutton(f, text="Desfragmentar SSD mesmo assim (não recomendado)", variable=self.v_force_ssd).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        note("Por padrão, SSDs são ignorados: não têm cabeça mecânica, não ganham desempenho e a reescrita "
             "só gasta a vida útil. Marque apenas se souber o que está fazendo.")

        ttk.Checkbutton(f, text="Reanálise completa ao final (mais lenta)", variable=self.v_fullscan).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        note("Por padrão, ao terminar o programa relê só o mapa de espaço livre e os arquivos que moveu, "
             "o que poupa muito tempo em discos grandes. Marque para reanalisar o volume inteiro no final "
             "(útil se outros programas estiverem alterando arquivos ao mesmo tempo).")

        ttk.Checkbutton(f, text="Desligar o computador ao terminar", variable=self.v_shutdown).grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        note("Desliga o PC 60 segundos depois que a operação terminar com sucesso (não desliga se você "
             "clicou em Parar).")

    def _tab_schedule(self) -> None:
        sf = ScrollFrame(self.nb)
        self.nb.add(sf, text="Agendamento")
        f = ttk.Frame(sf.inner, padding=(14, 12, 14, 12))
        f.pack(fill="both", expand=True)
        f.columnconfigure(0, weight=1)
        sf.note(f, "Cria uma tarefa no Agendador de Tarefas do Windows para a unidade selecionada na lista. "
                   "Ela roda sozinha com privilégios de administrador e usa as opções da aba Opções "
                   "(tamanho mínimo para mover ao fim). A tarefa não executa na bateria.", 0)
        ttk.Label(f, text="Frequência", font=("Segoe UI Semibold", 10)).grid(row=1, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.v_sched_freq, values=list(scheduler.FREQUENCIES), width=12,
                     state="readonly").grid(row=2, column=0, sticky="w", pady=(2, 2))
        sf.note(f, "Com que frequência a tarefa roda: todo dia, toda semana ou todo mês.", 3)
        ttk.Label(f, text="Horário (HH:MM)", font=("Segoe UI Semibold", 10)).grid(row=4, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.v_sched_time, width=8).grid(row=5, column=0, sticky="w", pady=(2, 2))
        sf.note(f, "Hora do dia no formato 24 h, por exemplo 03:00. Escolha um horário em que o PC esteja ligado e ocioso.", 6)
        ttk.Label(f, text="Modo", font=("Segoe UI Semibold", 10)).grid(row=7, column=0, sticky="w")
        ttk.Combobox(f, textvariable=self.v_sched_mode, values=["quick", "defrag", "freespace"], width=12,
                     state="readonly").grid(row=8, column=0, sticky="w", pady=(2, 2))
        sf.note(f, "quick = rápida (só arquivos até 256 MB, ideal para o dia a dia); defrag = completa; "
                   "freespace = completa + consolida o espaço livre (mais demorada).", 9)
        btns = ttk.Frame(f)
        btns.grid(row=10, column=0, sticky="w")
        ttk.Button(btns, text="Criar tarefa", style="Accent.TButton", command=lambda: self._schedule("create")).pack(side="left")
        ttk.Button(btns, text="Remover tarefa", command=lambda: self._schedule("delete")).pack(side="left", padx=(8, 0))
        sf.note(f, "Remover apaga a tarefa com a frequência escolhida acima para a unidade selecionada.", 11, pady=(8, 0))

    def _tab_log(self) -> None:
        f = ttk.Frame(self.nb, style="Panel.TFrame")
        self.nb.add(f, text="Registro")
        self.log = tk.Text(f, bg=C["panel"], fg=C["fg"], insertbackground=C["fg"], relief="flat", height=6,
                           font=("Consolas", 9), state="disabled", wrap="word", padx=8, pady=6)
        self.log.pack(fill="both", expand=True)

    # --- helpers de UI -----------------------------------------------------
    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _selected_letter(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def refresh_drives(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.drives = {d.letter: d for d in list_drives(self.demo)}
        for d in self.drives.values():
            self.tree.insert("", "end", iid=d.letter, values=(d.letter + ":", d.label, d.kind,
                                                              fmt_bytes(d.total_bytes), fmt_bytes(d.free_bytes)))
        if self.drives and not self._selected_letter():
            first = next(iter(self.drives))
            self.tree.selection_set(first)

    def _on_drive_selected(self) -> None:
        letter = self._selected_letter()
        if letter and letter in self.drives and not self.drives[letter].supported:
            self.v_status.set("%s: %s" % (letter, self.drives[letter].note))

    def _set_busy(self, busy: bool) -> None:
        for b in self.btns:
            b.configure(state="disabled" if busy else "normal")
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.tree.configure(selectmode="none" if busy else "browse")

    # --- execução ----------------------------------------------------------
    def _threads(self) -> int:
        try:
            n = int(self.v_threads.get() or 0)
        except (tk.TclError, ValueError):
            n = 0
        return max(0, min(MAX_THREADS, n))

    def start(self, mode: str, root_path: Optional[str] = None) -> None:
        if self.worker and self.worker.is_alive():
            return
        letter = self._selected_letter()
        if not letter:
            messagebox.showinfo("Unfrag Dark", "Selecione uma unidade.")
            return
        d = self.drives[letter]
        if not d.supported:
            messagebox.showwarning("Unfrag Dark", "Unidade não suportada: %s" % d.note)
            return
        if mode != "analyze":
            reason = guard_reason(self.v_battery.get(), [p for p in self.v_exclude.get().split(",") if p.strip()])
            if reason:
                messagebox.showinfo("Unfrag Dark", "Não executado: %s." % reason)
                return
            if self.demo is False and not messagebox.askyesno(
                    "Confirmar", "Desfragmentar %s: no modo '%s'?\nFaça backup dos dados importantes antes." % (letter, mode)):
                return
        self._persist()
        threads = self._threads()
        path = root_path if root_path is not None else (self.v_path.get().strip() or None)
        opts = Options(mode="defrag" if mode == "analyze" else mode, root=path, recurse=self.v_recurse.get(),
                       move_large_mb=int(self.v_large.get() or 0),
                       move_exts=[e.strip() for e in self.v_exts.get().split(",") if e.strip()],
                       force_ssd=self.v_force_ssd.get(), min_fragmentation=float(self.v_minfrag.get() or 0),
                       full_rescan=self.v_fullscan.get())
        self.cancel.clear()
        self._set_busy(True)
        self.progress.configure(value=0, mode="indeterminate")
        self.progress.start(12)
        self._log("== %s %s: ==" % (mode, letter))
        self.worker = threading.Thread(target=self._work, args=(letter, mode, opts, threads), daemon=True)
        self.worker.start()

    def _work(self, letter: str, mode: str, opts: Options, threads: int = 0) -> None:
        def prog(phase, done, total, msg):
            self.q.put(("progress", phase, done, total, msg))
        try:
            vol = open_volume(letter, demo=self.demo, delay=0.004 if self.demo else 0.0)
        except Exception as exc:
            self.q.put(("error", "Não foi possível abrir %s: (%s)" % (letter, exc)))
            return
        vol.threads = threads
        try:
            eng = Engine(vol, progress=prog, cancel=self.cancel)
            self.engine = eng
            if mode == "analyze":
                res = eng.analyze(opts.root, opts.recurse)
                self.q.put(("analysis", res, None))
            else:
                rep = eng.defragment(opts)
                self.q.put(("analysis", rep.after, rep))
        except SsdSkipped as exc:
            self.q.put(("info", "%s." % exc))
        except Cancelled:
            self.q.put(("info", "Operação cancelada."))
        except Exception as exc:
            self.q.put(("error", "Erro: %s" % exc))
        finally:
            vol.close()
            self.q.put(("done",))

    def stop(self) -> None:
        self.cancel.set()
        self.v_status.set("Parando após a operação atual…")

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.q.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _handle(self, m: tuple) -> None:
        kind = m[0]
        if kind == "progress":
            _, phase, done, total, msg = m
            label = PHASES.get(phase, phase)
            if total:
                if str(self.progress.cget("mode")) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress.configure(value=100.0 * done / max(1, total), maximum=100)
                self.v_status.set("%s: %d/%d  %s" % (label, done, total, msg[-70:]))
            else:
                self.v_status.set("%s: %d arquivos…" % (label, done))
        elif kind == "analysis":
            _, res, rep = m
            self._show_result(res, rep)
        elif kind == "info":
            self.v_status.set(m[1])
            self._log(m[1])
        elif kind == "error":
            self.v_status.set(m[1])
            self._log(m[1])
            messagebox.showerror("Unfrag Dark", m[1])
        elif kind == "done":
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self._set_busy(False)
            self.refresh_drives_keep()
            if self.v_shutdown.get() and self.engine and not self.cancel.is_set():
                self._log("Desligando em 60 s…")
                shutdown_soon(60)

    def refresh_drives_keep(self) -> None:
        letter = self._selected_letter()
        self.refresh_drives()
        if letter and letter in self.drives:
            self.tree.selection_set(letter)

    # --- resultado ---------------------------------------------------------
    def _show_result(self, r: AnalysisResult, rep: Optional[Report]) -> None:
        cs = r.cluster_size
        self.stats["frag"].set("%.1f%%" % r.fragmentation_percent)
        self.stats["fragfiles"].set("%d de %d" % (r.fragmented_files, r.files))
        self.stats["frags"].set(str(r.fragments))
        self.stats["free"].set(fmt_bytes(r.free_clusters * cs))
        self.stats["largest"].set(fmt_bytes(r.largest_free_run * cs))
        if rep:
            self.stats["moved"].set("%d arq. · %s" % (rep.defragmented + rep.compacted + rep.moved_to_end,
                                                       fmt_bytes(rep.clusters_moved * cs)))
            self._log("Desfragmentados %d, compactados %d, movidos ao fim %d, falhas %d, ignorados %d (%.1fs)" % (
                rep.defragmented, rep.compacted, rep.moved_to_end, len(rep.failed), len(rep.skipped), rep.seconds))
            for p, why in rep.failed[:20]:
                self._log("  falha: %s — %s" % (p, why))
            self.v_status.set("Cancelado." if rep.cancelled else "Concluído em %.1f s." % rep.seconds)
        else:
            self.v_status.set("Análise concluída em %.1f s: %.1f%% fragmentado, %d arquivos." % (r.seconds, r.fragmentation_percent, r.files))
            self._log("Análise em %.1f s: %.1f%% fragmentado, %d de %d arquivos fragmentados" % (r.seconds, r.fragmentation_percent, r.fragmented_files, r.files))
        self._draw_map()
        self._fill_files()

    def _draw_map(self) -> None:
        c = self.canvas
        c.delete("all")
        if not self.engine:
            c.create_text(c.winfo_width() // 2, c.winfo_height() // 2, fill=C["muted"],
                          text="Selecione uma unidade e clique em Analisar")
            return
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        size, gap = 11, 2
        cols, rows = max(1, w // (size + gap)), max(1, h // (size + gap))
        eng = self.engine
        cells = diskmap.build_map(eng.backend.total_clusters, eng.free.runs(), eng.files.values(), cols * rows)
        self.cells = cells
        for i, st in enumerate(cells):
            x, y = (i % cols) * (size + gap), (i // cols) * (size + gap)
            c.create_rectangle(x, y, x + size, y + size, fill=CELL_COLORS[st], outline="")

    def _fill_files(self) -> None:
        self.files_tree.delete(*self.files_tree.get_children())
        if not self.engine:
            return
        rows = [(f.fragments, f.size, f.path) for f in self.engine.files.values() if f.fragments > 1]
        self._file_rows = rows
        self._render_files()

    def _render_files(self) -> None:
        col, desc = self._sort_state
        idx = {"frags": 0, "size": 1, "path": 2}[col]
        rows = sorted(getattr(self, "_file_rows", []), key=lambda r: r[idx], reverse=desc)[:5000]
        self.files_tree.delete(*self.files_tree.get_children())
        for fr, size, path in rows:
            self.files_tree.insert("", "end", values=(fr, fmt_bytes(size), path), tags=(path,))

    def _sort_files(self, col: str) -> None:
        prev, desc = self._sort_state
        self._sort_state = (col, (not desc) if prev == col else True)
        self._render_files()

    def _popup(self, ev) -> None:
        row = self.files_tree.identify_row(ev.y)
        if row:
            self.files_tree.selection_set(row)
            self.file_menu.tk_popup(ev.x_root, ev.y_root)

    def _selected_path(self) -> Optional[str]:
        sel = self.files_tree.selection()
        return self.files_tree.item(sel[0], "values")[2] if sel else None

    def _defrag_selected_file(self) -> None:
        p = self._selected_path()
        if p:
            self.start("defrag", root_path=p)

    def _copy_path(self) -> None:
        p = self._selected_path()
        if p:
            self.root.clipboard_clear()
            self.root.clipboard_append(p)

    def _open_folder(self) -> None:
        p = self._selected_path()
        if p and IS_WINDOWS:
            os.startfile(os.path.dirname(p))  # type: ignore[attr-defined]

    def _pick_folder(self) -> None:
        d = filedialog.askdirectory()
        if d:
            self.v_path.set(os.path.normpath(d))

    def _pick_file(self) -> None:
        f = filedialog.askopenfilename()
        if f:
            self.v_path.set(os.path.normpath(f))

    def _schedule(self, action: str) -> None:
        letter = self._selected_letter()
        if not letter:
            return
        if action == "create":
            extra = []
            if self.v_large.get():
                extra.append("--move-large %d" % self.v_large.get())
            if self._threads():
                extra.append("--threads %d" % self._threads())
            args = scheduler.build_create_args(letter, self.v_sched_mode.get(), self.v_sched_freq.get(),
                                               self.v_sched_time.get(), " ".join(extra))
        else:
            args = scheduler.build_delete_args(letter, self.v_sched_freq.get())
        if not IS_WINDOWS:
            self._log("(simulado) " + " ".join(args))
            messagebox.showinfo("Unfrag Dark", "Fora do Windows o agendamento é só simulado:\n\n" + " ".join(args))
            return
        try:
            out = scheduler.run(args)
            self._log(out)
            messagebox.showinfo("Unfrag Dark", "Tarefa %s." % ("criada" if action == "create" else "removida"))
        except Exception as exc:
            messagebox.showerror("Unfrag Dark", str(exc))

    def _persist(self) -> None:
        save_settings(dict(force_ssd=self.v_force_ssd.get(), skip_battery=self.v_battery.get(),
                           move_large_mb=int(self.v_large.get() or 0), move_exts=self.v_exts.get(),
                           min_frag=float(self.v_minfrag.get() or 0), exclude=self.v_exclude.get(),
                           threads=self._threads(), full_rescan=self.v_fullscan.get()))

    def on_close(self) -> None:
        self.cancel.set()
        self._persist()
        self.root.destroy()


def run_gui(demo: bool = False) -> int:
    root = tk.Tk()
    App(root, demo=demo)
    root.mainloop()
    return 0
