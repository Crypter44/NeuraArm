"""Turn a line-art image into an ordered list of robot drawing primitives.

Pipeline:
  1. binarize       dark pixels are ink
  2. thin           Zhang-Suen thinning to a 1 px skeleton, then remove staircase pixels
  3. graph          split the skeleton at junctions into edges, prune short spurs
  4. strokes        join edges through junctions where the line continues straight
  5. order          greedy nearest neighbour + 2-opt to minimise pen-up travel
  6. fit            approximate each stroke with lines and circular arcs (greedy, within a tolerance)
  7. quantize       turtle-style segments with quantized headings / lengths; every segment aims at the
                    traced target from the *actual* pen position, so quantization errors do not accumulate

Coordinates of the result are canvas units (y up), headings in degrees counter-clockwise from east,
like the rest of pdraw.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

NEIGHBOURS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


@dataclass
class TraceConfig:
    canvas: float = 512.0
    margin: float = 16.0
    threshold: int = 128         # gray value below which a pixel is ink
    angle_step: float = 5.0      # heading / arc angle quantization in degrees
    length_step: float = 1.0     # length / radius / position quantization in canvas units
    tolerance: float = 1.5       # max deviation of a primitive from the traced line (canvas units)
    min_radius: float = 2.0      # smaller arcs are drawn as lines
    min_stroke: float = 4.0      # drop strokes shorter than this (noise)
    spur_length: float = 10.0    # prune dead-end branches shorter than this at junctions
    join_angle: float = 50.0     # max bend to continue a stroke through a junction (degrees)
    smooth_join: float = 15.0    # keep the previous heading when the next arc starts within this angle
    corner_angle: float = 10.0   # heading changes above this count as visible corners in the stats
    smooth: int = 2              # moving-average half window on the traced pixels
    start: tuple[float, float] = (0.0, 512.0)  # pen position before the first stroke (top left)


@dataclass
class Segment:
    kind: str                    # "line" or "arc"
    heading: float               # absolute heading at the start of the segment
    length: float = 0.0          # line
    radius: float = 0.0          # arc
    angle: float = 0.0           # arc sweep, positive = left turn
    points: list = field(default_factory=list, repr=False)  # sampled path, for rendering

    def to_json(self) -> dict:
        if self.kind == "line":
            return {"type": "line", "heading": self.heading, "length": self.length}
        return {"type": "arc", "heading": self.heading, "radius": self.radius, "angle": self.angle}


@dataclass
class Stroke:
    start: tuple[float, float]
    segments: list[Segment]
    target: np.ndarray = field(repr=False)  # the traced polyline this stroke approximates
    closed: bool = False


@dataclass
class TraceResult:
    strokes: list[Stroke]
    config: TraceConfig
    image: np.ndarray = field(repr=False)          # cropped grayscale source
    to_canvas: tuple[float, float, float] = (1, 0, 0)  # scale, x offset, y offset

    def to_json(self) -> dict:
        return {
            "canvas": self.config.canvas,
            "angle_step": self.config.angle_step,
            "length_step": self.config.length_step,
            "strokes": [{"start": list(s.start), "closed": s.closed,
                         "segments": [seg.to_json() for seg in s.segments]} for s in self.strokes],
        }


# --------------------------------------------------------------------------- 1-2 image -> skeleton

def load_ink(path: str, cfg: TraceConfig) -> tuple[np.ndarray, np.ndarray]:
    gray = np.asarray(Image.open(path).convert("L"))
    ink = gray < cfg.threshold
    rows, cols = np.nonzero(ink)
    if not len(rows):
        raise ValueError("no ink found in the image")
    pad = 2
    r0, r1 = max(rows.min() - pad, 0), min(rows.max() + pad + 1, ink.shape[0])
    c0, c1 = max(cols.min() - pad, 0), min(cols.max() + pad + 1, ink.shape[1])
    return ink[r0:r1, c0:c1], gray[r0:r1, c0:c1]


def thin(ink: np.ndarray) -> np.ndarray:
    """Zhang-Suen thinning."""
    img = ink.astype(np.uint8)
    while True:
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1)
            p2, p3, p4 = P[:-2, 1:-1], P[:-2, 2:], P[1:-1, 2:]
            p5, p6, p7 = P[2:, 2:], P[2:, 1:-1], P[2:, :-2]
            p8, p9 = P[1:-1, :-2], P[:-2, :-2]
            ring = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = sum(ring[:8])
            a = sum(((ring[i] == 0) & (ring[i + 1] == 1)) for i in range(8))
            if step == 0:
                c = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                c = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            remove = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & c
            if remove.any():
                img[remove] = 0
                changed = True
        if not changed:
            return img.astype(bool)


def remove_staircases(skel: np.ndarray) -> set[tuple[int, int]]:
    """Delete pixels whose neighbours stay connected without them (8-connected minimal skeleton).

    Zhang-Suen leaves L-shaped corners on diagonals; those pixels have three neighbours and would
    otherwise look like junctions.
    """
    pixels = set(zip(*map(np.ndarray.tolist, np.nonzero(skel))))
    for p in sorted(pixels):
        nb = [(p[0] + dr, p[1] + dc) for dr, dc in NEIGHBOURS if (p[0] + dr, p[1] + dc) in pixels]
        if len(nb) < 2 or len(nb) > 4:
            continue
        if _connected(nb):
            pixels.discard(p)
    return pixels


def _connected(points: list[tuple[int, int]]) -> bool:
    seen, todo = {points[0]}, [points[0]]
    while todo:
        q = todo.pop()
        for r in points:
            if r not in seen and max(abs(q[0] - r[0]), abs(q[1] - r[1])) == 1:
                seen.add(r)
                todo.append(r)
    return len(seen) == len(points)


# --------------------------------------------------------------------------- 3 skeleton -> graph

@dataclass
class Edge:
    path: list[tuple[int, int]]
    a: int  # node id at path[0]
    b: int  # node id at path[-1]


def build_graph(pixels: set[tuple[int, int]]) -> tuple[list[Edge], list[list[tuple[int, int]]]]:
    """Split the skeleton into edges between nodes (endpoints / junction clusters).

    Returns the edges and the pure loops (closed curves without any node).
    """
    def nbrs(p):
        return [(p[0] + dr, p[1] + dc) for dr, dc in NEIGHBOURS if (p[0] + dr, p[1] + dc) in pixels]

    degree = {p: len(nbrs(p)) for p in pixels}
    node_of: dict[tuple[int, int], int] = {}
    next_id = 0
    for p in pixels:
        if degree[p] == 2 or p in node_of:
            continue
        if degree[p] == 1 or degree[p] == 0:
            node_of[p] = next_id
        else:  # flood-fill adjacent junction pixels into one node
            todo = [p]
            node_of[p] = next_id
            while todo:
                q = todo.pop()
                for r in nbrs(q):
                    if degree[r] >= 3 and r not in node_of:
                        node_of[r] = next_id
                        todo.append(r)
        next_id += 1

    used: set[frozenset] = set()
    visited: set[tuple[int, int]] = set(node_of)
    edges = []
    for n, nid in node_of.items():
        for q in nbrs(n):
            if node_of.get(q) == nid or frozenset((n, q)) in used:
                continue
            used.add(frozenset((n, q)))
            path, prev, cur = [n, q], n, q
            while cur not in node_of:
                visited.add(cur)
                nxt = [r for r in nbrs(cur) if r != prev and frozenset((cur, r)) not in used]
                if not nxt:
                    break
                used.add(frozenset((cur, nxt[0])))
                prev, cur = cur, nxt[0]
                path.append(cur)
            if cur in node_of:
                edges.append(Edge(path, nid, node_of[cur]))

    loops = []
    for p in pixels - visited:
        if p in visited:
            continue
        path, prev, cur = [p], None, p
        visited.add(p)
        while True:
            nxt = [r for r in nbrs(cur) if r != prev and r not in visited]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            visited.add(cur)
            path.append(cur)
        if len(path) > 2:
            loops.append(path + [p])
    return edges, loops


def prune_spurs(edges: list[Edge], max_len: float) -> list[Edge]:
    """Remove short dead-end branches that hang off junctions (skeleton noise)."""
    while True:
        deg: dict[int, int] = {}
        for e in edges:
            deg[e.a] = deg.get(e.a, 0) + 1
            deg[e.b] = deg.get(e.b, 0) + 1
        keep = [e for e in edges
                if not (len(e.path) < max_len and min(deg[e.a], deg[e.b]) == 1 and max(deg[e.a], deg[e.b]) >= 3)]
        if len(keep) == len(edges):
            return keep
        edges = keep


# --------------------------------------------------------------------------- 4 graph -> strokes

def join_strokes(edges: list[Edge], loops, join_angle: float, probe: int = 10):
    """Chain edges into strokes, continuing through junctions where the line goes on straight."""
    ends_at: dict[int, list[tuple[int, int]]] = {}
    for i, e in enumerate(edges):
        ends_at.setdefault(e.a, []).append((i, 0))
        ends_at.setdefault(e.b, []).append((i, 1))

    def direction(i, end):
        path = edges[i].path if end == 0 else edges[i].path[::-1]
        q = path[min(probe, len(path) - 1)]
        v = np.array([q[0] - path[0][0], q[1] - path[0][1]], float)
        return v / (np.linalg.norm(v) or 1)

    pair: dict[tuple[int, int], tuple[int, int]] = {}
    for node, ends in ends_at.items():
        if len(ends) == 2:
            pair[ends[0]], pair[ends[1]] = ends[1], ends[0]
            continue
        candidates = []
        for x in range(len(ends)):
            for y in range(x + 1, len(ends)):
                bend = math.degrees(math.acos(np.clip(-direction(*ends[x]) @ direction(*ends[y]), -1, 1)))
                if bend <= join_angle:
                    candidates.append((bend, ends[x], ends[y]))
        for _, u, v in sorted(candidates):
            if u not in pair and v not in pair:
                pair[u], pair[v] = v, u

    visited: set[int] = set()

    def walk(i, end):
        pts, cur, cur_end = [], i, end
        while True:
            visited.add(cur)
            path = edges[cur].path if cur_end == 0 else edges[cur].path[::-1]
            pts.extend(path if not pts else path[1:])
            nxt = pair.get((cur, 1 - cur_end))
            if nxt is None:
                return pts, False
            if nxt[0] in visited:
                return pts, nxt == (i, end)
            cur, cur_end = nxt

    strokes = []
    for i, e in enumerate(edges):
        for end in (0, 1):
            if i not in visited and (i, end) not in pair:
                strokes.append(walk(i, end))
    for i in range(len(edges)):
        if i not in visited:
            strokes.append(walk(i, 0))
    strokes += [(loop, True) for loop in loops]
    return strokes


# --------------------------------------------------------------------------- 5 ordering

def order_strokes(strokes: list[tuple[np.ndarray, bool]], start) -> list[tuple[np.ndarray, bool]]:
    """Greedy nearest neighbour (choosing direction / entry point), then 2-opt on the travel."""
    remaining = list(strokes)
    pen = np.asarray(start, float)
    ordered = []
    while remaining:
        best = None
        for k, (pts, closed) in enumerate(remaining):
            if closed:
                d = np.linalg.norm(pts[:-1] - pen, axis=1)
                j = int(d.argmin())
                cand = (d[j], k, np.vstack([pts[j:-1], pts[:j + 1]]))
            else:
                d0, d1 = np.linalg.norm(pts[0] - pen), np.linalg.norm(pts[-1] - pen)
                cand = (d0, k, pts) if d0 <= d1 else (d1, k, pts[::-1])
            if best is None or cand[0] < best[0]:
                best = cand
        _, k, pts = best
        ordered.append((pts, remaining.pop(k)[1]))
        pen = pts[-1]

    def travel(seq):
        pos, total = np.asarray(start, float), 0.0
        for pts, _ in seq:
            total += np.linalg.norm(pts[0] - pos)
            pos = pts[-1]
        return total

    best_cost, improved = travel(ordered), True
    while improved:
        improved = False
        for i in range(len(ordered) - 1):
            for j in range(i + 1, len(ordered)):
                cand = ordered[:i] + [(p[::-1], c) for p, c in ordered[i:j + 1][::-1]] + ordered[j + 1:]
                cost = travel(cand)
                if cost < best_cost - 1e-6:
                    ordered, best_cost, improved = cand, cost, True
    return ordered


# --------------------------------------------------------------------------- 6-7 fitting

def _circle(a, m, b):
    """Circle through three points, or None if (nearly) collinear."""
    d = 2 * (a[0] * (m[1] - b[1]) + m[0] * (b[1] - a[1]) + b[0] * (a[1] - m[1]))
    if abs(d) < 1e-9:
        return None
    sa, sm, sb = a @ a, m @ m, b @ b
    cx = (sa * (m[1] - b[1]) + sm * (b[1] - a[1]) + sb * (a[1] - m[1])) / d
    cy = (sa * (b[0] - m[0]) + sm * (a[0] - b[0]) + sb * (m[0] - a[0])) / d
    c = np.array([cx, cy])
    return c, float(np.linalg.norm(a - c))


def _signed_angle(u, v):
    return math.atan2(u[0] * v[1] - u[1] * v[0], u @ v)


def _fits_line(pts, tol):
    a, b = pts[0], pts[-1]
    ab = b - a
    n = np.linalg.norm(ab)
    if n < 1e-9:
        return False
    dist = np.abs((pts[:, 0] - a[0]) * ab[1] - (pts[:, 1] - a[1]) * ab[0]) / n
    return dist.max() <= tol


def _fit_arc(pts, tol, min_radius):
    """Return (center, radius, sweep) if pts lie on one circular arc, else None."""
    if len(pts) < 5:
        return None
    a, m, b = pts[0], pts[len(pts) // 2], pts[-1]
    circ = _circle(a, m, b)
    if circ is None:
        return None
    c, r = circ
    if r < min_radius or r > 1e4:
        return None
    if np.abs(np.linalg.norm(pts - c, axis=1) - r).max() > tol:
        return None
    sweep = _signed_angle(a - c, m - c) + _signed_angle(m - c, b - c)
    # all points must advance monotonically along the arc
    angles = np.unwrap(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))
    steps = np.diff(angles) * np.sign(sweep)
    if (steps < -1e-3).any() or abs(sweep) > math.radians(300):
        return None
    return c, r, sweep


def fit_pieces(pts: np.ndarray, cfg: TraceConfig) -> list[tuple]:
    """Greedily split a polyline into the longest possible line / arc pieces."""
    pieces, i, n = [], 0, len(pts)
    while i < n - 1:
        j_line = i + 1
        while j_line + 1 < n and _fits_line(pts[i:j_line + 2], cfg.tolerance):
            j_line += 1
        j_arc, arc = i, None
        j = max(j_line, i + 4)
        while j < n:
            fit = _fit_arc(pts[i:j + 1], cfg.tolerance, cfg.min_radius)
            if fit is None:
                if j > j_line + 6:  # give up a bit after the last good arc
                    break
            else:
                j_arc, arc = j, fit
            j += 1
        if arc is not None and j_arc > j_line + 2:
            pieces.append(("arc", pts[i:j_arc + 1], arc))
            i = j_arc
        else:
            pieces.append(("line", pts[i:j_line + 1], None))
            i = j_line
    return pieces


def fit_full_circle(pts: np.ndarray, tol: float):
    """Least-squares (Kasa) circle for a closed stroke; (center, radius, ccw) or None."""
    x, y = pts[:-1, 0], pts[:-1, 1]
    a = np.column_stack([x, y, np.ones_like(x)])
    sol, *_ = np.linalg.lstsq(a, -(x ** 2 + y ** 2), rcond=None)
    c = np.array([-sol[0] / 2, -sol[1] / 2])
    r2 = c @ c - sol[2]
    if r2 <= 0:
        return None
    r = math.sqrt(r2)
    if np.abs(np.linalg.norm(pts - c, axis=1) - r).max() > tol:
        return None
    area = np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)
    return c, r, area > 0


def q(value, step):
    return round(value / step) * step


def arc_points(p, heading, radius, angle, n=None):
    a0, dtheta = math.radians(heading), math.radians(angle)
    sign = 1 if dtheta > 0 else -1
    cx = p[0] - sign * radius * math.sin(a0)
    cy = p[1] + sign * radius * math.cos(a0)
    n = n or max(4, int(abs(angle) / 5) + 1)
    return [(cx + sign * radius * math.sin(a0 + dtheta * k / n),
             cy - sign * radius * math.cos(a0 + dtheta * k / n)) for k in range(n + 1)]


def angle_diff(a: float, b: float) -> float:
    """Signed difference a - b in degrees, in (-180, 180]."""
    return (a - b + 180) % 360 - 180


def quantize_stroke(pts: np.ndarray, closed: bool, cfg: TraceConfig) -> Stroke:
    start = (q(pts[0][0], cfg.length_step), q(pts[0][1], cfg.length_step))
    pen = np.array(start, float)
    heading_now = None  # pen heading after the previous segment
    segments = []
    circle = fit_full_circle(pts, cfg.tolerance) if closed else None
    if circle is not None:
        c, r, ccw = circle
        # start where the tangent lies exactly on the heading grid, so quantizing the heading
        # does not shift the whole circle
        radial = q(math.degrees(math.atan2(pts[0][1] - c[1], pts[0][0] - c[0])), cfg.angle_step)
        on_circle = c + r * np.array([math.cos(math.radians(radial)), math.sin(math.radians(radial))])
        start = (q(on_circle[0], cfg.length_step), q(on_circle[1], cfg.length_step))
        seg = _arc_from_center(np.array(start), c, r, 360.0 if ccw else -360.0, cfg)
        if seg is not None:
            return Stroke(start, [seg], pts, closed)

    for kind, piece, arc in fit_pieces(pts, cfg):
        end = piece[-1]
        if np.linalg.norm(end - pen) < cfg.length_step / 2:
            continue
        if kind == "arc":
            seg = _best_arc(pen, piece, arc, heading_now, cfg)
            if seg is not None:
                segments.append(seg)
                pen, heading_now = np.array(seg.points[-1]), (seg.heading + seg.angle) % 360
                continue
        for seg in _lines_to(pen, end, cfg):
            segments.append(seg)
            pen, heading_now = np.array(seg.points[-1]), seg.heading
    return Stroke(start, segments, pts, closed)


def _line(pen, heading, length) -> Segment:
    new = pen + length * np.array([math.cos(math.radians(heading)), math.sin(math.radians(heading))])
    return Segment("line", heading % 360, length=length, points=[tuple(pen), tuple(new)])


def _lines_to(pen, end, cfg: TraceConfig) -> list[Segment]:
    """Straight line(s) to the target.

    A quantized heading misses a far target sideways by dist * sin(error). If that exceeds the
    tolerance, zigzag between the two neighbouring grid headings: lengths are solved so that each
    pair lands back on the true line, and enough pairs are used to stay within the tolerance.
    """
    chord = end - pen
    dist = float(np.linalg.norm(chord))
    if dist < cfg.length_step / 2:
        return []
    chord_dir = math.degrees(math.atan2(chord[1], chord[0]))
    heading = q(chord_dir, cfg.angle_step)
    miss = dist * abs(math.sin(math.radians(angle_diff(heading, chord_dir))))
    if miss <= cfg.tolerance or dist < 4 * cfg.length_step:
        return [_line(pen, heading, q(dist, cfg.length_step))]

    lo = math.floor(chord_dir / cfg.angle_step) * cfg.angle_step
    hi = lo + cfg.angle_step
    sin_step = math.sin(math.radians(cfg.angle_step))

    def split(vec):  # lengths along lo / hi that add up to vec
        d = float(np.linalg.norm(vec))
        e = math.radians(angle_diff(math.degrees(math.atan2(vec[1], vec[0])), lo))
        return d * math.sin(math.radians(cfg.angle_step) - e) / sin_step, d * math.sin(e) / sin_step

    l_lo, _ = split(chord)
    sideways = l_lo * math.sin(math.radians(angle_diff(chord_dir, lo)))
    pairs = max(1, math.ceil(sideways / cfg.tolerance))
    out = []
    for k in range(pairs, 0, -1):
        l_lo, l_hi = split(end - pen)
        for h, length in ((lo, q(l_lo / k, cfg.length_step)), (hi, q(l_hi / k, cfg.length_step))):
            if length > 0:
                out.append(_line(pen, h, length))
                pen = np.array(out[-1].points[-1])
    return out


def _best_arc(pen, piece, arc, heading_now, cfg: TraceConfig) -> Segment | None:
    """Try several quantized arcs for a traced arc piece and keep the closest one.

    Candidates: the chord construction with the previous heading (smooth join), the chord
    construction with a freshly quantized heading, and the fitted circle itself.
    """
    end = piece[-1]
    chord = end - pen
    dist = float(np.linalg.norm(chord))
    chord_dir = math.degrees(math.atan2(chord[1], chord[0]))
    fitted_sweep = math.degrees(arc[2])
    wanted = chord_dir - fitted_sweep / 2  # tangent the traced arc starts with

    candidates = []
    if heading_now is not None and abs(angle_diff(wanted, heading_now)) <= cfg.smooth_join:
        candidates.append(_arc_by_chord(pen, chord_dir, dist, fitted_sweep, heading_now, cfg))
    candidates.append(_arc_by_chord(pen, chord_dir, dist, fitted_sweep, q(wanted, cfg.angle_step) % 360, cfg))
    candidates.append(_arc_from_center(pen, arc[0], arc[1], q(fitted_sweep, cfg.angle_step), cfg))

    best, best_err = None, math.inf
    for seg in candidates:
        if seg is None:
            continue
        err = max(_deviation(seg.points, piece), float(np.linalg.norm(np.array(seg.points[-1]) - end)))
        if err < best_err - 0.25:  # earlier candidates (smoother) win near-ties
            best, best_err = seg, err
    return best if best_err <= 3 * cfg.tolerance else None


def _deviation(path, target: np.ndarray) -> float:
    """Max distance of the target points to a densely sampled path."""
    p = np.array(path)
    dense = np.vstack([a + (b - a) * np.linspace(0, 1, max(2, int(np.linalg.norm(b - a) / 0.5)))[:, None]
                       for a, b in zip(p[:-1], p[1:])])
    d = np.linalg.norm(target[:, None, :] - dense[None, :, :], axis=2).min(axis=1)
    return float(d.max())


def _arc_from_center(pen, center, radius, sweep, cfg: TraceConfig) -> Segment | None:
    """Arc around a known center, starting at the pen with the tangent there."""
    if sweep == 0:
        return None
    radial = math.degrees(math.atan2(pen[1] - center[1], pen[0] - center[0]))
    heading = q(radial + (90 if sweep > 0 else -90), cfg.angle_step) % 360
    radius = q(radius, cfg.length_step)
    if radius < cfg.min_radius:
        return None
    return Segment("arc", heading, radius=radius, angle=sweep, points=arc_points(pen, heading, radius, sweep))


def _arc_by_chord(pen, chord_dir, dist, fitted_sweep, heading, cfg: TraceConfig) -> Segment | None:
    """Arc starting with `heading` whose sweep is chosen so that it ends on the target:
    for a circular arc the chord direction is start heading + sweep / 2."""
    sweep = q(2 * angle_diff(chord_dir, heading), cfg.angle_step)
    # the chord-derived sweep must still describe the traced arc, otherwise draw a line instead
    if sweep == 0 or abs(sweep - fitted_sweep) > max(15.0, 0.3 * abs(fitted_sweep)):
        return None
    radius = q(dist / (2 * abs(math.sin(math.radians(sweep) / 2))), cfg.length_step)
    if radius < cfg.min_radius:
        return None
    return Segment("arc", heading, radius=radius, angle=sweep, points=arc_points(pen, heading, radius, sweep))


# --------------------------------------------------------------------------- driver

def smooth(pts: np.ndarray, k: int, closed: bool) -> np.ndarray:
    if k <= 0 or len(pts) < 2 * k + 2:
        return pts
    kernel = np.ones(2 * k + 1) / (2 * k + 1)
    if closed:
        core = pts[:-1]
        ext = np.vstack([core[-k:], core, core[:k]])
        out = np.column_stack([np.convolve(ext[:, d], kernel, "valid") for d in (0, 1)])
        return np.vstack([out, out[:1]])
    out = pts.copy()
    out[k:-k] = np.column_stack([np.convolve(pts[:, d], kernel, "valid") for d in (0, 1)])
    return out


def trace(path: str, cfg: TraceConfig | None = None) -> TraceResult:
    cfg = cfg or TraceConfig()
    ink, gray = load_ink(path, cfg)
    h, w = ink.shape
    scale = (cfg.canvas - 2 * cfg.margin) / max(h, w)
    ox = (cfg.canvas - w * scale) / 2
    oy = (cfg.canvas - h * scale) / 2

    def to_canvas(path_px):
        arr = np.array(path_px, float)
        return np.column_stack([ox + arr[:, 1] * scale, cfg.canvas - (oy + arr[:, 0] * scale)])

    pixels = remove_staircases(thin(ink))
    edges, loops = build_graph(pixels)
    edges = prune_spurs(edges, cfg.spur_length / scale)
    raw = join_strokes(edges, loops, cfg.join_angle)

    strokes = []
    for path_px, closed in raw:
        pts = smooth(to_canvas(path_px), cfg.smooth, closed)
        if np.linalg.norm(np.diff(pts, axis=0), axis=1).sum() >= cfg.min_stroke:
            strokes.append((pts, closed))

    ordered = order_strokes(strokes, cfg.start)
    result = [quantize_stroke(pts, closed, cfg) for pts, closed in ordered]
    return TraceResult([s for s in result if s.segments], cfg, gray, (scale, ox, oy))


def stats(result: TraceResult) -> dict:
    from scipy.spatial import cKDTree

    segs = [seg for s in result.strokes for seg in s.segments]
    drawn = np.vstack([np.array(seg.points) for seg in segs]) if segs else np.zeros((1, 2))
    target = np.vstack([s.target for s in result.strokes])
    # densify the drawn path for the deviation measure
    dense = []
    for seg in segs:
        p = np.array(seg.points)
        for a, b in zip(p[:-1], p[1:]):
            k = max(1, int(np.linalg.norm(b - a) / 0.5))
            dense.append(a + (b - a) * np.linspace(0, 1, k + 1)[:, None])
    dist, _ = cKDTree(np.vstack(dense) if dense else drawn).query(target)

    pen, travel, corners = np.asarray(result.config.start, float), 0.0, 0
    for s in result.strokes:
        travel += np.linalg.norm(np.asarray(s.start) - pen)
        heading = None
        for seg in s.segments:
            if heading is not None and abs(angle_diff(seg.heading, heading)) > result.config.corner_angle:
                corners += 1
            heading = seg.heading + (seg.angle if seg.kind == "arc" else 0)
        pen = np.array(s.segments[-1].points[-1])
    draw_len = sum(seg.length if seg.kind == "line" else abs(math.radians(seg.angle)) * seg.radius
                   for seg in segs)
    return {
        "strokes": len(result.strokes),
        "primitives": len(segs),
        "lines": sum(seg.kind == "line" for seg in segs),
        "arcs": sum(seg.kind == "arc" for seg in segs),
        "corners": corners,
        "pen_lifts": max(0, len(result.strokes) - 1),
        "draw_length": round(draw_len),
        "travel_length": round(travel),
        "deviation_mean": round(float(dist.mean()), 2),
        "deviation_max": round(float(dist.max()), 2),
    }
