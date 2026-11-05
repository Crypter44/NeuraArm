import math
import unittest

from pdraw import ActionError, COMPASS, Drawing


def close(a, b, tol=1e-6):
    return math.isclose(a[0], b[0], abs_tol=tol) and math.isclose(a[1], b[1], abs_tol=tol)


class PrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.d = Drawing()  # pen at center (256, 256), heading N

    def test_line_moves_along_heading(self):
        self.d.apply({"type": "line", "length": "M"})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (256, 306)))
        self.assertEqual(COMPASS[self.d.pen.heading], "N")

    def test_quarter_curve_left(self):
        self.d.apply({"type": "curve", "turn": "left", "angle": 90, "length": "M"})
        r = 50 / (math.pi / 2)
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (256 - r, 256 + r)))
        self.assertEqual(COMPASS[self.d.pen.heading], "W")

    def test_quarter_curve_right(self):
        self.d.apply({"type": "curve", "turn": "right", "angle": 90, "length": "M"})
        r = 50 / (math.pi / 2)
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (256 + r, 256 + r)))
        self.assertEqual(COMPASS[self.d.pen.heading], "E")

    def test_full_circle_returns_to_start(self):
        self.d.apply({"type": "curve", "turn": "left", "angle": 360, "length": "L"})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (256, 256)))
        self.assertEqual(COMPASS[self.d.pen.heading], "N")

    def test_corner_turns_then_draws(self):
        self.d.apply({"type": "corner", "turn": "right", "angle": 90, "length": "S"})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (281, 256)))
        self.assertEqual(COMPASS[self.d.pen.heading], "E")

    def test_pen_lifts_only_count_real_jumps(self):
        self.d.apply({"type": "line", "label": "a"})
        self.d.apply({"type": "curve", "turn": "left", "angle": 360})  # back at a
        self.d.apply({"from": "a", "heading": "E", "type": "line"})    # no lift
        self.d.apply({"from": "center", "type": "line"})                # lift
        self.assertEqual(self.d.pen_lifts(), 1)

    def test_move_repositions_without_drawing(self):
        self.d.apply({"type": "line"})
        self.d.apply({"type": "move", "heading": "E", "length": "L"})
        self.d.apply({"type": "line", "heading": "N"})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (356, 356)))
        self.assertEqual(self.d.pen_lifts(), 1)
        self.assertFalse(self.d.steps[1].drawn)

    def test_jump_and_numeric_lengths(self):
        self.d.apply({"type": "jump", "x": 100, "y": 200, "heading": "E"})
        self.d.apply({"type": "line", "length": 35})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (135, 200)))
        self.assertEqual(sum(s.drawn for s in self.d.steps), 1)

    def test_circle_by_radius(self):
        self.d.apply({"type": "curve", "heading": "E", "turn": "left", "angle": 180, "radius": 20})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (256, 296)))

    def test_feedback_reports_position_and_expectation(self):
        log = self.d.apply_all([
            {"type": "line", "length": 50, "expect": [256, 306]},
            {"type": "line", "heading": "E", "length": 50, "expect": [256, 356]},
        ])
        self.assertIn("pen now at (256, 306) heading N (as expected)", log[0])
        self.assertIn("off by 71", log[1])

    def test_from_label_with_heading(self):
        self.d.apply({"type": "line", "length": "L", "label": "top"})
        self.d.apply({"type": "line", "length": "S"})
        self.d.apply({"from": "top", "heading": "E", "type": "line", "length": "S"})
        self.assertTrue(close((self.d.pen.x, self.d.pen.y), (281, 356)))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.d = Drawing()

    def test_duplicate_label(self):
        self.d.apply({"type": "line", "label": "a"})
        with self.assertRaises(ActionError):
            self.d.apply({"type": "line", "label": "a"})

    def test_start_points_are_reserved(self):
        with self.assertRaises(ActionError):
            self.d.apply({"type": "line", "label": "center"})

    def test_unknown_label(self):
        with self.assertRaisesRegex(ActionError, "known labels"):
            self.d.apply({"from": "nope", "type": "line"})

    def test_off_grid_angle(self):
        with self.assertRaises(ActionError):
            self.d.apply({"type": "curve", "turn": "left", "angle": 30})

    def test_failed_action_changes_nothing(self):
        with self.assertRaises(ActionError):
            self.d.apply({"type": "line", "length": 7, "label": "x"})
        self.assertEqual(self.d.steps, [])
        self.assertNotIn("x", self.d.labels)

    def test_apply_all_stops_at_error(self):
        log = self.d.apply_all([{"type": "line"}, {"type": "zigzag"}, {"type": "line"}])
        self.assertEqual(len(log), 2)
        self.assertIn("ERROR", log[1])
        self.assertEqual(len(self.d.steps), 1)

    def test_undo_restores_pen_and_labels(self):
        self.d.apply({"type": "line", "label": "a"})
        before = self.d.pen
        self.d.apply({"type": "curve", "turn": "left", "angle": 45, "label": "b"})
        self.d.apply({"type": "undo"})
        self.assertEqual(self.d.pen, before)
        self.assertNotIn("b", self.d.labels)
        self.assertIn("a", self.d.labels)

    def test_out_of_canvas_warns(self):
        warnings = self.d.apply({"from": "top_left", "heading": "W", "type": "line", "length": "L"})
        self.assertEqual(warnings, ["primitive leaves the canvas"])

    def test_live_mode_rejects_off_paper_and_undo(self):
        d = Drawing(strict_bounds=True, allow_undo=False)
        d.apply({"type": "line"})
        with self.assertRaisesRegex(ActionError, "leave the paper"):
            d.apply({"from": "top_left", "heading": "W", "type": "line", "length": "L"})
        with self.assertRaisesRegex(ActionError, "undo"):
            d.apply({"type": "undo"})
        self.assertEqual(len(d.steps), 1)


if __name__ == "__main__":
    unittest.main()
