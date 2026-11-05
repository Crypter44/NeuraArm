# PrimitiveDrawingGame

Turns a short word into a line drawing made by a NEURA LARA 5: Gemini generates line art, the
image is traced into ordered lines and arcs, and the robot draws them.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`vendor/neurapy` is NEURA's proprietary client, so don't publish it. Image generation needs the
Antigravity CLI (`agy`) on your `PATH`.

Connect to the robot. The address is lost when the Mac restarts or the adapter is unplugged, so
repeat this each session. `en7` is the USB LAN adapter here; check yours with
`networksetup -listallhardwareports`.

```bash
sudo ifconfig en7 alias 192.168.2.10 255.255.255.0
ping -c 2 192.168.2.13
python robot_readtest.py    # read-only, doesn't move the arm
```

The web GUI is at http://192.168.2.13:8080.

Before drawing, release the E-stop and press the white reset button. After switching to Real in
the web GUI the arm drops to unpowered (red), so press **Power On** again.

## All in one: `draw.py`

```bash
python draw.py                              # asks what to draw
python draw.py "a house"                    # skips the question
python draw.py "a house" --sim --size 0.12  # extra options go to robot_draw.py
python draw.py "a house" --dry              # no robot, only the plan and preview
```

1. Gemini generates the image: `samples/<name>.png` (about a minute).
2. The image is traced: `out/<name>.json`, with a preview in `out/<name>.png`.
3. You choose: Enter to draw, `r` for a new image, `q` to quit.
4. `robot_draw.py` draws on the plane from `paper.json`. The real arm only moves after you type `yes`.

`draw.py` needs a `paper.json`, so calibrate first.

## Calibration

The script never moves the robot. Guide the pen yourself (free drive or pendant) and press Enter at
each point.

```bash
python robot_calibrate.py            # writes paper.json
python robot_calibrate.py --measure  # height of any touched point above the plane, in mm
```

It asks for four poses:

1. Pen touching the paper at the **centre** of the drawing. This also sets the pen orientation.
2. Pen touching the paper a few cm to the **right** of the centre. This sets the drawing's x direction.
3. Pen touching the paper at a **third** point, far from the other two and not in line with them.
4. Pen lifted a few cm **above** the paper.

Calibrate again whenever the table or board moves.

## Tracing only

```bash
python trace_image.py samples/car.png
python trace_image.py samples/car.png --angle-step 5 --tolerance 1.5 --out out/car
```

This writes `out/car.json` (the primitives in drawing order) and `out/car.png` (the preview, coloured
by order, with pen-up travel dashed).

## Drawing only

```bash
python robot_draw.py out/smiley.json --paper paper.json --dry  # no robot, writes out/smiley_robot.png
python robot_draw.py out/smiley.json --paper paper.json --sim  # simulation
python robot_draw.py out/smiley.json --paper paper.json        # real arm, asks for 'yes'
```

With `--paper`, the pen first moves in a straight line from wherever it is to above the calibrated
centre, so keep that path clear. Without `--paper`, the current pose is the centre and the drawing
lies in the robot base's XY plane. Keep the E-stop within reach.

| Option | Default | Meaning |
|---|---|---|
| `--size` | 0.10 | Metres the whole canvas spans |
| `--lift` | 0.02 | Metres the pen lifts between strokes |
| `--press` | 0 | Metres the pen goes below the paper plane while drawing |
| `--speed` | 0.02 | Drawing speed in m/s |
| `--travel-speed` | 0.04 | Speed in m/s with the pen lifted |
| `--tol` | 0.0002 | Metres an arc may stray from its straight chords |
| `--blend` | 0.0005 | Blend radius in metres between points |
| `--rotate` | 0 | Degrees to turn the drawing |

## Tests

```bash
python -m unittest discover -s tests -t .
```
