"""Widgets desenhados em Canvas para o visual "vidro azul": cartões arredondados com brilho,
botões em pílula, abas com ícones e ícones vetoriais pequenos (sem imagens externas)."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Dict, List, Optional


def rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    """Pontos para create_polygon(..., smooth=True) de um retângulo com cantos arredondados."""
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
            x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def mix(c1: str, c2: str, t: float) -> str:
    """Mistura duas cores #rrggbb (t=0 -> c1, t=1 -> c2)."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


# --- ícones --------------------------------------------------------------------------------

def draw_icon(c: tk.Canvas, kind: str, cx: float, cy: float, color: str, tag: str = "icon") -> None:
    """Ícones de ~16 px: list, gear, calendar, history, clock."""
    kw = dict(fill=color, tags=tag)
    if kind == "list":
        for dy in (-5, 0, 5):
            c.create_rectangle(cx - 8, cy + dy - 1.5, cx - 5, cy + dy + 1.5, outline="", **kw)
            c.create_line(cx - 2, cy + dy, cx + 8, cy + dy, width=2, **kw)
    elif kind == "gear":
        for i in range(8):
            a = math.pi * i / 4
            c.create_line(cx + 5.5 * math.cos(a), cy + 5.5 * math.sin(a),
                          cx + 8 * math.cos(a), cy + 8 * math.sin(a), width=3, **kw)
        c.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, outline=color, width=2, tags=tag)
        c.create_oval(cx - 2, cy - 2, cx + 2, cy + 2, outline=color, width=2, tags=tag)
    elif kind == "calendar":
        c.create_polygon(rounded_points(cx - 8, cy - 6, cx + 8, cy + 8, 3), smooth=True, outline=color,
                         fill="", width=2, tags=tag)
        c.create_line(cx - 8, cy - 1, cx + 8, cy - 1, width=2, **kw)
        c.create_line(cx - 4, cy - 8, cx - 4, cy - 4, width=2, **kw)
        c.create_line(cx + 4, cy - 8, cx + 4, cy - 4, width=2, **kw)
        c.create_rectangle(cx - 4, cy + 2, cx - 1, cy + 5, outline="", **kw)
        c.create_rectangle(cx + 1, cy + 2, cx + 4, cy + 5, outline="", **kw)
    elif kind == "history":
        c.create_arc(cx - 8, cy - 8, cx + 8, cy + 8, start=40, extent=300, style="arc", outline=color,
                     width=2, tags=tag)
        c.create_polygon(cx - 9, cy - 5, cx - 3, cy - 4, cx - 8, cy + 1, fill=color, outline="", tags=tag)
        c.create_line(cx, cy - 4, cx, cy, cx + 3, cy + 2, width=2, **kw)
    elif kind == "clock":
        c.create_oval(cx - 8, cy - 8, cx + 8, cy + 8, outline=color, width=2, tags=tag)
        c.create_line(cx, cy - 4, cx, cy, cx + 4, cy + 2, width=2, **kw)


# --- cartão ----------------------------------------------------------------------------------

class Card(tk.Frame):
    """Cartão com cantos arredondados e borda azul com brilho; o conteúdo vai em `.inner`.

    É um Frame comum (o tamanho vem do conteúdo, pelo próprio Tk) com um Canvas desenhado por
    trás. Assim o cartão sempre acompanha o conteúdo, mesmo quando um texto muda e cresce."""

    def __init__(self, parent, fill: str, glow: str, edge: str, bg: str, radius: int = 12,
                 pad=(12, 10)):
        super().__init__(parent, bg=bg, bd=0, highlightthickness=0)
        self.fill, self.glow, self.edge, self.radius = fill, glow, edge, radius
        self._back = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._back.place(x=0, y=0, relwidth=1, relheight=1)
        self.inner = tk.Frame(self, bg=fill)
        self.inner.pack(fill="both", expand=True, padx=pad[0], pady=pad[1])
        self._back.bind("<Configure>", self._on_size)

    def _on_size(self, e) -> None:
        c = self._back
        c.delete("all")
        r = self.radius
        c.create_polygon(rounded_points(1, 1, e.width - 2, e.height - 2, r), smooth=True, fill=self.fill,
                         outline=self.glow, width=3)
        c.create_polygon(rounded_points(2, 2, e.width - 3, e.height - 3, r), smooth=True, fill=self.fill,
                         outline=self.edge, width=1)


# --- botão em pílula ------------------------------------------------------------------------

THEME: Dict[str, str] = {}  # preenchido pelo gui com o dicionário de cores atual (atualizado no lugar)


class PillButton(tk.Canvas):
    """Botão arredondado. Aceita configure(state="disabled"/"normal"). As cores vêm do tema atual."""

    def _colors(self):
        t = THEME
        if not t:  # uso fora do app
            return {"normal": ("#0f1f46", "#18336f", "#22448f", "#e8efff"),
                    "soft": ("#16336e", "#1f4590", "#2d5bc0", "#e8efff"),
                    "danger": ("#e5372f", "#f2514a", "#ff7a72", "#ffffff")}[self._kind], \
                   ("#0a1330", "#17274f", "#5d6d93")
        if self._kind == "danger":
            on = ("#e5372f", "#f2514a", "#ff7a72", "#ffffff")
            off = (mix(t["panel"], "#e5372f", 0.18), mix(t["panel"], "#e5372f", 0.3), mix(t["fg"], t["panel"], 0.6))
        elif self._kind == "soft":
            on = (t["line"], mix(t["line"], t["accent"], 0.22), t["edge"], t["fg"])
            off = (t["panel"], t["line"], t["muted"])
        else:
            on = (t["panel2"], t["line"], t["edge"], t["fg"])
            off = (t["panel"], t["line"], t["muted"])
        return on, off

    def __init__(self, parent, text: str, command: Optional[Callable] = None, kind: str = "normal",
                 bg: str = "#070d20", font=("Segoe UI", 10), padx: int = 18, height: int = 34,
                 state: str = "normal"):
        self._font = tkfont.Font(font=font)
        self._text, self._cmd, self._kind, self._state = text, command, kind, state
        self._padx, self._h, self._hover = padx, height, False
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, height=height,
                         width=self._font.measure(text) + 2 * padx)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True), add="+")
        self.bind("<Leave>", lambda e: self._set_hover(False), add="+")
        self.bind("<ButtonRelease-1>", self._click)

    def _set_hover(self, v: bool) -> None:
        self._hover = v
        self._draw()

    def _click(self, e) -> None:
        if self._state == "normal" and self._cmd and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self._cmd()

    def configure(self, cnf=None, **kw):  # type: ignore[override]
        if cnf:
            kw.update(cnf)
        redraw = False
        if "state" in kw:
            self._state = kw.pop("state")
            self.configure_cursor()
            redraw = True
        if "text" in kw:
            self._text = kw.pop("text")
            super().configure(width=self._font.measure(self._text) + 2 * self._padx)
            redraw = True
        if "command" in kw:
            self._cmd = kw.pop("command")
        res = super().configure(**kw) if kw else None
        if redraw:
            self._draw()
        return res

    config = configure

    def configure_cursor(self) -> None:
        super().configure(cursor="hand2" if self._state == "normal" else "")

    def _draw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        (fill, hov, edge, fg), (ofill, oedge, ofg) = self._colors()
        if self._state != "normal":
            fill, edge, fg = ofill, oedge, ofg
        elif self._hover:
            fill = hov
        self.create_polygon(rounded_points(1, 1, w - 2, h - 2, 9), smooth=True, fill=fill, outline=edge, width=1)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


# --- abas com ícone --------------------------------------------------------------------------

class TabView(tk.Frame):
    """Barra de abas (ícone + texto) e um cartão que mostra a página selecionada."""

    FIXED_HEIGHT = 270

    def __init__(self, parent, colors: Dict[str, str]):
        super().__init__(parent, bg=colors["bg"])
        self.c = colors
        self.bar = tk.Frame(self, bg=colors["bg"])
        self.bar.pack(fill="x")
        self.card = Card(self, colors["panel"], colors["glow"], colors["edge"], colors["bg"], pad=(6, 6))
        self.card.pack(fill="both", expand=True, pady=(0, 0))
        # tamanho fixo: a altura pedida não depende da página aberta, então trocar de aba
        # não muda a escala do resto da janela (a página rola ou se ajusta dentro do cartão)
        self.card.configure(height=self.FIXED_HEIGHT, width=10)
        self.card.pack_propagate(False)
        self.body = self.card.inner
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self._tabs: List[tuple] = []
        self.current: Optional[int] = None

    def add(self, page: tk.Widget, text: str, icon: str) -> None:
        idx = len(self._tabs)
        font = tkfont.Font(font=("Segoe UI Semibold", 10))
        width = font.measure(text) + 62
        cv = tk.Canvas(self.bar, width=width, height=40, bg=self.c["bg"], highlightthickness=0, bd=0, cursor="hand2")
        cv.pack(side="left", padx=(0, 6))
        cv.bind("<Button-1>", lambda e, i=idx: self.select(i))
        cv.bind("<Enter>", lambda e, i=idx: self._paint(i, hover=True))
        cv.bind("<Leave>", lambda e, i=idx: self._paint(i))
        self._tabs.append((cv, page, text, icon, font))
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_remove()
        self._paint(idx)
        if self.current is None:
            self.select(0)

    def _paint(self, i: int, hover: bool = False) -> None:
        cv, _page, text, icon, font = self._tabs[i]
        sel = i == self.current
        cv.delete("all")
        w, h = int(cv["width"]), 40
        if sel or hover:
            fill = self.c["tab_sel"] if sel else self.c["tab_hover"]
            cv.create_polygon(rounded_points(1, 4, w - 2, h + 12, 10), smooth=True, fill=fill,
                              outline=self.c["edge"] if sel else "", width=1)
        col = self.c["accent"] if sel else self.c["muted"]
        draw_icon(cv, icon, 22, 22, col)
        cv.create_text(40, 22, text=text, anchor="w", fill=self.c["fg"] if sel else self.c["muted"], font=font)
        if sel:
            cv.create_line(8, h - 1, w - 8, h - 1, fill=self.c["accent"], width=2)

    def select(self, i: int) -> None:
        prev = self.current
        self.current = i
        for j, (_cv, page, *_r) in enumerate(self._tabs):
            if j == i:
                page.grid()
                page.tkraise()
            else:
                page.grid_remove()
        for j in {i, prev} - {None}:
            self._paint(j)  # type: ignore[arg-type]
