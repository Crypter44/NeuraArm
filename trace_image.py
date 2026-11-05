"""Trace a line-art image into ordered robot primitives and visualise the result.

    python3 trace_image.py samples/car.png [--angle-step 5] [--tolerance 1.5] [--out out/car]

Writes <out>.json (the primitives, in drawing order) and <out>.png (source next to the result,
primitives coloured by drawing order, pen-up travel dashed).
"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

from pdraw.trace import TraceConfig, stats, trace


def visualise(result, info, out_png: Path, title: str):
    cfg = result.config
    segs = [seg for s in result.strokes for seg in s.segments]
    cmap = plt.get_cmap("turbo")

    fig, (ax_src, ax) = plt.subplots(1, 2, figsize=(16, 8.6), gridspec_kw={"wspace": 0.04})
    scale, ox, oy = result.to_canvas
    h, w = result.image.shape
    ax_src.imshow(result.image, cmap="gray", vmin=0, vmax=255,
                  extent=(ox, ox + w * scale, cfg.canvas - oy - h * scale, cfg.canvas - oy))
    ax_src.set_title("Vorlage", fontsize=13)

    # pen-up travel
    pen = cfg.start
    for s in result.strokes:
        ax.plot([pen[0], s.start[0]], [pen[1], s.start[1]], ls=(0, (2, 3)), lw=0.8, color="0.65", zorder=1)
        pen = s.segments[-1].points[-1]

    # drawn primitives, coloured by order; each segment gets its own colour stop
    lines, colors = [], []
    for k, seg in enumerate(segs):
        lines.append(seg.points)
        colors.append(cmap(k / max(1, len(segs) - 1)))
    ax.add_collection(LineCollection(lines, colors=colors, linewidths=2.2, capstyle="round", zorder=2))

    # segment joints (small) and stroke starts (numbered)
    joints = [seg.points[0] for seg in segs]
    ax.scatter(*zip(*joints), s=4, color="black", zorder=3, linewidths=0)
    for n, s in enumerate(result.strokes, 1):
        ax.scatter(*s.start, s=22, color="white", edgecolors="black", linewidths=0.8, zorder=4)
        ax.annotate(str(n), s.start, xytext=(3, 3), textcoords="offset points", fontsize=7, zorder=5)

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(1, len(segs)))
    cbar = fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("Reihenfolge (Primitiv Nr.)")

    ax.set_title(
        f"{info['primitives']} Primitive ({info['lines']} Linien, {info['arcs']} Bögen) · "
        f"{info['strokes']} Striche · {info['pen_lifts']} Stiftanhebungen\n"
        f"Abweichung Ø {info['deviation_mean']} / max {info['deviation_max']} · "
        f"Leerfahrt {info['travel_length']} · Zeichenweg {info['draw_length']} · "
        f"Winkelraster {cfg.angle_step:g}°", fontsize=11)

    for a in (ax_src, ax):
        a.set_xlim(0, cfg.canvas)
        a.set_ylim(0, cfg.canvas)
        a.set_aspect("equal")
        a.set_xticks([])
        a.set_yticks([])
    fig.suptitle(title, fontsize=14)
    fig.savefig(out_png, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("--angle-step", type=float, default=TraceConfig.angle_step)
    ap.add_argument("--length-step", type=float, default=TraceConfig.length_step)
    ap.add_argument("--tolerance", type=float, default=TraceConfig.tolerance)
    ap.add_argument("--out", help="output path without extension (default: out/<image name>)")
    args = ap.parse_args()

    cfg = TraceConfig(angle_step=args.angle_step, length_step=args.length_step, tolerance=args.tolerance)
    result = trace(args.image, cfg)
    info = stats(result)

    out = Path(args.out or Path("out") / Path(args.image).stem)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(result.to_json(), indent=1))
    visualise(result, info, out.with_suffix(".png"), Path(args.image).name)

    print(json.dumps(info, indent=1))
    print(f"→ {out.with_suffix('.json')}, {out.with_suffix('.png')}")


if __name__ == "__main__":
    main()
