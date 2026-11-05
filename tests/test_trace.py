import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from pdraw.trace import TraceConfig, stats, trace


def draw_sample(path: Path) -> None:
    img = Image.new("L", (800, 800), 255)
    d = ImageDraw.Draw(img)
    d.ellipse([100, 100, 350, 350], outline=0, width=8)       # circle
    d.rectangle([450, 450, 700, 650], outline=0, width=8)     # rectangle
    d.line([100, 600, 350, 700], fill=0, width=8)             # slanted line
    img.save(path)


class TraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "shapes.png"
        draw_sample(path)
        cls.result = trace(str(path), TraceConfig())
        cls.info = stats(cls.result)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_one_stroke_per_shape(self):
        self.assertEqual(self.info["strokes"], 3)

    def test_circle_is_a_single_full_arc(self):
        full = [s for s in self.result.strokes
                if len(s.segments) == 1 and s.segments[0].kind == "arc" and abs(s.segments[0].angle) == 360]
        self.assertEqual(len(full), 1)

    def test_traced_path_stays_close_to_the_source(self):
        self.assertLess(self.info["deviation_mean"], 1.0)
        self.assertLess(self.info["deviation_max"], 3.0)

    def test_headings_are_quantized(self):
        step = self.result.config.angle_step
        for s in self.result.strokes:
            for seg in s.segments:
                self.assertAlmostEqual(seg.heading / step, round(seg.heading / step))
                self.assertAlmostEqual(seg.angle / step, round(seg.angle / step))

    def test_strokes_end_where_the_next_travel_starts(self):
        # every stroke is continuous: each segment starts where the previous one ended
        for s in self.result.strokes:
            for a, b in zip(s.segments, s.segments[1:]):
                self.assertLess(abs(a.points[-1][0] - b.points[0][0]) + abs(a.points[-1][1] - b.points[0][1]), 1e-6)


if __name__ == "__main__":
    unittest.main()
