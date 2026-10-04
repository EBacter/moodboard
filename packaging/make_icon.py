"""Generate the app icon (assets/moodboard.ico + .png): a paint fan deck.

Run from the project root:  python packaging/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024  # drawn large, then downsampled for each icon size
CARDS = ["#6D597A", "#E76F51", "#F4A261", "#E9C46A", "#2A9D8F"]  # back to front
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def draw(size: int = SIZE) -> Image.Image:
    s = size / 1024
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # Rounded tile in the canvas grey, like the app's board
    tile = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle(
        [40 * s, 40 * s, 984 * s, 984 * s], radius=210 * s, fill="#F3F3F6", outline="#D6D6DD", width=round(14 * s)
    )
    icon.alpha_composite(tile)

    # Fan of paint-chip cards pivoting around the bottom-left rivet
    pivot = (262 * s, 742 * s)  # chosen so the whole fan sits centred on the tile
    card_w, card_h = 182 * s, 610 * s
    angles = [-72, -54, -36, -18, 0]  # degrees clockwise from upright
    for color, angle in zip(CARDS, angles):
        layer = Image.new("RGBA", icon.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        x0, y1 = pivot[0] - card_w / 2, pivot[1] + card_w / 2
        box = [x0, y1 - card_h, x0 + card_w, y1]
        d.rounded_rectangle(box, radius=48 * s, fill=color, outline="#FFFFFF", width=round(12 * s))
        layer = layer.rotate(angle, center=pivot, resample=Image.Resampling.BICUBIC)
        icon.alpha_composite(layer)

    r = 46 * s  # rivet
    ImageDraw.Draw(icon).ellipse(
        [pivot[0] - r, pivot[1] - r, pivot[0] + r, pivot[1] + r], fill="#FFFFFF", outline="#C9C9D1", width=round(8 * s)
    )
    return icon


def main() -> None:
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.Resampling.LANCZOS).save(assets / "moodboard.png")
    frames = [big.resize((n, n), Image.Resampling.LANCZOS) for n in ICO_SIZES]
    frames[-1].save(assets / "moodboard.ico", sizes=[(n, n) for n in ICO_SIZES], append_images=frames[:-1])
    print("wrote", assets / "moodboard.ico", "and", assets / "moodboard.png")


if __name__ == "__main__":
    main()
