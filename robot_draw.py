"""Draw a traced JSON (out/*.json) with the LARA.

    python3 robot_draw.py out/smiley.json --dry   # no robot: print the plan, write out/smiley_robot.png
    python3 robot_draw.py out/smiley.json --sim   # in the simulation
    python3 robot_draw.py out/smiley.json         # on the real arm, asks for confirmation first

Before running: jog the pen so it just touches the paper where the CENTRE of the drawing should be.
That pose is the origin; the drawing spans --size metres around it in the XY plane of the robot base,
with the pen lifted by --lift between strokes. Keep the E-stop within reach.

With --paper paper.json (from robot_calibrate.py) the origin and the tilted paper plane come from the
calibration instead; the pen then starts wherever it is and first moves above the calibrated centre.
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from pdraw.robot import PaperFrame, path_length, plan, strokes_from_json

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

OVERRIDE = 1.0
ACCELERATION = 0.1    # m/s²


def preview(moves, out_png: Path, title: str):
    fig, ax = plt.subplots(figsize=(7, 7))
    pos = moves[0].poses[0]
    for m in moves:
        pts = [pos] + m.poses
        xs, ys = [p[0] * 1000 for p in pts], [p[1] * 1000 for p in pts]
        if m.kind == "draw":
            ax.plot(xs, ys, color="black", lw=1.6)
        else:
            ax.plot(xs, ys, color="0.6", lw=0.8, ls=(0, (2, 3)))
        pos = m.poses[-1]
    ax.scatter([0], [0], marker="+", color="red", s=80, zorder=3, label="Startpose (Stift auf Papier)")
    ax.set_aspect("equal")
    ax.set_xlabel("Δx Roboterbasis [mm]")
    ax.set_ylabel("Δy Roboterbasis [mm]")
    ax.set_title(title)
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.3)
    fig.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("json", type=Path)
    ap.add_argument("--size", type=float, default=0.10, help="m the whole canvas spans (default 0.10)")
    ap.add_argument("--lift", type=float, default=0.02, help="m the pen is lifted between strokes")
    ap.add_argument("--press", type=float, default=0.0, help="m the pen goes below the origin while drawing")
    ap.add_argument("--speed", type=float, default=0.02, help="m/s while drawing")
    ap.add_argument("--travel-speed", type=float, default=0.04, help="m/s with the pen lifted")
    ap.add_argument("--tol", type=float, default=0.0002,
                    help="m an arc may stray from its chords (fewer points = fewer stops)")
    ap.add_argument("--blend", type=float, default=0.0005,
                    help="m blend radius between the points of one primitive (keeps the speed up)")
    ap.add_argument("--per-point", action="store_true",
                    help="one move_linear per point (stops at every point) instead of one per primitive")
    ap.add_argument("--paper", type=Path, help="calibrated paper plane from robot_calibrate.py")
    ap.add_argument("--rotate", type=float, default=0.0, help="degrees to turn the drawing")
    ap.add_argument("--host", default="192.168.2.13")
    ap.add_argument("--sim", action="store_true", help="use the simulation instead of the real arm")
    ap.add_argument("--dry", action="store_true", help="do not connect, only print the plan and write the preview")
    args = ap.parse_args()

    data = json.loads(args.json.read_text())
    canvas = data.get("canvas", 512)

    paper = json.loads(args.paper.read_text()) if args.paper else None

    def make_plan(origin, axes=None):
        frame = PaperFrame(origin, args.size, canvas, args.rotate, **(axes or {}))
        strokes = strokes_from_json(data, tol=args.tol / frame.scale)
        return plan(strokes, frame, args.lift, args.press)

    # summary + preview relative to a zero origin, so it does not need the robot
    moves = make_plan([0.0] * 6)
    pos, draw_len, travel_len = moves[0].poses[0], 0.0, 0.0
    for m in moves:
        d = path_length(m, pos)
        draw_len, travel_len = (draw_len + d, travel_len) if m.kind == "draw" else (draw_len, travel_len + d)
        pos = m.poses[-1]
    n_strokes = len(data["strokes"])
    n_points = sum(len(m.poses) for m in moves)
    n_calls = n_points if args.per_point else sum(1 if m.kind == "draw" else len(m.poses) for m in moves)
    eta = draw_len / args.speed + travel_len / args.travel_speed  # without stops and blending
    summary = (f"{n_strokes} Striche, {n_points} Punkte in {n_calls} Aufrufen, {args.size * 1000:.0f} mm Feld, "
               f"gezeichnet {draw_len * 1000:.0f} mm, Leerfahrt {travel_len * 1000:.0f} mm, ≥ {eta:.0f} s")
    out_png = args.json.with_name(args.json.stem + "_robot.png")
    preview(moves, out_png, f"{args.json.name}: {summary}")
    print(summary)
    print(f"preview → {out_png}")
    if args.dry:
        return

    from neurapy.robot import Robot

    r = Robot(args.host)
    if args.sim:
        r.switch_to_simulation()
    else:
        half = args.size * 500
        where = f"the calibrated centre in {args.paper}" if paper else "the current pose"
        prompt = (f"REAL arm will draw within ±{half:.0f} mm around {where}, "
                  f"lifting {args.lift * 1000:.0f} mm between strokes, pressing {args.press * 1000:.1f} mm. Type 'yes': ")
        if input(prompt) != "yes":
            print("aborted")
            return
        r.switch_to_real()
    print("simulation:", r.is_robot_in_simulation())

    r.switch_to_automatic_mode()
    r.set_override(OVERRIDE)
    print("automatic:", r.is_robot_in_automatic_mode(), "override:", r.get_override())

    if r.is_robot_in_simulation() != args.sim:
        print("robot did not switch to", "simulation" if args.sim else "the real arm", "- aborted")
        return
    if paper:
        origin = paper["origin"]
        moves = make_plan(origin, {k: tuple(paper[k]) for k in ("x_axis", "y_axis", "normal")})
    else:
        # read the origin only now: before the switch it would be the pose of the other (sim/real) arm
        origin = r.get_tcp_pose()  # [x, y, z, roll, pitch, yaw]; the pen touches the paper here
        moves = make_plan(origin)
    print("origin:", [round(v, 4) for v in origin])

    try:
        for i, m in enumerate(moves, 1):
            print(f"[{i}/{len(moves)}] {m.kind} ({len(m.poses)} points)")
            if m.kind == "draw" and not args.per_point and len(m.poses) > 1:
                # one primitive in one call, blended so the pen keeps its speed between the points
                r.move_linear(target_pose=m.poses, speed=args.speed, acceleration=ACCELERATION,
                              blending=True, blending_mode=2, blend_radius=args.blend, enable_safety=True)
                continue
            # travel (lift, lower) point by point, so the pen goes straight down onto the paper
            speed = args.speed if m.kind == "draw" else args.travel_speed
            for pose in m.poses:
                r.move_linear(target_pose=[pose], speed=speed, acceleration=ACCELERATION, enable_safety=True)
        print("done, pen lifted above the origin:", [round(v, 4) for v in r.get_tcp_pose()])
    finally:
        r.stop()


if __name__ == "__main__":
    main()
