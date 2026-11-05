"""Entry point: a short word in, a drawing by the robot out.

    python3 draw.py                 # asks what to draw
    python3 draw.py "ein Haus"      # same, without asking
    python3 draw.py "ein Haus" --sim --size 0.12   # extra options go to robot_draw.py

1. Gemini (via the Antigravity CLI `agy`) generates a line-art image  → samples/<name>.png
2. it is traced into ordered lines and arcs                            → out/<name>.json, out/<name>.png
3. robot_draw.py draws it on the plane from paper.json and asks for 'yes' before the real arm moves.
After step 2 you can look at out/<name>.png and generate a new image instead.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pdraw.trace import TraceConfig, stats, trace
from trace_image import visualise

ROOT = Path(__file__).parent
MODEL = "gemini-3.8-flash-medium"
PROMPT = (
    "Generate an image with your image generation tool: A simple coloring book page of {subject}. "
    "Clean black outlines only, uniform thick line weight, no shading, no hatching, no fills, no text, "
    "pure white background, minimal details, centered. "
    "Save the generated image as drawing.png in the current working directory. Reply only with the file path."
)


def slug(text: str) -> str:
    s = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:40] or "drawing"


def generate(subject: str, target: Path) -> None:
    """Let agy generate the image in an empty folder and move it to `target`."""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = ["agy", "-p", PROMPT.format(subject=subject), "--model", MODEL,
               "--output-format", "json", "--print-timeout", "300s"]
        proc = subprocess.Popen(cmd, cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        start = time.monotonic()
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=1)
                break
            except subprocess.TimeoutExpired:
                print(f"\r   … Gemini malt ({time.monotonic() - start:.0f} s)", end="", flush=True)
        print(f"\r   fertig nach {time.monotonic() - start:.0f} s.            ")
        images = [p for p in Path(tmp).rglob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
        if not images:
            raise SystemExit(f"agy hat kein Bild gespeichert (exit {proc.returncode}):\n{stdout}\n{stderr}")
        shutil.move(str(images[0]), target)


def trace_to(image: Path, out: Path) -> dict:
    result = trace(str(image), TraceConfig())
    info = stats(result)
    out.with_suffix(".json").write_text(json.dumps(result.to_json(), indent=1))
    visualise(result, info, out.with_suffix(".png"), image.name)
    return info


def main():
    args = sys.argv[1:]
    subject = args.pop(0) if args and not args[0].startswith("-") else ""
    while not subject:
        subject = input("Was soll der Roboter malen? ").strip()
    paper = ROOT / "paper.json"
    if not paper.exists():
        raise SystemExit("paper.json fehlt, erst kalibrieren: python3 robot_calibrate.py")

    name = f"{slug(subject)}_{time.strftime('%H%M%S')}"
    image, out = ROOT / "samples" / f"{name}.png", ROOT / "out" / name
    out.parent.mkdir(exist_ok=True)
    while True:
        print(f"1/3 Bild erzeugen: {subject}")
        generate(subject, image)
        print(f"2/3 abpausen: {image.relative_to(ROOT)}")
        info = trace_to(image, out)
        print(f"   {info['primitives']} Primitive ({info['lines']} Linien, {info['arcs']} Bögen), "
              f"{info['strokes']} Striche, Abweichung Ø {info['deviation_mean']}")
        print(f"   Vorschau: {out.with_suffix('.png').relative_to(ROOT)}")
        choice = input("Enter = malen, r = neues Bild, q = abbrechen: ").strip().lower()
        if choice == "q":
            return
        if choice != "r":
            break

    print("3/3 malen", flush=True)
    cmd = [sys.executable, str(ROOT / "robot_draw.py"), str(out.with_suffix(".json")), "--paper", str(paper), *args]
    sys.exit(subprocess.call(cmd, cwd=ROOT))


if __name__ == "__main__":
    main()
