"""Read-only connection test against the LARA: prints state, never moves the robot.

    python3 robot_readtest.py [--host 192.168.2.13]
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "vendor"))
from neurapy.robot import Robot  # noqa: E402

READ_CALLS = [
    "is_robot_in_simulation",
    "is_robot_in_teach_mode",
    "is_robot_in_automatic_mode",
    "get_override",
    "get_current_joint_angles",
    "get_tcp_pose_quaternion",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.2.13")
    args = ap.parse_args()

    r = Robot(args.host)
    for attr in ("robot_name", "dof", "payload", "version", "current_tool"):
        print(f"{attr:28s} {getattr(r, attr, '?')}")

    available = set(r.get_functions())
    print(f"{'functions available':28s} {len(available)}")
    for name in READ_CALLS:
        if name not in available:
            print(f"{name:28s} (not available)")
            continue
        try:
            value = getattr(r, name)()
        except Exception as e:  # report and go on, this is only a probe
            value = f"ERROR {e}"
        if name == "get_current_joint_angles" and isinstance(value, list):
            value = ", ".join(f"{math.degrees(a):7.2f}°" for a in value)
        print(f"{name:28s} {value}")


if __name__ == "__main__":
    main()
