"""The phone as an agent uses it: look at the screen, touch it, type, press keys, move a mouse.

This is the brain; the device (an ESP32-S3 board plugged into the phone, phone_kvm/device.py)
is its hands and eye. Input is sent as HID reports the device paces itself, so a gesture's
timing does not depend on the network between them.

Positions are pixels in the image `screenshot` returns, so what the agent sees is what it
points at. Three things tie an image to the inputs around it, all measured by `calibrate`:

  where   the screen's place in the camera's frame, so image pixels are touch positions
  pointer the mouse cursor's position in image pixels, tracked from the counts sent
  when    the delay between an input and the first captured frame that shows it; a screenshot
          never starts sooner than that after the last input, so it cannot show the screen
          from before the input
"""
import time

from . import hid, keymap, state, video
from .device import MAX_INPUT, Device

LATENCY = 0.4   # seconds from an input to a frame showing it, until `calibrate` measures it
STEP = 0.008  # seconds between touch reports while a finger moves (125 Hz)
# Android scales mouse movement by the cursor's speed, which it estimates from the movement
# of the last 100 ms. A report with no other movement that recently is scaled by a constant, so
# every move is one report after this long a pause (the margin is for a phone that reads late).
QUIET = 400
FAR = 32767  # the longest move one report carries


class Phone:
    def __init__(self):
        self.config = state.load("config")
        if not self.config.get("device"):
            raise RuntimeError("no device configured (run: phonectl setup --device HOST[:PORT])")
        self.device = Device(self.config["device"])
        self.session = state.load("session")   # what earlier commands left: input time, pointer
        self._reports, self._wait = [], 0.0

    # -- transport ---------------------------------------------------------------------
    def _send(self, interface, report):
        """Queue a report, to follow the one before it after whatever `_pause` asked for."""
        self._reports.append((self._wait, hid.INTERFACES[interface][0], report))
        self._wait = 0.0
        if sum(len(report) + 4 for _, _, report in self._reports) > MAX_INPUT:
            self._flush()

    def _pause(self, ms):
        self._wait += ms

    def _flush(self):
        """Have the device play the queued reports; returns once the phone has taken them all."""
        if self._reports:
            # Whole milliseconds, rounded along the running total so the gesture's length holds.
            total, sent, timed = 0.0, 0, []
            for wait, interface, report in self._reports:
                total += wait
                timed.append((min(round(total) - sent, 65535), interface, report))
                sent = round(total)
            self._reports = []
            self.device.input(timed)
            self.session["input_at"] = time.time()
        time.sleep(self._wait / 1000)
        self._wait = 0.0

    def finish(self):
        """Record this command's inputs for the commands that follow."""
        self._flush()
        state.save("session", self.session)

    # -- screen ------------------------------------------------------------------------
    def _screen(self, recalibrate=False):
        return video.screen(self.device, self.config, recalibrate)

    def _capture(self, settle_ms):
        """The screen once it shows the last input and has stopped changing (animations done), or
        after settle_ms: (image, time captured, whether it settled)."""
        self._flush()
        latency = state.load("calibration").get("latency_ms", LATENCY * 1000) / 1000
        time.sleep(max(0, self.session.get("input_at", 0) + latency - time.time()))
        deadline = time.monotonic() + settle_ms / 1000
        image, captured = self._screen(), time.time()
        settled = False
        while not settled and time.monotonic() < deadline:
            again, captured = self._screen(), time.time()
            settled = not video.changed(image, again)
            image = again
        return image, captured, settled

    def screenshot(self, path, settle_ms=2000):
        """Save the screen as it is after the last input. Says when it was captured, how long
        after the last input, whether it had stopped changing, and where the mouse cursor is."""
        image, captured, settled = self._capture(settle_ms)
        image.save(path)
        since = self.session.get("input_at")
        return {"path": str(path), "width": image.width, "height": image.height,
                "captured_at": round(captured, 3), "settled": settled,
                "ms_since_input": round((captured - since) * 1000) if since else None,
                "pointer": self.session.get("pointer")}

    def size(self):
        """The screenshot's size in pixels, which is the coordinate space for touches."""
        geometry = state.load("geometry")
        if not geometry:
            self._screen(recalibrate=True)
            geometry = state.load("geometry")
        return tuple(geometry["size"])

    def calibrate(self, what="all"):
        """Measure what ties an image to inputs.

        screen   where the screen is in the camera's frame. Needs a light screen that reaches all
                 four edges (Settings or a browser page in a light theme: `key meta+i`), because
                 a dark screen cannot be told from its bezel.
        pointer  how far the cursor moves per mouse count, and how long an input takes to show.
                 Needs a still screen that does not react to hovering, such as the home screen.
        """
        if what in ("all", "screen"):
            self._screen(recalibrate=True)
            state.save("calibration", {})
            self.session["pointer"] = None
        if what in ("all", "pointer"):
            self._latency_calibrate()   # first: measuring the cursor's travel relies on it
            self._mouse_calibrate()
        width, height = self.size()
        return {"width": width, "height": height, **state.load("geometry"), **state.load("calibration"),
                "pointer": self.session.get("pointer")}

    def frame(self, path):
        """Save the camera's whole frame, as it arrives: for aiming the camera."""
        image = video.frame(self.device)
        image.save(path)
        return {"path": str(path), "width": image.width, "height": image.height}

    def _point(self, x, y):
        width, height = self.size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"({x}, {y}) is outside the {width}x{height} screen")
        # Pixel centres, scaled to the touchscreen's range.
        return (round((x + 0.5) / width * hid.TOUCH_MAX), round((y + 0.5) / height * hid.TOUCH_MAX))

    # -- touch -------------------------------------------------------------------------
    def _gesture(self, paths, ms, hold_ms=0):
        """Move one finger along each path [(x, y), ...] at constant speed, all taking `ms`."""
        paths = [[self._point(*p) for p in path] for path in paths]

        def along(path, t):
            lengths = [((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(path, path[1:])]
            left = t * sum(lengths)
            for (a, b), length in zip(zip(path, path[1:]), lengths):
                if left <= length and length:
                    return round(a[0] + (b[0] - a[0]) * left / length), round(a[1] + (b[1] - a[1]) * left / length)
                left -= length
            return path[-1]

        steps = max(1, round(ms / 1000 / STEP))
        for i in range(steps + 1):
            self._send("touch", hid.touch_report([(cid, True, *along(path, i / steps))
                                                  for cid, path in enumerate(paths)]))
            self._pause(STEP * 1000)
        # Rest at the end so Android sees a stop, not a fling, when asked to hold.
        self._pause(hold_ms)
        self._send("touch", hid.touch_report([(cid, False, *path[-1]) for cid, path in enumerate(paths)]))
        self._send("touch", hid.touch_report([]))
        self._flush()

    def tap(self, x, y, hold_ms=60):
        point = self._point(x, y)
        self._send("touch", hid.touch_report([(0, True, *point)]))
        self._pause(hold_ms)
        self._send("touch", hid.touch_report([(0, False, *point)]))
        self._send("touch", hid.touch_report([]))
        self._flush()

    def swipe(self, points, ms=300, hold_ms=0):
        """Drag one finger through the points in order (two for a swipe, more for a pattern)."""
        self._gesture([points], ms, hold_ms)

    def pinch(self, x, y, start, end, ms=400):
        """Two fingers on a horizontal line through (x, y), from `start` to `end` pixels apart."""
        self._gesture([[(x - start / 2, y), (x - end / 2, y)],
                       [(x + start / 2, y), (x + end / 2, y)]], ms, hold_ms=100)

    # -- keyboard ----------------------------------------------------------------------
    def _stroke(self, modifiers, usage):
        self._send("keyboard", hid.keyboard_report(modifiers, [usage] if usage else []))
        self._send("keyboard", hid.keyboard_report())

    def type(self, text):
        unknown = sorted({c for c in text if c not in keymap.CHARS})
        if unknown:
            raise ValueError(f"cannot type {unknown!r}: a USB keyboard sends keys, not characters, "
                             "and these are not on a US layout")
        for c in text:
            usage, shift = keymap.CHARS[c]
            self._stroke(keymap.SHIFT if shift else 0, usage)
        self._flush()

    def key(self, name, hold_ms=0):
        """A named key (home, back, power, enter, ...) or a combination such as ctrl+a."""
        if name in hid.CONSUMER:
            self._send("consumer", hid.consumer_report(hid.CONSUMER[name]))
            self._pause(hold_ms)
            self._send("consumer", hid.consumer_report())
            self._flush()
            return
        modifiers, usage = keymap.combo(name)
        self._send("keyboard", hid.keyboard_report(modifiers, [usage] if usage else []))
        self._pause(hold_ms)
        self._send("keyboard", hid.keyboard_report())
        self._flush()

    # -- mouse -------------------------------------------------------------------------
    BUTTONS = {"left": 1, "right": 2, "middle": 4}

    def mouse_move(self, dx, dy, buttons=0):
        """Move the cursor by mouse counts: one report after a pause, which Android scales by a
        constant, so the distance on screen is proportional to the counts."""
        self._pause(QUIET)
        self._send("mouse", hid.mouse_report(buttons, dx, dy))
        self._flush()
        pointer, gain = self.session.get("pointer"), state.load("calibration").get("pixels_per_count")
        if pointer and gain:
            width, height = self.size()
            self.session["pointer"] = [round(min(max(pointer[0] + dx * gain[0], 0), width - 1)),
                                       round(min(max(pointer[1] + dy * gain[1], 0), height - 1))]
        else:
            self.session["pointer"] = None

    def _mouse_corner(self):
        """Run the cursor into the top-left corner, the one position known without looking."""
        self.mouse_move(-FAR, -FAR)
        self.session["pointer"] = None

    def _mouse_from_corner(self, counts_x, counts_y, calibration):
        """From the corner out by dead reckoning; the cursor's position is then known."""
        self._mouse_corner()
        self.mouse_move(counts_x, counts_y)
        (gx, gy), (ox, oy) = calibration["pixels_per_count"], calibration["offset"]
        self.session["pointer"] = [round(ox + counts_x * gx), round(oy + counts_y * gy)]

    def _mouse_calibrate(self):
        """Measure, by watching the screen, how many screenshot pixels one mouse count moves the
        cursor. The cursor is seen at several places down a diagonal and a line is fitted through
        them that ignores an odd one out (a place where something reacted to the cursor)."""
        from itertools import combinations
        from statistics import median

        from PIL import ImageChops, ImageFilter
        width, height = self.size()
        near, probe = 40, 100

        def view(counts):
            self._mouse_corner()
            self.mouse_move(counts, counts)
            return self._capture(3000)[0]

        origin = view(near)
        # Whatever changes on its own (the clock) is not the cursor: leave it out.
        still = ImageChops.difference(origin, view(near)).convert("L").point(lambda v: 0 if v > 32 else 255)
        still = still.filter(ImageFilter.MinFilter(9))

        def tips(other):
            """The cursor's tip in `origin` and in `other`, or None if `other` shows no second one."""
            change = ImageChops.difference(origin, other).convert("L").point(lambda v: 255 if v > 48 else 0)
            change = ImageChops.multiply(change, still)
            box = change.getbbox()
            if box is None:
                raise RuntimeError("the cursor is not visible on the captured screen")
            # The origin's cursor is the top-left of what changed; the other is down and right.
            middle = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
            far = change.crop((middle[0], middle[1], box[2], box[3])).getbbox()
            return far and ((box[0], box[1]), (middle[0] + far[0], middle[1] + far[1]))

        # A short move says roughly how far a count goes; the moves measured then span the screen.
        found = tips(view(near + probe))
        rough = found and max((found[1][axis] - found[0][axis]) / probe for axis in (0, 1))
        if not rough or rough <= 0.05:
            raise RuntimeError("could not see the cursor move; calibrate on a still screen")
        reach = 0.8 * min(width, height) / rough
        points = []   # (counts, tip x, tip y)
        for part in (0.3, 0.45, 0.6, 0.8, 1.0):
            counts = near + round(reach * part)
            found = tips(view(counts))
            if found:
                points += [(near, *found[0]), (counts, *found[1])]
        gain = [median((b[axis] - a[axis]) / (b[0] - a[0]) for a, b in combinations(points, 2) if a[0] != b[0])
                if len(points) > 2 else 0 for axis in (1, 2)]
        if min(gain) <= 0.05:
            raise RuntimeError("could not see the cursor move; calibrate on a still screen")
        offset = [median(point[axis] - point[0] * gain[axis - 1] for point in points) for axis in (1, 2)]
        state.save("calibration", {**state.load("calibration"), "pixels_per_count": gain, "offset": offset})
        self.session["pointer"] = [round(offset[0] + near * gain[0]), round(offset[1] + near * gain[1])]

    def _latency_calibrate(self):
        """Measure the camera's noise (how much a still screen differs from picture to picture)
        and the delay from an input to the first picture that shows all of it: take pictures
        while the cursor jumps, three times, and keep the longest."""
        calibration = state.load("calibration")
        stream = video.watch(self.device, self.config, seconds=60)
        still = [image for _, image in (next(stream) for _ in range(6))]
        calibration["noise"] = max(video.difference(a, b) for a, b in zip(still, still[1:]))
        state.save("calibration", calibration)   # `video.changed` reads the noise
        delays = []
        for jump in (60, -60, 60):
            for _ in range(8):   # let the last jump finish arriving
                _, before = next(stream)
            self.mouse_move(jump, jump)
            sent, shown, last = time.time(), None, before
            for taken, image in stream:
                if shown is None and video.changed(before, image):
                    shown = taken
                elif shown is not None and not video.changed(last, image):
                    break   # two pictures alike: `last` showed the whole jump
                if shown is not None:
                    shown = taken
                last = image
            else:
                raise RuntimeError("the captured screen did not change after moving the cursor")
            delays.append(shown - sent)
        stream.close()
        # A margin of a quarter and a tenth of a second, for a delay that varies.
        calibration["latency_ms"] = round(max(delays) * 1250) + 100
        state.save("calibration", calibration)

    def mouse_to(self, x, y):
        """Put the cursor's tip on a screenshot pixel: into the corner, then out by dead reckoning."""
        width, height = self.size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"({x}, {y}) is outside the {width}x{height} screen")
        calibration = state.load("calibration")
        if "pixels_per_count" not in calibration:
            raise RuntimeError("the mouse is not calibrated (run: phonectl calibrate)")
        (gx, gy), (ox, oy) = calibration["pixels_per_count"], calibration["offset"]
        self._mouse_from_corner(max(0, round((x - ox) / gx)), max(0, round((y - oy) / gy)), calibration)

    def mouse_click(self, button="left", hold_ms=40):
        self._send("mouse", hid.mouse_report(self.BUTTONS[button]))
        self._pause(hold_ms)
        self._send("mouse", hid.mouse_report())
        self._flush()

    def mouse_scroll(self, notches):
        """Positive scrolls the content down, as rolling a wheel towards you does."""
        step = -1 if notches > 0 else 1
        for _ in range(abs(notches)):
            self._send("mouse", hid.mouse_report(wheel=step))
            self._pause(STEP * 1000)
        self._flush()

    def status(self):
        return {"device": self.device.status(), "config": self.config or None,
                "geometry": state.load("geometry") or None, "calibration": state.load("calibration") or None,
                "session": self.session or None}
