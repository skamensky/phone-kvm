"""The phone as an agent uses it: look at the screen, touch it, type, press keys, move a mouse.

Positions are pixels in the image `screenshot` returns, so what the agent sees is what it
points at.
"""
import os
import select
import time

from . import gadget, hid, keymap, state, video

STEP = 0.008  # seconds between touch reports while a finger moves (125 Hz)
SLOW = 2     # mouse counts per report below which Android's pointer acceleration stays off


class Phone:
    def __init__(self):
        self.nodes = gadget.nodes()
        if not self.nodes:
            raise RuntimeError("the USB gadget is not up (run: python -m phone_kvm.gadget up, as root)")
        self.video = state.load("config").get("video")
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

    # -- screen ------------------------------------------------------------------------
    def _need_video(self):
        if not self.video:
            raise RuntimeError("no capture device configured (run: phonectl setup --video /dev/videoN)")
        return self.video

    def screenshot(self, path, settle_ms=2000):
        """Save the screen once it has stopped changing (animations done), or after settle_ms."""
        device = self._need_video()
        image = video.screen(device)
        deadline = time.monotonic() + settle_ms / 1000
        while time.monotonic() < deadline:
            time.sleep(0.15)
            again = video.screen(device)
            if again.tobytes() == image.tobytes():
                break
            image = again
        image.save(path)
        return image.size

    def size(self):
        """The screenshot's size in pixels, which is the coordinate space for touches."""
        geometry = state.load("geometry")
        if not geometry:
            self._calibrate()
            geometry = state.load("geometry")
        left, top, right, bottom = geometry["picture"]
        return right - left, bottom - top

    def _calibrate(self):
        video.screen(self._need_video(), recalibrate=True)

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

    def _mouse_corner(self):
        """Run the cursor into the top-left corner, the one position known without looking."""
        width, height = self.size()
        for _ in range(-(-max(width, height) * 8 // 127)):
            self._send("mouse", hid.mouse_report(0, -127, -127))
        time.sleep(0.05)

    def mouse_calibrate(self):
        """Measure, by watching the screen, how many screenshot pixels one mouse count moves the
        cursor. Needs a screen that does not react to hovering, such as empty wallpaper."""
        from PIL import ImageChops
        width, height = self.size()
        near, span = 40, min(width, height) // 3

        def view(counts):
            self._mouse_corner()
            self.mouse_move(counts, counts)
            time.sleep(0.6)
            return video.screen(self._need_video())

        def moved(a, b):
            box = ImageChops.difference(a, b).convert("L").point(lambda v: 255 if v > 48 else 0).getbbox()
            if box is None:
                raise RuntimeError("the cursor is not visible on the captured screen")
            return box

        origin = view(near)
        half, full = moved(origin, view(near + span // 2)), moved(origin, view(near + span))
        # Each box runs from the first cursor's top-left to the second cursor's bottom-right.
        gain = [(full[2] - half[2]) / (span - span // 2), (full[3] - half[3]) / (span - span // 2)]
        if min(gain) <= 0.05:
            raise RuntimeError("could not see the cursor move; calibrate on a still screen")
        offset = [full[0] - near * gain[0], full[1] - near * gain[1]]
        state.save("mouse", {"pixels_per_count": gain, "offset": offset})
        return {"pixels_per_count": gain, "offset": offset}

    def mouse_to(self, x, y):
        """Put the cursor's tip on a screenshot pixel: into the corner, then out by dead reckoning."""
        width, height = self.size()
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError(f"({x}, {y}) is outside the {width}x{height} screen")
        calibration = state.load("mouse") or self.mouse_calibrate()
        (gx, gy), (ox, oy) = calibration["pixels_per_count"], calibration["offset"]
        self._mouse_corner()
        self.mouse_move(max(0, round((x - ox) / gx)), max(0, round((y - oy) / gy)))

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
        return {"gadget": gadget.status(), "video": self.video, "geometry": state.load("geometry") or None}
