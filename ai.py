"""Image analysis: dominant-colour extraction for "Extract palette".

Uses colorthief's MMCQ (modified median cut) quantiser, ranks the resulting
colour boxes by how many pixels they actually cover, and drops near-duplicates.
If that yields fewer colours than requested (flat graphics, mostly-white
images: colorthief ignores near-white pixels) the gap is filled from a Pillow
median-cut quantisation.
"""
from __future__ import annotations

import io

from colorthief import MMCQ
from PIL import Image

# Analysis runs on a thumbnail: plenty for colour statistics and keeps the
# pure-Python quantiser fast even for very large photos.
ANALYSIS_SIDE = 200
# Colours closer than this (Euclidean RGB distance) count as duplicates. If an
# image has too little variety, the threshold is relaxed step by step.
DISTANCE_STEPS = (24.0, 12.0, 6.0, 1.0)
# Boxes covering less than this share of pixels are noise, not "dominant".
MIN_SHARE = 0.005

RGB = tuple[int, int, int]


def extract_palette(image_bytes: bytes, count: int = 5) -> list[str]:
    """Return up to `count` dominant colours as '#RRGGBB', most dominant first."""
    image = _thumbnail(image_bytes)
    candidates = _mmcq_palette(image, count)
    colors = _distinct(candidates, count, DISTANCE_STEPS[0])
    if len(colors) < count:
        candidates += _pillow_palette(image, count * 2)
        for min_distance in DISTANCE_STEPS:
            colors = _distinct(candidates, count, min_distance)
            if len(colors) == count:
                break
    return [f"#{r:02X}{g:02X}{b:02X}" for r, g, b in colors]


def _thumbnail(image_bytes: bytes) -> Image.Image:
    with Image.open(io.BytesIO(image_bytes)) as img:
        img.seek(0)  # first frame of animated GIF/WebP
        rgba = img.convert("RGBA")
    rgba.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE))
    return rgba


def _pixels(image: Image.Image) -> list[tuple[int, int, int, int]]:
    getter = getattr(image, "get_flattened_data", None)  # Pillow >= 12.1
    return list(getter() if getter else image.getdata())


def _mmcq_palette(image: Image.Image, count: int) -> list[RGB]:
    # Same pixel filter colorthief applies: skip transparent and near-white.
    pixels = [
        (r, g, b)
        for r, g, b, a in _pixels(image)
        if a >= 125 and not (r > 250 and g > 250 and b > 250)
    ]
    if not pixels:
        return []
    try:
        # Ask for a few extra boxes, then keep the most populated ones.
        cmap = MMCQ.quantize(pixels, count + 3)
    except Exception:
        return []
    total = len(pixels)
    boxes = [(entry["vbox"].count, entry["color"]) for entry in cmap.vboxes.contents]
    boxes = [b for b in boxes if b[0] / total >= MIN_SHARE]
    boxes.sort(key=lambda b: b[0], reverse=True)
    return [tuple(int(c) for c in color) for _, color in boxes]


def _pillow_palette(image: Image.Image, count: int) -> list[RGB]:
    # Composite onto white so transparent areas don't turn black.
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    rgb = Image.alpha_composite(background, image).convert("RGB")
    quantized = rgb.quantize(colors=count, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    ranked = sorted(quantized.getcolors() or [], reverse=True)
    return [tuple(palette[i * 3 : i * 3 + 3]) for _, i in ranked]


def _distinct(colors: list[RGB], count: int, min_distance: float) -> list[RGB]:
    result: list[RGB] = []
    for color in colors:
        if all(_distance(color, kept) >= min_distance for kept in result):
            result.append(color)
        if len(result) == count:
            break
    return result


def _distance(a: RGB, b: RGB) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
