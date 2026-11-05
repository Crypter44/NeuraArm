"""Drawing loop between an LLM and the simulated robot.

Automatic (the model is asked in the background via the Antigravity CLI `agy`):

    python3 play.py new "ein Auto von der Seite" [--model MODEL]
    python3 play.py next [n]   ask the model for the next n rounds (default 1)

Manual (copy & paste with a chat):

    python3 play.py new        start a new drawing (copies PROMPT.md to the clipboard)
    python3 play.py add        apply the JSON actions currently in the clipboard
    python3 play.py add -      same, but read from stdin
    python3 play.py add FILE   same, but read from a file

    python3 play.py show       re-render and print the state

After a manual `add` the feedback text for the LLM is copied to the clipboard and
canvas.png is updated: paste the text and attach the image in the chat.
`agy models` lists the available models.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pdraw import Drawing, render

ROOT = Path(__file__).parent
SESSION = ROOT / "session.json"
CANVAS = ROOT / "canvas.png"
PROMPT = ROOT / "PROMPT.md"
FRAMES = ROOT / "frames"  # one image per round, for showing the progress
RUN = ROOT / "run.json"  # state of an automatic run: task, model, conversation
TRANSCRIPT = ROOT / "transcript.md"
# The model only gets this folder as its workspace, so it sees the image but no code.
WORKSPACE = ROOT / "workspace"

DEFAULT_MODEL = "gemini-3.8-flash-high"
AGY_TIMEOUT = "600s"


def new_drawing() -> Drawing:
    return Drawing(strict_bounds=True, allow_undo=False)


def load() -> Drawing:
    drawing = new_drawing()
    if SESSION.exists():
        for action in json.loads(SESSION.read_text()):
            drawing.apply(action)
    return drawing


def save(drawing: Drawing) -> None:
    SESSION.write_text(json.dumps([s.action for s in drawing.steps], indent=1))
    render(drawing).save(CANVAS)


class AnswerError(ValueError):
    """The LLM answer contains no usable action list."""


def parse_actions(text: str) -> list[dict]:
    """Take the JSON array out of an LLM answer (code block or bare)."""
    block = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if block:
        text = block.group(1)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise AnswerError("no JSON array found in the answer")
    try:
        actions = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise AnswerError(f"invalid JSON, nothing was drawn: {e}")
    if not isinstance(actions, list) or not all(isinstance(a, dict) for a in actions):
        raise AnswerError("expected a JSON array of objects")
    return actions


def feedback(drawing: Drawing, log: list[str]) -> str:
    parts = []
    if log:
        parts += ["Ergebnis deiner Aktionen:", *log, ""]
    parts += ["Aktueller Zustand:", drawing.describe(), "",
              "Hier ist das aktuelle Bild. Was zeichnest du als Nächstes? "
              "Antworte nur mit Text und einem JSON-Array, erzeuge kein Bild."]
    return "\n".join(parts)


def apply_answer(drawing: Drawing, text: str) -> tuple[list[str], bool]:
    """Apply the actions of an LLM answer. Returns (log, done)."""
    actions = parse_actions(text)
    done = any(a.get("type") == "done" for a in actions)
    log = drawing.apply_all([a for a in actions if a.get("type") != "done"])
    save(drawing)
    if log:
        FRAMES.mkdir(exist_ok=True)
        n = len(list(FRAMES.glob("round_*.png"))) + 1
        render(drawing).save(FRAMES / f"round_{n:02d}.png")
    return log, done


def reset() -> None:
    save(new_drawing())
    for frame in FRAMES.glob("round_*.png"):
        frame.unlink()
    RUN.unlink(missing_ok=True)


# ---------------------------------------------------------------- automatic mode

def start_run(task: str, model: str) -> None:
    reset()
    RUN.write_text(json.dumps({"task": task, "model": model, "conversation_id": None,
                               "pending": None, "done": False}, indent=1))
    TRANSCRIPT.write_text(f"# {task}\n\nModell: {model}, gestartet {datetime.now():%Y-%m-%d %H:%M}\n")
    print(f"Neue Zeichnung: „{task}“ mit {model}. Weiter mit: python3 play.py next")


def first_message(task: str) -> str:
    prompt = re.sub(r"\*\*Aufgabe:\*\*.*", f"**Aufgabe:** {task}", PROMPT.read_text())
    return prompt + (
        "\n\nDas aktuelle Bild liegt immer in der Datei `canvas.png` in deinem Arbeitsordner. "
        "Sieh es dir in jeder Runde an. Nutze keine anderen Werkzeuge: kein Code, keine Befehle, "
        "keine anderen Dateien. Deine Aktionen schreibst du nur als JSON-Block in deine Antwort.")


def ask_model(run: dict, message: str) -> str:
    WORKSPACE.mkdir(exist_ok=True)
    shutil.copy(CANVAS, WORKSPACE / "canvas.png")
    cmd = ["agy", "-p", message, "--model", run["model"], "--output-format", "json",
           "--sandbox", "--print-timeout", AGY_TIMEOUT]
    if run["conversation_id"]:
        cmd += ["--conversation", run["conversation_id"]]
    proc = subprocess.Popen(cmd, cwd=WORKSPACE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    start = time.monotonic()
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired:
            print(f"\r   … das Modell denkt nach ({time.monotonic() - start:.0f} s)", end="", flush=True)
    print(f"\r   Antwort nach {time.monotonic() - start:.0f} s.                    ")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"agy failed (exit {proc.returncode}):\n{stdout}\n{stderr}")
    if data.get("status") != "SUCCESS":
        raise SystemExit(f"agy returned status {data.get('status')}:\n{stdout}")
    run["conversation_id"] = data["conversation_id"]
    return data["response"]


def next_round(run: dict, round_no: int) -> None:
    drawing = load()
    message = run["pending"] or first_message(run["task"])
    message += "\n\n(Das aktuelle Bild: `canvas.png` in deinem Arbeitsordner.)" if run["pending"] else ""

    print(f"── Runde {round_no}: frage {run['model']} …", flush=True)
    answer = ask_model(run, message)
    print(answer.strip(), "\n")

    try:
        log, done = apply_answer(drawing, answer)
    except AnswerError as e:
        log, done = [f"ERROR {e}. Bitte antworte mit einem gültigen JSON-Array."], False
    print("\n".join(log))
    run["pending"] = feedback(drawing, log)
    run["done"] = done

    with TRANSCRIPT.open("a") as f:
        f.write(f"\n## Runde {round_no}\n\n### Nachricht an das Modell\n\n{message}\n\n"
                f"### Antwort\n\n{answer.strip()}\n\n### Ergebnis\n\n" + "\n".join(log) + "\n")


def run_rounds(n: int) -> None:
    if not RUN.exists():
        raise SystemExit('Erst eine Zeichnung starten: python3 play.py new "Motiv"')
    run = json.loads(RUN.read_text())
    for _ in range(n):
        if run["done"]:
            print("Das Modell hat die Zeichnung abgeschlossen.")
            break
        round_no = TRANSCRIPT.read_text().count("\n## Runde ") + 1
        try:
            next_round(run, round_no)
        finally:
            RUN.write_text(json.dumps(run, indent=1))
    if run["done"]:
        print("✔ Das Modell meldet: fertig.")
    print(f"→ Bild aktualisiert: {CANVAS.name}")


# ---------------------------------------------------------------- manual mode

def finish(drawing: Drawing, log: list[str], clipboard: bool = True) -> None:
    save(drawing)
    text = feedback(drawing, log)
    print(text)
    if clipboard:
        subprocess.run(["pbcopy"], input=text.encode())
        print(f"\n→ Text ist in der Zwischenablage, Bild: {CANVAS.name}")
    else:
        print(f"\n→ Bild aktualisiert: {CANVAS.name}")


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "show"

    if cmd == "new" and len(argv) > 1:
        model = DEFAULT_MODEL
        if "--model" in argv:
            i = argv.index("--model")
            model = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
        start_run(" ".join(argv[1:]), model)
    elif cmd == "new":
        reset()
        subprocess.run(["pbcopy"], input=PROMPT.read_bytes())
        print("Neue Zeichnung. PROMPT.md ist in der Zwischenablage. Aufgabe unten eintragen und in den Chat einfügen.")
    elif cmd == "next":
        run_rounds(int(argv[1]) if len(argv) > 1 else 1)
    elif cmd == "add":
        source = argv[1] if len(argv) > 1 else None
        if source is None:
            text = subprocess.run(["pbpaste"], capture_output=True, text=True).stdout
        elif source == "-":
            text = sys.stdin.read()
        else:
            text = Path(source).read_text()
        drawing = load()
        try:
            log, done = apply_answer(drawing, text)
        except AnswerError as e:
            raise SystemExit(str(e))
        if done:
            print("Das LLM meldet: fertig.")
        finish(drawing, log, clipboard=source is None)
    elif cmd == "show":
        finish(load(), [])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
