"""The phone as an agent uses it: look at the screen, touch it, type, press keys, move a mouse.

Positions are pixels in the image `screenshot` returns, so what the agent sees is what it
points at. Three things tie an image to the inputs around it, all measured by `calibrate`:

  where   the screen's place in the captured frame, so image pixels are touch positions
  pointer the mouse cursor's position in image pixels, tracked from the counts sent
  when    the delay between an input and the first captured frame that shows it; a screenshot
          never starts sooner than that after the last input, so it cannot show the screen
          from before the input
"""
import os
import select
import time

from . import gadget, hid, keymap, state, video

LATENCY = 0.4   # seconds from an input to a frame showing it, until `calibrate` measures it
STEP = 0.008  # seconds between touch reports while a finger moves (125 Hz)
SLOW = 2     # mouse counts per report below which Android's pointer acceleration stays off


class Phone:
    def __init__(self):
        self.nodes = gadget.nodes()
        if not self.nodes:
            raise RuntimeError("the USB gadget is not up (run: python -m phone_kvm.gadget up, as root)")
        self.config = state.load("config")
        self.session = state.load("session")   # what earlier commands left: input time, pointer
        self._files = {}

    # -- transport ---------------------------------------------------------------------
    def _send(self, interface, report):
        if interface not in self._files:
            self._files[interface] = os.open(self.nodes[interface], os.O_WRONLY | os.O_NONBLOCK)
        fd = self._files[interface]
        # A report is accepted only when the phone polls the endpoint.
        if not select.select([], [fd], [], 2.0)[1]:
            raise RuntimeError(f"the phone is not reading the {interface} interface (cable unplugged, "
                               "or the phone has not enumerated the device)")
        os.write(fd, report)
        self.session["input_at"] = time.time()

    def finish(self):
        """Record this command's inputs for the commands that follow."""
        state.save("session", self.session)

    # -- screen ------------------------------------------------------------------------
    def _need_video(self):
        if not self.config.get("video"):
            raise RuntimeError("no capture device configured (run: phonectl setup --video /dev/videoN)")
        return self.config

    def _capture(self, settle_ms):
        """The screen once it shows the last input and has stopped changing (animations done), or
        after settle_ms: (image, time captured, whether it settled)."""
        config = self._need_video()
        latency = state.load("calibration").get("latency_ms", LATENCY * 1000) / 1000
        time.sleep(max(0, self.session.get("input_at", 0) + latency - time.time()))
        deadline = time.monotonic() + settle_ms / 1000
        image, captured = video.screen(config), time.time()
        settled = False
        while not settled and time.monotonic() < deadline:
            again, captured = video.screen(config), time.time()
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
            video.screen(self._need_video(), recalibrate=True)
            geometry = state.load("geometry")
        return tuple(geometry["size"])

    def calibrate(self, what="all"):
        """Measure what ties an image to inputs.

        screen   where the screen is in the frame. An HDMI capture needs any lit screen; a camera
                 needs a light one that reaches all four edges (Settings or a browser page in a
                 light theme: `key meta+i`), because a dark screen cannot be told from its bezel.
        pointer  how far the cursor moves per mouse count, and how long an input takes to show.
                 Needs a still screen that does not react to hovering, such as the home screen.
        """
        if what in ("all", "screen"):
            video.screen(self._need_video(), recalibrate=True)
            state.save("calibration", {})
            self.session["pointer"] = None
        if what in ("all", "pointer"):
            self._latency_calibrate()   # first: measuring the cursor's travel relies on it
            self._mouse_calibrate()
        width, height = self.size()
        return {"width": width, "height": height, **state.load("geometry"), **state.load("calibration"),
                "pointer": self.session.get("pointer")}

    def frame(self, path):
        """Save the capture device's whole frame, as it arrives: for aiming a camera."""
        image = video.frame(self._need_video())
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
            time.sleep(STEP)
        # Rest at the end so Android sees a stop, not a fling, when asked to hold.
        time.sleep(hold_ms / 1000)
        self._send("touch", hid.touch_report([(cid, False, *path[-1]) for cid, path in enumerate(paths)]))
        self._send("touch", hid.touch_report([]))

    def tap(self, x, y, hold_ms=60):
        point = self._point(x, y)
        self._send("touch", hid.touch_report([(0, True, *point)]))
        time.sleep(hold_ms / 1000)
        self._send("touch", hid.touch_report([(0, False, *point)]))
        self._send("touch", hid.touch_report([]))

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

    def key(self, name, hold_ms=0):
        """A named key (home, back, power, enter, ...) or a combination such as ctrl+a."""
        if name in hid.CONSUMER:
            self._send("consumer", hid.consumer_report(hid.CONSUMER[name]))
            time.sleep(hold_ms / 1000)
            self._send("consumer", hid.consumer_report())
            return
        modifiers, usage = keymap.combo(name)
        self._send("keyboard", hid.keyboard_report(modifiers, [usage] if usage else []))
        time.sleep(hold_ms / 1000)
        self._send("keyboard", hid.keyboard_report())

    # -- mouse -------------------------------------------------------------------------
    BUTTONS = {"left": 1, "right": 2, "middle": 4}

    def mouse_move(self, dx, dy, buttons=0):
        """Move the cursor by mouse counts, slowly enough that Android applies no acceleration,
        so the distance on screen is proportional to the counts."""
        reports = max(1, -(-max(abs(dx), abs(dy)) // SLOW))
        sent_x = sent_y = 0
        for i in range(1, reports + 1):
            x, y = round(dx * i / reports), round(dy * i / reports)
            self._send("mouse", hid.mouse_report(buttons, x - sent_x, y - sent_y))
            sent_x, sent_y = x, y
        pointer, gain = self.session.get("pointer"), state.load("calibration").get("pixels_per_count")
        if pointer and gain:
            width, height = self.size()
            self.session["pointer"] = [round(min(max(pointer[0] + dx * gain[0], 0), width - 1)),
                                       round(min(max(pointer[1] + dy * gain[1], 0), height - 1))]
        else:
            self.session["pointer"] = None

    def _mouse_corner(self):
        """Run the cursor into the top-left corner, the one position known without looking."""
        width, height = self.size()
        for _ in range(-(-max(width, height) * 8 // 127)):
            self._send("mouse", hid.mouse_report(0, -127, -127))
        self.session["pointer"] = None
        time.sleep(0.05)

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
        near, reach = 40, min(width, height)

        def view(counts):
            self._mouse_corner()
            self.mouse_move(counts, counts)
            return self._capture(3000)[0]

        origin = view(near)
        others = {near + round(reach * part): None for part in (0.25, 0.4, 0.55, 0.7, 0.85)}
        for counts in others:
            others[counts] = view(counts)
        # Whatever changed on its own meanwhile (the clock) is not the cursor: leave it out.
        still = ImageChops.difference(origin, view(near)).convert("L").point(lambda v: 0 if v > 32 else 255)
        still = still.filter(ImageFilter.MinFilter(9))

        points = []   # (counts, tip x, tip y)
        for counts, other in others.items():
            change = ImageChops.difference(origin, other).convert("L").point(lambda v: 255 if v > 48 else 0)
            change = ImageChops.multiply(change, still)
            box = change.getbbox()
            if box is None:
                raise RuntimeError("the cursor is not visible on the captured screen")
            # The origin's cursor is the top-left of what changed; the other is down and right.
            middle = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
            far = change.crop((middle[0], middle[1], box[2], box[3])).getbbox()
            if far is not None:
                points += [(near, box[0], box[1]), (counts, middle[0] + far[0], middle[1] + far[1])]
        gain = [median((b[axis] - a[axis]) / (b[0] - a[0]) for a, b in combinations(points, 2) if a[0] != b[0])
                if len(points) > 2 else 0 for axis in (1, 2)]
        if min(gain) <= 0.05:
            raise RuntimeError("could not see the cursor move; calibrate on a still screen")
        offset = [median(point[axis] - point[0] * gain[axis - 1] for point in points) for axis in (1, 2)]
        state.save("calibration", {**state.load("calibration"), "pixels_per_count": gain, "offset": offset})
        self.session["pointer"] = [round(offset[0] + near * gain[0]), round(offset[1] + near * gain[1])]

    def _latency_calibrate(self):
        """Measure the capture's noise (how much a still screen differs from frame to frame) and
        the delay from an input to the first captured frame that shows all of it: watch the video
        while the cursor jumps, three times, and keep the longest."""
        calibration = state.load("calibration")
        stream = video.watch(self._need_video(), seconds=30)
        still = [image for _, image in (next(stream) for _ in range(6))]
        calibration["noise"] = max(video.difference(a, b) for a, b in zip(still, still[1:]))
        state.save("calibration", calibration)   # `video.changed` reads the noise
        delays = []
        for jump in (60, -60, 60):
            for _ in range(8):   # let the last jump finish arriving
                _, before = next(stream)
            self.mouse_move(jump, jump)
            sent, shown, last = time.time(), None, before
            for arrived, image in stream:
                if shown is None and video.changed(before, image):
                    shown = arrived
                elif shown is not None and not video.changed(last, image):
                    break   # two frames alike: `last` showed the whole jump
                if shown is not None:
                    shown = arrived
                last = image
            else:
                raise RuntimeError("the captured screen did not change after moving the cursor")
            delays.append(shown - sent)
        stream.close()
        # A margin of a quarter and one frame, for a delay that varies.
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
        time.sleep(hold_ms / 1000)
        self._send("mouse", hid.mouse_report())

    def mouse_scroll(self, notches):
        """Positive scrolls the content down, as rolling a wheel towards you does."""
        step = -1 if notches > 0 else 1
        for _ in range(abs(notches)):
            self._send("mouse", hid.mouse_report(wheel=step))
            time.sleep(STEP)

    def status(self):
        return {"gadget": gadget.status(), "config": self.config or None,
                "geometry": state.load("geometry") or None, "calibration": state.load("calibration") or None,
                "session": self.session or None}
