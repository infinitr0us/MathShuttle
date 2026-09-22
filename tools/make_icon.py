"""Generate assets/icon.ico (a sigma glyph on a rounded tile).  Needs Pillow."""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "assets", "icon.ico")


def render(size: int) -> Image.Image:
    s = size * 4  # supersample
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=(31, 78, 121, 255))
    font = None
    for name in ("cambria.ttc", "cambriab.ttf", "times.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(name, int(s * 0.72))
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


if __name__ == "__main__":
    main()
