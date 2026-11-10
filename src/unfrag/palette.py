"""Paleta de cores do app: 8 cores base editáveis; as demais (bordas, brilho, abas...) são derivadas.

Com as cores base padrão, as derivadas são exatamente as do visual original; ao mudar uma cor,
só as derivadas que dependem dela são recalculadas (misturando cores), então trocar, por exemplo,
a cor dos blocos livres não altera o resto do tema."""
from __future__ import annotations

import re
from typing import Dict

BASE_LABELS = {  # chave -> rótulo exibido nas Opções (na ordem de exibição)
    "free": "Blocos livres",
    "used": "Blocos em uso",
    "frag": "Blocos fragmentados",
    "unmov": "Blocos não movíveis",
    "bg": "Fundo da janela",
    "panel": "Cartões e painéis",
    "accent": "Cor de destaque",
    "fg": "Texto",
}

DEFAULT_BASE: Dict[str, str] = {
    "free": "#6c7589", "used": "#2f7be8", "frag": "#ef4444", "unmov": "#fbd22b",
    "bg": "#060b1d", "panel": "#0b1633", "accent": "#4cc3ff", "fg": "#e8efff",
}

PRESETS: Dict[str, Dict[str, str]] = {
    "Azul (padrão)": dict(DEFAULT_BASE),
    "Grafite": {"free": "#5a6272", "used": "#4c7bd6", "frag": "#ef4444", "unmov": "#f0b429",
                "bg": "#0e1014", "panel": "#171a21", "accent": "#8ab4ff", "fg": "#e6e8ee"},
    "Esmeralda": {"free": "#5f7a70", "used": "#1f9d6b", "frag": "#ef5350", "unmov": "#f4c430",
                  "bg": "#05140f", "panel": "#0a2119", "accent": "#3ee0a0", "fg": "#e6fff5"},
    "Violeta": {"free": "#6d6788", "used": "#7c4dff", "frag": "#ff5c7a", "unmov": "#ffd24a",
                "bg": "#0d0a1f", "panel": "#171233", "accent": "#b48cff", "fg": "#f0eaff"},
    # preto puro (#000000) no fundo: em telas OLED/AMOLED os pixels ficam apagados e economizam bateria
    "AMOLED": {"free": "#4a505c", "used": "#2f7be8", "frag": "#ff3b3b", "unmov": "#ffd21f",
               "bg": "#000000", "panel": "#06070a", "accent": "#4cc3ff", "fg": "#f2f5ff"},
}

# valores derivados do visual padrão (mantidos fixos enquanto as cores de que dependem não mudam)
_DEFAULT_DERIVED = {
    "panel2": "#10204a", "line": "#1c3470", "glow": "#12296a", "edge": "#2457c9", "accent2": "#2c5fe0",
    "tab_sel": "#0f2556", "tab_hover": "#0c1c44", "muted": "#8da2cf", "used_full": "#1f4fc4",
}
FIXED = {"ok": "#3ecf8e", "warn": "#f5a524", "bad": "#ef4444"}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


# chave derivada -> (cores base de que depende, fórmula)
_RULES = {
    "panel2": (("panel",), lambda b: mix(b["panel"], "#ffffff", 0.07)),
    "line": (("panel", "accent"), lambda b: mix(b["panel"], b["accent"], 0.25)),
    "glow": (("bg", "panel", "accent"), lambda b: mix(b["bg"], b["accent"], 0.22)),
    "edge": (("accent", "panel"), lambda b: mix(b["panel"], b["accent"], 0.55)),
    "accent2": (("accent", "bg"), lambda b: mix(b["accent"], b["bg"], 0.4)),
    "tab_sel": (("panel", "accent"), lambda b: mix(b["panel"], b["accent"], 0.14)),
    "tab_hover": (("bg", "panel"), lambda b: mix(b["bg"], b["panel"], 0.6)),
    "muted": (("fg", "panel"), lambda b: mix(b["fg"], b["panel"], 0.45)),
    "used_full": (("used",), lambda b: mix(b["used"], "#000000", 0.28)),
}


def sanitize(base: object) -> Dict[str, str]:
    """Aceita só cores #rrggbb válidas; o que faltar ou estiver inválido volta ao padrão."""
    out = dict(DEFAULT_BASE)
    if isinstance(base, dict):
        for k in DEFAULT_BASE:
            v = base.get(k)
            if isinstance(v, str) and _HEX.match(v):
                out[k] = v.lower()
    return out


def full(base: Dict[str, str]) -> Dict[str, str]:
    base = sanitize(base)
    out = dict(base)
    for key, (deps, fn) in _RULES.items():
        if all(base[d] == DEFAULT_BASE[d] for d in deps):
            out[key] = _DEFAULT_DERIVED[key]
        else:
            out[key] = fn(base)
    out.update(FIXED)
    return out


def apply_palette(target: Dict[str, str], base: Dict[str, str]) -> None:
    """Atualiza `target` (o dict de cores usado pela interface) no lugar."""
    target.update(full(base))


def matching_preset(base: Dict[str, str]) -> str:
    base = sanitize(base)
    for name, p in PRESETS.items():
        if sanitize(p) == base:
            return name
    return "Personalizado"
