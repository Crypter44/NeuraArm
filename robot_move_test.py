"""Minimal motion test: move the TCP 2 cm straight up and back down.

    python3 robot_move_test.py          # on the real arm, asks for confirmation first
    python3 robot_move_test.py --sim    # in the simulation (only the model in the web GUI moves)

Before the real run: keep the E-stop within reach and make sure there are 2 cm free above the tool.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
from neurapy.robot import Robot  # noqa: E402

LIFT = 0.10       # m, upwards (+z in the robot base frame)
SPEED = 0.02      # m/s
OVERRIDE = 1.0    # % of the programmed speeds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.2.13")
    ap.add_argument("--sim", action="store_true", help="use the simulation instead of the real arm")
    args = ap.parse_args()

    r = Robot(args.host)

    if args.sim:
        r.switch_to_simulation()
    else:
        if input(f"REAL arm will move {LIFT * 100:.0f} cm up and back. Type 'yes': ") != "yes":
            print("aborted")
            return
        r.switch_to_real()
    print("simulation:", r.is_robot_in_simulation())

    r.switch_to_automatic_mode()
    r.set_override(OVERRIDE)
    print("automatic:", r.is_robot_in_automatic_mode(), "override:", r.get_override())

    start = r.get_tcp_pose()  # [x, y, z, roll, pitch, yaw]
    up = list(start)
    up[2] += LIFT
    print("start:", [round(v, 4) for v in start])

    try:
        print("moving up")
        r.move_linear(target_pose=[up], speed=SPEED, acceleration=0.1, enable_safety=True)
        print("up: ", [round(v, 4) for v in r.get_tcp_pose()])
        print("moving down")
        r.move_linear(target_pose=[start], speed=SPEED, acceleration=0.1, enable_safety=True)
        end = r.get_tcp_pose()
        print("end:  ", [round(v, 4) for v in end])
    finally:
        r.stop()


if __name__ == "__main__":
    main()
