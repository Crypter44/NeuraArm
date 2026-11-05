"""Core model: a pen that is moved by discrete drawing primitives.

Coordinates are math-style (origin bottom-left, y up). Headings are
discretized to 16 compass directions (22.5 degree steps), so every
primitive ends on the heading grid and consecutive primitives always join
tangentially (except at an explicit corner).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

HEADING_STEPS = 16
STEP_DEG = 360 / HEADING_STEPS

# Index 0 = East, counter-clockwise (math convention).
COMPASS = [
    "E", "ENE", "NE", "NNE", "N", "NNW", "NW", "WNW",
    "W", "WSW", "SW", "SSW", "S", "SSE", "SE", "ESE",
]

LENGTHS = {"S": 25.0, "M": 50.0, "L": 100.0}

START_POINTS = {
    "center": (0.5, 0.5),
    "top_left": (0.15, 0.85),
    "top_right": (0.85, 0.85),
    "bottom_left": (0.15, 0.15),
    "bottom_right": (0.85, 0.15),
}

PRIMITIVE_TYPES = ("line", "curve", "corner", "move", "jump")
PEN_UP_TYPES = ("move", "jump")
LENGTH_GRID = 5
MAX_LENGTH = 400
SAMPLES_PER_90_DEG = 12


class ActionError(ValueError):
    """Invalid action. The message is meant to be shown to the LLM."""


@dataclass(frozen=True)
class Pen:
    x: float
    y: float
    heading: int  # index into COMPASS

    @property
    def angle(self) -> float:
        return math.radians(self.heading * STEP_DEG)


@dataclass
class Step:
    action: dict
    points: list[tuple[float, float]]
    pen_before: Pen
    pen_after: Pen
    label: str | None

    @property
    def drawn(self) -> bool:
        return self.action.get("type") not in PEN_UP_TYPES


@dataclass
class Drawing:
    width: int = 512
    height: int = 512
    start_points: tuple[str, ...] = tuple(START_POINTS)
    # live mode (robot): leaving the canvas is an error and undo is not possible
    strict_bounds: bool = False
    allow_undo: bool = True
    steps: list[Step] = field(default_factory=list)
    labels: dict[str, Pen] = field(default_factory=dict)
    pen: Pen | None = None

    def __post_init__(self):
        for name in self.start_points:
            if name not in START_POINTS:
                raise ValueError(f"unknown start point {name!r}")
            fx, fy = START_POINTS[name]
            self.labels[name] = Pen(fx * self.width, fy * self.height, COMPASS.index("N"))
        self.pen = self.labels[self.start_points[0]]

    # ------------------------------------------------------------------ actions

    def apply(self, action: dict) -> list[str]:
        """Apply one action. Returns warnings; raises ActionError if invalid."""
        if action.get("type") == "undo":
            if not self.allow_undo:
                raise ActionError("undo is not possible: what is drawn stays on the paper")
            self.undo(int(action.get("n", 1)))
            return []

        pen = self._resolve_origin(action)
        points, pen_after = trace(pen, action)
        warnings = self._bounds_warnings(points)
        if warnings and self.strict_bounds:
            raise ActionError("primitive would leave the paper; it was not executed")

        label = action.get("label")
        if label is not None:
            if not isinstance(label, str) or not label.strip():
                raise ActionError("label must be a non-empty string")
            if label in self.labels:
                raise ActionError(f"label {label!r} already exists; labels must be unique")
            self.labels[label] = pen_after

        self.steps.append(Step(action, points, pen, pen_after, label))
        self.pen = pen_after
        return warnings

    def apply_all(self, actions: list[dict]) -> list[str]:
        """Apply actions in order, stopping at the first error.

        Returns a log of one line per action, suitable as LLM feedback.
        """
        log = []
        for i, action in enumerate(actions):
            try:
                warnings = self.apply(action)
            except ActionError as e:
                log.append(f"#{i}: ERROR {e} (remaining actions skipped)")
                break
            p = self.pen
            line = f"#{i}: ok, pen now at ({p.x:.0f}, {p.y:.0f}) heading {COMPASS[p.heading]}"
            line += self._check_expectation(action)
            log.append(line + "".join(f"; warning: {w}" for w in warnings))
        return log

    def undo(self, n: int = 1) -> None:
        if n < 1:
            raise ActionError("undo n must be >= 1")
        if n > len(self.steps):
            raise ActionError(f"cannot undo {n} steps, only {len(self.steps)} exist")
        for _ in range(n):
            step = self.steps.pop()
            if step.label is not None:
                del self.labels[step.label]
            self.pen = step.pen_before

    # ------------------------------------------------------------------ helpers

    def _check_expectation(self, action: dict) -> str:
        """Compare the optional "expect": [x, y] of an action with the real end point."""
        expect = action.get("expect")
        if not (isinstance(expect, list) and len(expect) == 2
                and all(isinstance(v, (int, float)) for v in expect)):
            return ""
        off = math.dist(expect, (self.pen.x, self.pen.y))
        if off <= 5:
            return " (as expected)"
        return f" (you expected ({expect[0]:.0f}, {expect[1]:.0f}), off by {off:.0f})"

    def _resolve_origin(self, action: dict) -> Pen:
        pen = self.pen
        origin = action.get("from")
        if origin is not None:
            if origin not in self.labels:
                known = ", ".join(self.labels) or "none"
                raise ActionError(f"unknown label {origin!r}; known labels: {known}")
            pen = self.labels[origin]
        if "heading" in action:
            pen = Pen(pen.x, pen.y, parse_heading(action["heading"]))
        return pen

    def _bounds_warnings(self, points) -> list[str]:
        outside = [p for p in points if not (0 <= p[0] <= self.width and 0 <= p[1] <= self.height)]
        return ["primitive leaves the canvas"] if outside else []

    def describe(self) -> str:
        """Text summary of the state, to accompany the annotated image."""
        lines = [
            f"canvas: {self.width}x{self.height}, "
            f"primitives drawn: {sum(s.drawn for s in self.steps)}, "
            f"pen lifts: {self.pen_lifts()}",
            f"pen: heading {COMPASS[self.pen.heading]}, at {self._where(self.pen)}",
            "labels:",
        ]
        for name, p in self.labels.items():
            kind = " (start point)" if name in self.start_points else ""
            lines.append(f"  - {name}{kind}: heading {COMPASS[p.heading]}, at {self._where(p)}")
        return "\n".join(lines)

    def pen_lifts(self) -> int:
        """Number of times the pen has to leave the paper between primitives."""
        drawn = [s for s in self.steps if s.drawn]
        return sum(
            1 for prev, step in zip(drawn, drawn[1:])
            if math.dist((prev.pen_after.x, prev.pen_after.y), (step.pen_before.x, step.pen_before.y)) > 1e-6
        )

    def _where(self, p: Pen) -> str:
        fx, fy = p.x / self.width, p.y / self.height
        horiz = "left" if fx < 1 / 3 else "right" if fx > 2 / 3 else "middle"
        vert = "bottom" if fy < 1 / 3 else "top" if fy > 2 / 3 else "middle"
        return f"({p.x:.0f}, {p.y:.0f}) {vert}-{horiz}"


def parse_heading(value) -> int:
    if isinstance(value, str) and value.upper() in COMPASS:
        return COMPASS.index(value.upper())
    raise ActionError(f"heading must be one of {', '.join(COMPASS)}; got {value!r}")


def parse_angle_steps(action: dict, max_deg: float) -> int:
    angle = action.get("angle")
    if not isinstance(angle, (int, float)):
        raise ActionError(f"{action['type']} needs a numeric 'angle'")
    steps = angle / STEP_DEG
    if abs(steps - round(steps)) > 1e-9 or not 0 < angle <= max_deg:
        raise ActionError(
            f"angle must be a multiple of {STEP_DEG} in ({0}, {max_deg:g}]; got {angle}")
    turn = action.get("turn")
    if turn not in ("left", "right"):
        raise ActionError(f"{action['type']} needs 'turn': 'left' or 'right'")
    return round(steps) * (1 if turn == "left" else -1)


def parse_length(action: dict) -> float:
    length = action.get("length", "M")
    if isinstance(length, str) and length in LENGTHS:
        return LENGTHS[length]
    if isinstance(length, (int, float)) and 0 < length <= MAX_LENGTH and length % LENGTH_GRID == 0:
        return float(length)
    raise ActionError(
        f"length must be a multiple of {LENGTH_GRID} up to {MAX_LENGTH} (or S/M/L); got {length!r}")


def parse_radius(action: dict) -> float:
    radius = action["radius"]
    if isinstance(radius, (int, float)) and 0 < radius <= MAX_LENGTH / 2 and radius % LENGTH_GRID == 0:
        return float(radius)
    raise ActionError(f"radius must be a multiple of {LENGTH_GRID} up to {MAX_LENGTH // 2}; got {radius!r}")


def parse_target(action: dict) -> tuple[float, float]:
    x, y = action.get("x"), action.get("y")
    if not all(isinstance(v, (int, float)) and v % LENGTH_GRID == 0 for v in (x, y)):
        raise ActionError(f"jump needs 'x' and 'y' as multiples of {LENGTH_GRID}; got x={x!r}, y={y!r}")
    return float(x), float(y)


def trace(pen: Pen, action: dict) -> tuple[list[tuple[float, float]], Pen]:
    """Compute the polyline of a primitive and the resulting pen."""
    kind = action.get("type")
    if kind not in PRIMITIVE_TYPES:
        raise ActionError(f"type must be one of {', '.join(PRIMITIVE_TYPES)} or undo; got {kind!r}")

    if kind == "jump":
        end = parse_target(action)
        return [(pen.x, pen.y), end], Pen(*end, pen.heading)

    if kind == "curve":
        return trace_arc(pen, action)

    length = parse_length(action)

    if kind == "corner":
        dsteps = parse_angle_steps(action, 180)
        pen = Pen(pen.x, pen.y, (pen.heading + dsteps) % HEADING_STEPS)
        kind = "line"

    if kind in ("line", "move"):
        a = pen.angle
        end = (pen.x + length * math.cos(a), pen.y + length * math.sin(a))
        return [(pen.x, pen.y), end], Pen(*end, pen.heading)

    raise AssertionError(kind)


def trace_arc(pen: Pen, action: dict) -> tuple[list[tuple[float, float]], Pen]:
    """Circular arc given by `radius` or by arc `length`."""
    dsteps = parse_angle_steps(action, 360)
    dtheta = math.radians(dsteps * STEP_DEG)
    radius = parse_radius(action) if "radius" in action else parse_length(action) / abs(dtheta)
    sign = 1 if dtheta > 0 else -1
    a0 = pen.angle
    # centre lies perpendicular to the heading, on the turning side
    cx = pen.x - sign * radius * math.sin(a0)
    cy = pen.y + sign * radius * math.cos(a0)
    n = max(4, math.ceil(abs(dtheta) / (math.pi / 2) * SAMPLES_PER_90_DEG))
    points = []
    for i in range(n + 1):
        a = a0 + dtheta * i / n
        points.append((cx + sign * radius * math.sin(a), cy - sign * radius * math.cos(a)))
    end_heading = (pen.heading + dsteps) % HEADING_STEPS
    return points, Pen(*points[-1], end_heading)
