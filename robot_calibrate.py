"""Calibrate the paper plane by touching points with the pen, or measure how flat the paper is.

    python3 robot_calibrate.py             # touch 4 points, writes paper.json
    python3 robot_calibrate.py --measure   # touch as many points as you like, prints their height above paper.json

The script never moves the robot. Guide the pen yourself (free drive / pendant) and press Enter
when it just touches the paper. robot_draw.py --paper paper.json then draws on that plane.
"""

import argparse
import json
import math
import sys
from pathlib import Path

from pdraw.robot import plane_coords, plane_from_points

sys.path.insert(0, str(Path(__file__).parent / "vendor"))

STEPS = [
    ("center", "Stift berührt das Papier in der MITTE der Zeichnung"),
    ("right", "Stift berührt das Papier ein paar cm RECHTS davon (gibt die x-Richtung der Zeichnung)"),
    ("third", "Stift berührt das Papier an einem dritten Punkt, möglichst OBERHALB der Mitte, weit weg von den anderen"),
    ("above", "Stift ein paar cm vom Papier ABGEHOBEN, irgendwo über dem Blatt"),
]


def read_pose(r, prompt: str):
    if input(f"{prompt}  [Enter, q = Ende] ").strip().lower() == "q":
        return None
    return r.get_tcp_pose()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", type=Path, default=Path("paper.json"))
    ap.add_argument("--measure", action="store_true", help="measure points against an existing calibration")
    ap.add_argument("--host", default="192.168.2.13")
    args = ap.parse_args()

    from neurapy.robot import Robot

    r = Robot(args.host)
    if r.is_robot_in_simulation():
        r.switch_to_real()  # no motion, but the pose must come from the real arm
    if r.is_robot_in_simulation():
        print("robot is still in simulation, switch to the real arm first")
        return

    if args.measure:
        paper = json.loads(args.paper.read_text())
        heights = []
        print("Messmodus: Stift an beliebige Stellen aufs Papier, Höhe über der kalibrierten Ebene in mm")
        while (pose := read_pose(r, f"Punkt {len(heights) + 1}")) is not None:
            x, y, h = plane_coords(paper, pose)
            heights.append(h)
            print(f"  rechts {x * 1000:7.1f}  oben {y * 1000:7.1f}  Höhe {h * 1000:+6.2f} mm")
        if heights:
            print(f"{len(heights)} Punkte, Höhe von {min(heights) * 1000:+.2f} bis {max(heights) * 1000:+.2f} mm, "
                  f"Spanne {(max(heights) - min(heights)) * 1000:.2f} mm")
        return

    poses = {}
    for key, text in STEPS:
        pose = read_pose(r, text)
        if pose is None:
            print("aborted, nothing written")
            return
        poses[key] = pose
        print("  ", [round(v, 4) for v in pose])

    paper = plane_from_points(poses["center"], poses["right"], poses["third"], poses["above"])
    paper["touched"] = poses
    tilt = math.degrees(math.acos(min(1.0, abs(paper["normal"][2]))))
    print(f"Ebene um {tilt:.2f}° gegen die XY-Ebene der Roboterbasis gekippt")
    args.paper.write_text(json.dumps(paper, indent=2))
    print(f"→ {args.paper}; prüfen mit: python3 robot_calibrate.py --measure")


if __name__ == "__main__":
    main()
