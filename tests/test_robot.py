import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from pdraw.robot import PaperFrame, plan, plane_coords, plane_from_points, strokes_from_json
from pdraw.trace import TraceConfig, trace
from tests.test_trace import draw_sample


class RobotPlanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shapes.png"
            draw_sample(path)
            cls.result = trace(str(path), TraceConfig())
        cls.data = json.loads(json.dumps(cls.result.to_json()))

    def test_json_replay_matches_the_traced_segments(self):
        replayed = strokes_from_json(self.data)
        for pts, stroke in zip(replayed, self.result.strokes):
            for seg_pts, seg in zip(pts, stroke.segments):
                np.testing.assert_allclose(seg_pts[0], seg.points[0], atol=1e-6)
                np.testing.assert_allclose(seg_pts[-1], seg.points[-1], atol=1e-6)

    def test_drawing_stays_on_the_paper_and_inside_the_field(self):
        frame = PaperFrame([0.4, 0.1, 0.2, 3.14, 0.0, 1.0], size=0.1)
        moves = plan(strokes_from_json(self.data), frame, lift=0.02)
        for m in moves:
            for p in m.poses:
                self.assertEqual(p[3:], [3.14, 0.0, 1.0])
                self.assertLessEqual(abs(p[0] - 0.4), 0.05 + 1e-9)
                self.assertLessEqual(abs(p[1] - 0.1), 0.05 + 1e-9)
                if m.kind == "draw":
                    self.assertAlmostEqual(p[2], 0.2)
        self.assertAlmostEqual(moves[-1].poses[-1][2], 0.22)


class PlaneTests(unittest.TestCase):
    def setUp(self):
        # paper tilted by ~3° around the base x axis, centre at (0.4, 0.1, 0.2)
        self.c = np.array([0.4, 0.1, 0.2])
        self.ex = np.array([1.0, 0.0, 0.0])
        self.ey = np.array([0.0, np.cos(0.05), np.sin(0.05)])
        self.n = np.cross(self.ex, self.ey)
        rpy = [3.14, 0.0, 1.0]
        self.touched = [list(self.c) + rpy, list(self.c + 0.05 * self.ex) + rpy,
                        list(self.c + 0.03 * self.ex + 0.04 * self.ey) + rpy, list(self.c + 0.03 * self.n) + rpy]

    def test_three_points_give_the_tilted_plane(self):
        paper = plane_from_points(*self.touched)
        np.testing.assert_allclose(paper["normal"], self.n, atol=1e-9)
        np.testing.assert_allclose(paper["y_axis"], self.ey, atol=1e-9)
        self.assertEqual(paper["origin"], self.touched[0])

    def test_third_point_on_the_other_side_does_not_mirror_the_drawing(self):
        c, r, t, a = self.touched
        t = list(self.c + 0.03 * self.ex - 0.04 * self.ey) + t[3:]
        paper = plane_from_points(c, r, t, a)
        np.testing.assert_allclose(paper["normal"], self.n, atol=1e-9)
        np.testing.assert_allclose(paper["y_axis"], self.ey, atol=1e-9)

    def test_drawing_poses_lie_on_the_plane(self):
        paper = plane_from_points(*self.touched)
        frame = PaperFrame(paper["origin"], 0.1, x_axis=paper["x_axis"], y_axis=paper["y_axis"],
                           normal=paper["normal"])
        for xy, lift in [((0.02, -0.03), 0.0), ((-0.04, 0.01), 0.0), ((0.01, 0.01), 0.02)]:
            x, y, h = plane_coords(paper, frame.pose(xy, lift))
            self.assertAlmostEqual(x, xy[0])
            self.assertAlmostEqual(y, xy[1])
            self.assertAlmostEqual(h, lift)

    def test_points_on_a_line_are_rejected(self):
        c, r, _, a = self.touched
        with self.assertRaises(ValueError):
            plane_from_points(c, r, list(self.c + 0.1 * self.ex) + c[3:], a)


if __name__ == "__main__":
    unittest.main()
