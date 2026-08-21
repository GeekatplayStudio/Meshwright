"""
Generates the Meshwright app icon (ui/assets/icon.png + icon.ico).
Geekatplay Studio — Vladimir Chopine
"""
import os
import math
from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "assets")
S = 1024  # working size (downsampled for anti-aliasing)

BG_TOP = (30, 33, 38)
BG_BOT = (17, 19, 21)
AMBER = (217, 164, 65)
AMBER_LIGHT = (240, 195, 100)
FACE_L = (58, 63, 71)
FACE_R = (38, 42, 48)
EDGE = (230, 232, 235)
GOOD = (76, 195, 138)


def rounded_bg():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        c = tuple(int(BG_TOP[i] * (1 - t) + BG_BOT[i] * t) for i in range(3)) + (255,)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=255)
    img.paste(grad, (0, 0), mask)
    # subtle amber rim
    rim = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(rim).rounded_rectangle([6, 6, S - 7, S - 7], radius=S * 0.21, outline=AMBER + (70,), width=8)
    img.alpha_composite(rim)
    return img


def iso_cube(img):
    d = ImageDraw.Draw(img)
    cx, cy = S / 2, S / 2 + S * 0.02
    r = S * 0.30                     # cube "radius"
    h = r * math.sin(math.radians(30))
    w = r * math.cos(math.radians(30))
    top = (cx, cy - r)
    left = (cx - w, cy - h)
    right = (cx + w, cy - h)
    center = (cx, cy)
    bl = (cx - w, cy + r - h)
    br = (cx + w, cy + r - h)
    bottom = (cx, cy + r)

    # soft shadow
    sh = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([left, top, right, br, bottom, bl], fill=(0, 0, 0, 120))
    sh = sh.filter(ImageFilter.GaussianBlur(S * 0.03))
    img.alpha_composite(sh, (0, int(S * 0.02)))

    d.polygon([top, right, center, left], fill=AMBER)          # top
    d.polygon([left, center, bottom, bl], fill=FACE_L)         # left
    d.polygon([center, right, br, bottom], fill=FACE_R)        # right
    # highlight on top face
    hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(hl).polygon([top, right, center, left], fill=AMBER_LIGHT + (90,))
    img.alpha_composite(hl)

    lw = int(S * 0.022)
    for a, b in [(top, left), (top, right), (left, center), (right, center), (center, bottom), (left, bl), (right, br), (bl, bottom), (br, bottom)]:
        d.line([a, b], fill=EDGE, width=lw, joint="curve")
    for p in [top, left, right, center, bl, br, bottom]:
        d.ellipse([p[0] - lw * 0.55, p[1] - lw * 0.55, p[0] + lw * 0.55, p[1] + lw * 0.55], fill=EDGE)

    # "repaired" badge
    br_c = (S * 0.78, S * 0.78)
    rad = S * 0.11
    d.ellipse([br_c[0] - rad - lw, br_c[1] - rad - lw, br_c[0] + rad + lw, br_c[1] + rad + lw], fill=BG_BOT)
    d.ellipse([br_c[0] - rad, br_c[1] - rad, br_c[0] + rad, br_c[1] + rad], fill=GOOD)
    cw = int(S * 0.028)
    d.line([(br_c[0] - rad * 0.5, br_c[1]), (br_c[0] - rad * 0.1, br_c[1] + rad * 0.4), (br_c[0] + rad * 0.55, br_c[1] - rad * 0.4)],
           fill=BG_BOT, width=cw, joint="curve")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    img = rounded_bg()
    iso_cube(img)
    png = img.resize((512, 512), Image.LANCZOS)
    png.save(os.path.join(OUT_DIR, "icon.png"))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT_DIR, "icon.ico"),
        sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print("written", OUT_DIR)


if __name__ == "__main__":
    main()
