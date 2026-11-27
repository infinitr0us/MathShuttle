"""Generate assets/icon.ico (Windows) and assets/icon.icns (macOS): a sigma glyph on a rounded tile.
Needs Pillow; run it on Windows, where the Cambria font gives the glyph (both files are in the repo,
so the Mac build does not need to draw them)."""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "icon.ico")
OUT_ICNS = os.path.join(HERE, "..", "assets", "icon.icns")


def render(size: int, margin: float = 0.0) -> Image.Image:
    """The icon at size x size; `margin` (a fraction of the size) around the tile: macOS draws app
    icons on a 1024 canvas with the tile 824 wide, Windows icons fill theirs."""
    s = size * 4  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = round(s * margin)
    d.rounded_rectangle((m, m, s - 1 - m, s - 1 - m), radius=(s - 2 * m) // 5, fill=(31, 78, 121, 255))
    s_tile = s - 2 * m
    font = None
    for name in ("cambria.ttc", "cambriab.ttf", "times.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, int(s_tile * 0.72))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    text = "Σ"
    box = d.textbbox((0, 0), text, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((s - w) / 2 - box[0], (s - h) / 2 - box[1]), text, font=font, fill="white")
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [render(n) for n in sizes]
    frames[-1].save(OUT, format="ICO", sizes=[(n, n) for n in sizes], append_images=frames[:-1])
    print("wrote", os.path.abspath(OUT))
    margin = 100 / 1024  # Apple's app icon grid
    big = [render(n, margin) for n in (16, 32, 64, 128, 256, 512)]  # each size drawn, not scaled from 1024
    render(1024, margin).save(OUT_ICNS, format="ICNS", append_images=big)
    print("wrote", os.path.abspath(OUT_ICNS))


if __name__ == "__main__":
    main()
