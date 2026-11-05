"""Draw a stick figure from a hand-written action list, as an LLM would send it."""

from pathlib import Path

from pdraw import Drawing, render

ACTIONS = [
    # torso: from the hip (canvas centre) upwards
    {"from": "center", "heading": "N", "type": "line", "length": "L", "label": "neck"},
    # head: full circle starting at the neck, heading east and turning left
    {"from": "neck", "heading": "E", "type": "curve", "turn": "left", "angle": 360, "length": "L"},
    # shoulders a bit below the neck
    {"from": "neck", "heading": "S", "type": "line", "length": "S", "label": "shoulder"},
    # left arm: upper arm, then bend at the elbow
    {"from": "shoulder", "heading": "WSW", "type": "line", "length": "M", "label": "elbow_l"},
    {"type": "curve", "turn": "right", "angle": 45, "length": "M", "label": "hand_l"},
    # right arm: waving
    {"from": "shoulder", "heading": "ESE", "type": "line", "length": "M", "label": "elbow_r"},
    {"type": "corner", "turn": "left", "angle": 112.5, "length": "M", "label": "hand_r"},
    # legs with feet
    {"from": "center", "heading": "SSW", "type": "line", "length": "L", "label": "ankle_l"},
    {"type": "corner", "turn": "right", "angle": 67.5, "length": "S"},
    {"from": "center", "heading": "SSE", "type": "line", "length": "L", "label": "ankle_r"},
    {"type": "corner", "turn": "left", "angle": 67.5, "length": "S"},
]


def main():
    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)

    drawing = Drawing()
    for line in drawing.apply_all(ACTIONS):
        print(line)
    print()
    print(drawing.describe())

    render(drawing).save(out / "stick_figure.png")
    render(drawing, annotated=True, scale=1.5).save(out / "stick_figure_annotated.png")
    print(f"\nwrote images to {out}")


if __name__ == "__main__":
    main()
