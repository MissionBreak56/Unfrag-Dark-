"""Verificação de versão nova no GitHub, sem API e sem token.

`/releases/latest` responde com um redirecionamento para `/releases/tag/<tag>`; lemos só o cabeçalho
Location. Isso não gasta o limite da API e funciona para qualquer usuário."""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Optional, Tuple

REPO = "MissionBreak56/Unfrag-Dark-"
LATEST_URL = "https://github.com/%s/releases/latest" % REPO
_TAG = re.compile(r"/releases/tag/(v?[0-9][0-9A-Za-z.\-+]*)$")


def parse_version(text: str) -> Tuple[int, ...]:
    """'v0.1.10' -> (0, 1, 10). Sufixos não numéricos (-beta) são ignorados."""
    nums = re.findall(r"\d+", text.split("-", 1)[0].split("+", 1)[0])
    return tuple(int(n) for n in nums) or (0,)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def latest_tag(timeout: float = 6.0) -> Optional[str]:
    """Tag da última versão publicada (ex.: 'v0.1.8') ou None se não deu para saber."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(LATEST_URL, headers={"User-Agent": "UnfragDark-update-check"})
    try:
        opener.open(req, timeout=timeout).close()
        return None  # sem redirecionamento: não há release ou a página mudou
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location", "") if exc.code in (301, 302, 303, 307, 308) else ""
        m = _TAG.search(loc.split("?", 1)[0])
        return m.group(1) if m else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def check(current: str, timeout: float = 6.0) -> Optional[Tuple[str, str]]:
    """(tag, url_da_página) se existir versão mais nova que `current`; senão None."""
    tag = latest_tag(timeout)
    if tag and is_newer(tag, current):
        return tag, "https://github.com/%s/releases/tag/%s" % (REPO, tag)
    return None


def status(current: str, timeout: float = 6.0) -> Tuple[str, Optional[str], Optional[str]]:
    """('newer', tag, url) | ('current', tag, None) | ('error', None, None)."""
    tag = latest_tag(timeout)
    if not tag:
        return "error", None, None
    if is_newer(tag, current):
        return "newer", tag, "https://github.com/%s/releases/tag/%s" % (REPO, tag)
    return "current", tag, None
