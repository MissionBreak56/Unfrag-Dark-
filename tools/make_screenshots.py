"""Gera as capturas de tela de docs/ com a interface real em modo demo (volumes simulados).

Linux/macOS com X11 e ImageMagick (comando `import`):

    xvfb-run -a -s "-screen 0 1280x860x24" python tools/make_screenshots.py

Usa configurações temporárias (não mexe nas suas), desliga a bandeja e a verificação de atualizações.
"""
import json, os, subprocess, sys, time, tempfile
tmp = tempfile.mkdtemp(prefix="unfrag-shots-")
os.environ["APPDATA"] = tmp
os.environ["UNFRAG_TRAY"] = "0"
os.makedirs(os.path.join(tmp, "unfrag-dark"), exist_ok=True)
json.dump({"check_updates": False}, open(os.path.join(tmp, "unfrag-dark", "settings.json"), "w"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import tkinter as tk
from unfrag import gui, diskmap

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs") + os.sep
root = tk.Tk()
app = gui.App(root, demo=True)
root.geometry("1280x820+0+0")

def pump(cond=lambda: True, timeout=40, extra=0.4):
    end = time.time() + timeout
    while time.time() < end and not cond():
        root.update(); time.sleep(0.02)
    t = time.time() + extra
    while time.time() < t:
        root.update(); time.sleep(0.02)

def done():
    return not (app.worker and app.worker.is_alive()) and app.q.empty()

def shot(name):
    pump(extra=0.5)
    subprocess.run(["import", "-window", "root", "-crop", "1280x820+0+0", "+repage", OUT + name], check=True)
    print("ok", name)

class Ev:  # evento falso de clique
    def __init__(self, x, y): self.x, self.y = x, y

pump(extra=1.0)
app.nb.select(0)
app.start("analyze"); pump(lambda: app.worker is not None and done(), extra=0.8)
shot("screenshot-analyze.png")

# detalhe de um bloco fragmentado
cols, step, size = app._map_geom
idx = next(i for i, s in enumerate(app.cells) if s == diskmap.FRAGMENTED)
app._on_map_click(Ev((idx % cols) * step + 4, (idx // cols) * step + 4)); pump(extra=0.6)
shot("screenshot-block.png")
app._clear_selection(); pump()

app.start("freespace"); pump(lambda: done(), timeout=120, extra=0.8)
app.nb.select(0)
shot("screenshot-defrag.png")

sfs = [w for w in app.nb.body.winfo_children() if isinstance(w, gui.ScrollFrame)]
def find_label(w, text):
    try:
        if str(w.cget("text")).startswith(text):
            return w
    except tk.TclError:
        pass
    for c in w.winfo_children():
        r = find_label(c, text)
        if r: return r
app.nb.select(1); pump()
sfs[0].canvas.yview_moveto(0); pump()
shot("screenshot-options.png")
lbl = find_label(sfs[0].inner, "Cores do app")
sfs[0].inner.update_idletasks()
frac = max(0.0, (lbl.winfo_y() - 12) / max(1, sfs[0].inner.winfo_height())) if lbl else 0.5
print("cores em", frac)
sfs[0].canvas.yview_moveto(frac); pump()
shot("screenshot-colors.png")
sfs[0].canvas.yview_moveto(0)
app.nb.select(2); pump(); shot("screenshot-schedule.png")
app.nb.select(0); pump()
b = app.btns[3]
b.event_generate("<Enter>"); pump(extra=1.2)
shot("screenshot-tooltip.png")
root.destroy()
