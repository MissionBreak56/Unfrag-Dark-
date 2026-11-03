"""Gera o ícone do app (desenho original): disco com trilhas, uma delas fragmentada."""
import math
import os
from PIL import Image, ImageDraw

S = 1024
OUT = os.path.join(os.path.dirname(__file__), "..", "src", "unfrag", "assets")
BG, EDGE, DISK = (23, 26, 32), (42, 47, 57), (31, 35, 43)
BLUE, RED, HUB = (76, 141, 255), (239, 83, 80), (231, 233, 238)
C = S // 2


def arc(d, r, a0, a1, color, w):
    R = r + w / 2  # PIL desenha a espessura para dentro; assim r é a linha central
    box = [C - R, C - R, C + R, C + R]
    d.arc(box, a0, a1, fill=color, width=w)
    for a in (a0, a1):  # pontas arredondadas
        x, y = C + r * math.cos(math.radians(a)), C + r * math.sin(math.radians(a))
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=color)


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=BG, outline=EDGE, width=10)
    d.ellipse([C - 400, C - 400, C + 400, C + 400], fill=DISK, outline=EDGE, width=8)
    w = 56
    # trilha externa: fragmentada (pedaços azuis e vermelhos espalhados)
    for a0, a1, col in ((-80, -20, BLUE), (0, 14, RED), (32, 100, BLUE), (122, 136, RED),
                        (156, 232, BLUE), (256, 270, RED), (288, 300, BLUE)):
        arc(d, 330, a0, a1, col, w)
    # trilha do meio: quase contínua
    arc(d, 235, -70, 250, BLUE, w)
    arc(d, 235, 272, 286, RED, w)
    # trilha interna: contínua (já desfragmentada)
    arc(d, 140, 0, 359.9, BLUE, w)
    # eixo
    d.ellipse([C - 62, C - 62, C + 62, C + 62], fill=HUB)
    d.ellipse([C - 22, C - 22, C + 22, C + 22], fill=DISK)
    return img


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    big.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "..", "..", "..", "docs", "icon.png"))
    print("ok")
