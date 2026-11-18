"""Click evidence regression tests; no screen capture or input."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.pycache_prefix = str(Path.home() / ".cache/desk/test-pycache")
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desk_click", ROOT / "desk-click.py")
click = importlib.util.module_from_spec(spec)
spec.loader.exec_module(click)


class ClickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.path = self.state / "frame.png"
        self.frame = Image.new("RGB", (120, 80), (36, 36, 36))
        ImageDraw.Draw(self.frame).rectangle((40, 20, 80, 60), fill=(180, 190, 200))
        self.frame.save(self.path)
        self.region = [0, 0, 120, 80]
        self.window = "0,0,120,80\tFixture · test\t1:2"
        (self.state / "user-input").write_text("0")
        click.observe(self.state, self.path, self.region, self.window)
        self.token = click.prepare(self.state, [60, 40], self.window)

    def verify(self, frame):
        frame.save(self.path)
        click.verify_initial(self.token, self.path, self.region, None)

    def test_capture_noise_in_each_channel_is_allowed(self):
        for channel in range(3):
            for delta in (-3, -1, 1, 3):
                with self.subTest(channel=channel, delta=delta):
                    frame = self.frame.copy()
                    r, g, b = frame.split()
                    channels = [r, g, b]
                    channels[channel] = channels[channel].point(lambda value: value + delta)
                    self.verify(Image.merge("RGB", channels))

    def test_one_pixel_beyond_tolerance_is_refused(self):
        frame = self.frame.copy()
        frame.putpixel((60, 40), (184, 190, 200))
        with self.assertRaises(click.StaleClick):
            self.verify(frame)

    def test_one_pixel_button_movement_is_refused(self):
        frame = Image.new("RGB", self.frame.size, (36, 36, 36))
        ImageDraw.Draw(frame).rectangle((41, 20, 81, 60), fill=(180, 190, 200))
        with self.assertRaises(click.StaleClick):
            self.verify(frame)

    def test_changed_letter_is_refused(self):
        frame = self.frame.copy()
        ImageDraw.Draw(frame).line((55, 30, 55, 48), fill="black", width=1)
        with self.assertRaises(click.StaleClick):
            self.verify(frame)

    def test_image_dimensions_are_not_interchangeable(self):
        self.assertFalse(click.matching_pixels(Image.new("RGB", (10, 20)), Image.new("RGB", (20, 10))))

    def test_replacement_is_refused_even_when_color_difference_is_allowed(self):
        frame = self.frame.copy()
        frame.putpixel((60, 40), (181, 190, 200))
        frame.save(self.path)
        before = {"id": "original", "role": "button", "name": "OK", "box": [40, 20, 40, 40], "enabled": True}
        with self.assertRaises(click.StaleClick):
            click.finalize(self.state, self.token, self.path, self.region, before, {**before, "id": "replacement"})

    def test_user_input_still_invalidates_evidence(self):
        (self.state / "user-input").write_text("1")
        with self.assertRaises(click.StaleClick):
            click.prepare(self.state, [60, 40], self.window)
        with self.assertRaises(click.StaleClick):
            click.finalize(self.state, self.token, self.path, self.region, None, None)

    def test_different_window_is_refused(self):
        with self.assertRaises(click.StaleClick):
            click.prepare(self.state, [60, 40], self.window.replace("1:2", "1:3"))

    def test_caret_exception_uses_same_color_tolerance(self):
        frame = self.frame.copy()
        ImageDraw.Draw(frame).line((60, 30, 60, 48), fill="black")
        frame.putpixel((50, 40), (181, 190, 200))
        frame.save(self.path)
        click.verify_initial(self.token, self.path, self.region, {"caret": [60, 30, 1, 19]})
        frame.putpixel((50, 40), (184, 190, 200))
        frame.save(self.path)
        with self.assertRaises(click.StaleClick):
            click.verify_initial(self.token, self.path, self.region, {"caret": [60, 30, 1, 19]})


@unittest.skipUnless(sys.platform == "darwin", "Swift macOS comparator")
class SwiftComparatorTests(unittest.TestCase):
    def test_native_comparator_tolerates_noise_and_rejects_changed_pixels(self):
        # Compile the actual native comparator, without the helper's input entry point.
        source = (ROOT / "desk-helper.swift").read_text()
        function = source[source.index("func matchingClickPixels("):source.index("func equalClickPixels(")]
        tolerance = json.loads((ROOT / "widget.json").read_text())["clickPixelTolerance"]
        harness = '''
let t = Int(CommandLine.arguments[1])!
let old = Data([36, 36, 36, 255, 180, 190, 200, 255])
precondition(matchingClickPixels(old, old, tolerance: t))
precondition(matchingClickPixels(old, Data([39, 33, 37, 255, 177, 193, 199, 255]), tolerance: t))
precondition(!matchingClickPixels(old, Data([40, 36, 36, 255, 180, 190, 200, 255]), tolerance: t))
precondition(!matchingClickPixels(old, Data([180, 190, 200, 255, 36, 36, 36, 255]), tolerance: t))
precondition(!matchingClickPixels(old, Data([36, 36, 36, 255]), tolerance: t))
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "main.swift"
            path.write_text("import Foundation\n" + function + harness)
            exe = Path(temp) / "compare"
            subprocess.run(["swiftc", str(path), "-o", str(exe)], check=True, timeout=90, capture_output=True)
            subprocess.run([str(exe), str(tolerance)], check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
