"""Turn traced primitives (out/*.json) into robot poses on a flat sheet of paper.

Pure geometry, no robot connection: robot_draw.py feeds the result to NeuraPy.

The pose the pen has when it touches the paper at the centre of the drawing is the `origin`;
canvas units are scaled so the whole canvas spans `size` metres, the orientation of the origin pose
is kept for every point. Without calibration the paper is the XY plane of the robot base through
the origin; `plane_from_points` turns three touched points into a tilted paper plane instead.
"""

import math
from dataclasses import dataclass

import numpy as np


def segment_points(pen, heading: float, seg: dict, tol: float) -> np.ndarray:
    """Points of one primitive (line or arc, as in the trace JSON) starting at `pen`.
    A line is just its two ends; an arc is cut into chords that stray at most `tol` canvas units from it."""
    pen = np.asarray(pen, float)
    h = math.radians(heading)
    if seg["type"] == "line":
        return np.array([pen, pen + seg["length"] * np.array([math.cos(h), math.sin(h)])])
    # arc: positive angle turns left, the centre lies to the left of the heading (right for negative)
    sign = 1.0 if seg["angle"] >= 0 else -1.0
    r = seg["radius"]
    center = pen + sign * r * np.array([-math.sin(h), math.cos(h)])
    start = math.atan2(pen[1] - center[1], pen[0] - center[0])
    sweep = math.radians(seg["angle"])
    max_step = 2 * math.acos(max(-1.0, 1 - tol / r))  # sweep per chord with sagitta == tol
    n = max(2, math.ceil(abs(sweep) / max_step))
    a = start + np.linspace(0, sweep, n + 1)
    return center + r * np.column_stack([np.cos(a), np.sin(a)])


def stroke_segments(stroke: dict, tol: float) -> list[np.ndarray]:
    """One polyline per primitive of a stroke, in canvas units; each starts where the previous ended."""
    pen, parts = np.asarray(stroke["start"], float), []
    for seg in stroke["segments"]:
        pts = segment_points(pen, seg["heading"], seg, tol)
        parts.append(pts)
        pen = pts[-1]
    return parts


def strokes_from_json(data: dict, tol: float = 1.0) -> list[list[np.ndarray]]:
    return [stroke_segments(s, tol) for s in data["strokes"]]


@dataclass
class PaperFrame:
    origin: list[float]       # [x, y, z, roll, pitch, yaw] of the pen touching the paper at the drawing centre
    size: float               # metres the whole canvas spans
    canvas: float = 512.0
    rotate: float = 0.0       # degrees, turns the drawing around the centre
    x_axis: tuple = (1.0, 0.0, 0.0)   # unit vectors in the base frame: drawing right, drawing up,
    y_axis: tuple = (0.0, 1.0, 0.0)   # and the paper normal pointing away from the paper (pen lift)
    normal: tuple = (0.0, 0.0, 1.0)

    @property
    def scale(self) -> float:
        return self.size / self.canvas

    def to_xy(self, pts: np.ndarray) -> np.ndarray:
        """Canvas units -> metres relative to the origin along the paper axes, rotated by `rotate`."""
        c, s = math.cos(math.radians(self.rotate)), math.sin(math.radians(self.rotate))
        rel = (np.asarray(pts, float) - self.canvas / 2) * self.scale
        return rel @ np.array([[c, s], [-s, c]])

    def pose(self, xy, lift: float = 0.0) -> list[float]:
        p = (np.asarray(self.origin[:3]) + float(xy[0]) * np.asarray(self.x_axis)
             + float(xy[1]) * np.asarray(self.y_axis) + lift * np.asarray(self.normal))
        return [*map(float, p), *self.origin[3:]]


def plane_from_points(center, right, third, above) -> dict:
    """Paper plane from touched TCP poses: `center` (drawing centre, becomes the origin incl. its
    orientation), `right` (towards the drawing's right), `third` (any other point on the paper,
    not on the line centre-right) and `above` (the pen lifted off the paper, gives the lift side).
    The drawing's up axis follows from right and normal, so the picture is never mirrored."""
    c, r, t, a = (np.asarray(v[:3], float) for v in (center, right, third, above))
    x = r - c
    if np.linalg.norm(x) < 0.01 or np.linalg.norm(np.cross(x, t - c)) < 1e-4:
        raise ValueError("points too close together or on one line, touch them further apart")
    x /= np.linalg.norm(x)
    n = np.cross(x, t - c)
    n /= np.linalg.norm(n)
    if (a - c) @ n < 0:
        n = -n
    y = np.cross(n, x)
    return {"origin": [float(v) for v in center], "x_axis": x.tolist(), "y_axis": y.tolist(),
            "normal": n.tolist()}


def plane_coords(paper: dict, pose) -> tuple[float, float, float]:
    """(right, up, height above the paper) of a TCP pose in the calibrated paper frame, metres."""
    d = np.asarray(pose[:3], float) - np.asarray(paper["origin"][:3])
    return float(d @ paper["x_axis"]), float(d @ paper["y_axis"]), float(d @ paper["normal"])


@dataclass
class Move:
    kind: str                 # "draw" (pen on paper) or "travel" (pen lifted)
    poses: list[list[float]]


def plan(strokes: list[list[np.ndarray]], frame: PaperFrame, lift: float, press: float = 0.0) -> list[Move]:
    """Pen up at the origin, then for every stroke: travel above its start, lower, draw each primitive
    as its own move, lift. While drawing the pen goes `press` metres below the origin.
    Ends lifted above the origin."""
    moves = [Move("travel", [frame.pose((0, 0), lift)])]
    for segments in strokes:
        first = frame.to_xy(segments[0][:1])[0]
        moves.append(Move("travel", [frame.pose(first, lift), frame.pose(first, -press)]))
        for pts in segments:
            moves.append(Move("draw", [frame.pose(p, -press) for p in frame.to_xy(pts)[1:]]))
        last = frame.to_xy(segments[-1][-1:])[0]
        moves.append(Move("travel", [frame.pose(last, lift)]))
    moves.append(Move("travel", [frame.pose((0, 0), lift)]))
    return moves


def path_length(move: Move, start: list[float]) -> float:
    pts = np.array([start] + move.poses)[:, :3]
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
