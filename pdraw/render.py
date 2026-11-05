"""Render a Drawing as a clean image or as an annotated working view for the LLM."""

from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFont

from .core import Drawing, Pen

STROKE = (20, 20, 20)
LABEL = (0, 110, 220)
START = (150, 150, 150)
PEN = (220, 40, 40)


def render(drawing: Drawing, annotated: bool = False, scale: float = 1.0, stroke_width: int = 3) -> Image.Image:
    w, h = round(drawing.width * scale), round(drawing.height * scale)
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)

    def to_px(x, y):
        return x * scale, h - y * scale  # flip y: model is y-up, image is y-down

    for step in drawing.steps:
        if not step.drawn:
            continue
        draw.line([to_px(*p) for p in step.points], fill=STROKE,
                  width=max(1, round(stroke_width * scale)), joint="curve")

    if annotated:
        font = ImageFont.load_default(size=max(10, round(13 * scale)))
        for name, pen in drawing.labels.items():
            color = START if name in drawing.start_points else LABEL
            _marker(draw, to_px(pen.x, pen.y), pen, color, name, font, scale)
        _marker(draw, to_px(drawing.pen.x, drawing.pen.y), drawing.pen, PEN, None, font, scale, arrow=22)

    return img


def _marker(draw, xy, pen: Pen, color, text, font, scale, arrow: float = 12):
    x, y = xy
    r = 4 * scale
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    # heading arrow (image y is flipped)
    ax, ay = math.cos(pen.angle), -math.sin(pen.angle)
    tip = (x + ax * arrow * scale, y + ay * arrow * scale)
    draw.line([(x, y), tip], fill=color, width=max(1, round(2 * scale)))
    head = 5 * scale
    for side in (+1, -1):
        a = math.atan2(ay, ax) + math.pi + side * 0.5
        draw.line([tip, (tip[0] + head * math.cos(a), tip[1] + head * math.sin(a))],
                  fill=color, width=max(1, round(2 * scale)))
    if text:
        # put the text left of the marker when it would run off the right edge
        right = x + 7 * scale + draw.textlength(text, font=font) <= draw.im.size[0]
        tx, anchor = (x + 7 * scale, "ls") if right else (x - 7 * scale, "rs")
        draw.text((tx, y - 6 * scale), text, fill=color, font=font, anchor=anchor,
                  stroke_width=2, stroke_fill="white")
