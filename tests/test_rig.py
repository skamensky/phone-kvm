"""The device against the virtual phone. Input goes in only through `phonectl` (USB HID) and
the screen comes out only through `phonectl screenshot` (HDMI); adb is the oracle that says
what Android actually did. Needs `bin/rig up`; skipped otherwise.
"""
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageChops, ImageStat

if "ANDROID_HOME" not in os.environ:
    pytest.skip("not in the project's Nix shell", allow_module_level=True)
from rig.__main__ import ADB, BUILD, ENV, SERIAL, adb, status  # noqa: E402

if not status()["ready"]:
    pytest.skip("the rig is not up (bin/rig up)", allow_module_level=True)

DEVICE_ENV = {**os.environ, "PHONE_KVM_STATE": str(BUILD / "state")}


def phonectl(*args):
    done = subprocess.run([sys.executable, "-m", "phone_kvm.cli", *map(str, args)],
                          capture_output=True, text=True, env=DEVICE_ENV)
    result = json.loads(done.stdout)
    assert done.returncode == 0, result
    return result


def until(check, seconds=8):
    """Poll the oracle: the phone takes up to a second to show what it was told."""
    deadline = time.monotonic() + seconds
    while True:
        value = check()
        # An XML element without children is falsy, so compare with None and False explicitly.
        if (value is not None and value is not False) or time.monotonic() > deadline:
            return value if value is not False else None
        time.sleep(0.3)


def nodes():
    xml = adb("exec-out", "uiautomator", "dump", "/dev/tty", check=False).stdout
    xml = xml[:xml.rfind(">") + 1]
    return list(ET.fromstring(xml).iter("node")) if xml.startswith("<?xml") else []


def node(**attributes):
    return next((n for n in nodes() if all(n.get(k.rstrip("_").replace("_", "-")) == v for k, v in attributes.items())), None)


def bounds(n):
    return tuple(map(int, re.findall(r"\d+", n.get("bounds"))))


def centre(n, at=0.5):
    """A point in a node, in screenshot pixels (the unit phonectl takes)."""
    left, top, right, bottom = bounds(n)
    width, height = map(int, re.search(r"(\d+)x(\d+)", adb("shell", "wm", "size").stdout).groups())
    return (round((left + (right - left) * at) * SCREEN[0] / width), round((top + bottom) / 2 * SCREEN[1] / height))


def focus():
    return adb("shell", "dumpsys", "window").stdout.split("mCurrentFocus=")[1].split("\n")[0]


def events(action, seconds=1.5):
    """Raw kernel input events Android received while `action` ran."""
    listener = subprocess.Popen([ADB, "-s", SERIAL, "shell", "getevent", "-l"], stdout=subprocess.PIPE,
                                text=True, env=ENV)
    time.sleep(0.7)
    action()
    time.sleep(seconds)
    listener.terminate()
    return listener.communicate()[0]


@pytest.fixture(scope="module", autouse=True)
def awake():
    for secret in CREDENTIALS:   # a failed earlier run may have left the phone locked
        adb("shell", "locksettings", "clear", "--old", shlex.quote(secret), check=False)
    if "Awake" not in adb("shell", "dumpsys", "power").stdout:
        phonectl("key", "power")
    dismiss()
    # Just after boot the launcher is still starting.
    assert until(lambda: "NexusLauncherActivity" in focus() and node(text="Chrome") is not None, 90)
    global SCREEN
    result = phonectl("calibrate")
    SCREEN = result["width"], result["height"]


@pytest.fixture(autouse=True)
def home():
    phonectl("key", "home")
    assert until(lambda: "NexusLauncherActivity" in focus() and node(text="Chrome") is not None)


PIN, PASSWORD, PATTERN = "271828", "Tr0ub4dor&3 {x}~", "14789"
CREDENTIALS = [PIN, PASSWORD, PATTERN]


def locked():
    return "isKeyguardShowing=true" in adb("shell", "dumpsys", "window").stdout


def dismiss():
    """Oracle-side cleanup: leave the phone awake and past a lock screen that has no credential."""
    def gone():
        adb("shell", "wm", "dismiss-keyguard")
        time.sleep(1)
        return not locked()
    assert until(gone, 15)


def wakefulness():
    return re.search(r"mWakefulness=(\w+)", adb("shell", "dumpsys", "power").stdout).group(1)


def lock_with(kind, secret):
    """Give the phone a screen lock (through adb: the oracle's side), then lock it with the
    power key and wake it to the lock screen, as its owner would find it."""
    assert "set to" in adb("shell", "locksettings", f"set-{kind}", shlex.quote(secret)).stdout
    phonectl("key", "power")
    assert until(lambda: wakefulness() != "Awake")
    time.sleep(1)
    phonectl("key", "power")
    assert until(lambda: wakefulness() == "Awake" and locked())
    time.sleep(1)
    x = SCREEN[0] // 2
    phonectl("swipe", x, SCREEN[1] * 5 // 6, x, SCREEN[1] // 4, "--ms", 250)   # up to the entry screen


def settings():
    adb("shell", "am", "force-stop", "com.google.android.settings.intelligence")   # its search screen
    adb("shell", "am", "start", "-S", "-n", "com.android.settings/.Settings")
    assert until(lambda: node(text="Network & internet")) is not None


def test_android_enumerates_all_four_interfaces():
    dump = adb("shell", "dumpsys", "input").stdout
    devices = [block for block in dump.split("\n    ") if "vendor=0x1209, product=0x0001" in block]
    # Sources are listed in the lines that follow each identifier; search the whole dump section.
    section = dump[dump.index("Input Reader State"):]
    ours = re.findall(r"Device \d+: phone-kvm.*?(?=\n  Device \d+:|\Z)", section, re.S)
    assert len(ours) == 4, devices
    sources = " ".join(re.search(r"Sources: (.*)", block).group(1) for block in ours)
    for source in ("KEYBOARD", "TOUCHSCREEN", "MOUSE"):
        assert source in sources
    assert all("IsExternal: true" in block for block in ours)


def test_screenshot_shows_what_the_phone_displays():
    path = BUILD / "test-screenshot.png"
    shot = phonectl("screenshot", path)
    seen = Image.open(path).convert("RGB")
    assert seen.size == (shot["width"], shot["height"])
    raw = subprocess.run([ADB, "-s", SERIAL, "exec-out", "screencap", "-p"], capture_output=True, env=ENV).stdout
    truth = Image.open(io.BytesIO(raw)).convert("RGB")
    assert abs(seen.width / seen.height - truth.width / truth.height) < 0.01
    difference = ImageStat.Stat(ImageChops.difference(seen, truth.resize(seen.size))).mean
    assert max(difference) < 6, difference


def test_tap_focuses_a_field_and_typing_enters_every_character():
    settings()
    phonectl("tap", *centre(node(text="Search Settings")))
    assert until(lambda: node(class_="android.widget.EditText", focused="true")) is not None
    text = "Hello, World 123 " + "".join(chr(c) for c in range(33, 127))
    phonectl("type", text)
    assert until(lambda: node(class_="android.widget.EditText", text=text)) is not None, \
        node(class_="android.widget.EditText", focused="true").get("text")
    phonectl("key", "ctrl+a", "backspace")
    assert until(lambda: node(class_="android.widget.EditText", focused="true").get("text") != text)


def test_back_home_and_recents_keys():
    settings()
    phonectl("key", "recents")
    assert until(lambda: "com.android.settings" not in focus())   # the app switcher has taken over
    phonectl("key", "back")
    assert until(lambda: "com.android.settings" in focus())
    phonectl("key", "home")
    assert until(lambda: "NexusLauncherActivity" in focus())


def test_volume_media_and_system_keys_arrive_as_the_right_keys():
    expected = {"volume-up": "KEY_VOLUMEUP", "volume-down": "KEY_VOLUMEDOWN", "mute": "KEY_MUTE",
                "play-pause": "KEY_PLAYPAUSE", "next-track": "KEY_NEXTSONG",
                "previous-track": "KEY_PREVIOUSSONG", "search": "KEY_SEARCH", "menu": "KEY_MENU"}
    log = events(lambda: phonectl("key", *expected))
    assert re.findall(r"EV_KEY\s+(\w+)\s+DOWN", log) == list(expected.values())


def test_power_key_sleeps_and_wakes():
    phonectl("key", "power")
    assert until(lambda: wakefulness() != "Awake")
    phonectl("key", "power")
    assert until(lambda: wakefulness() == "Awake")
    dismiss()


def test_unlock_with_a_pin():
    try:
        lock_with("pin", PIN)
        phonectl("type", PIN)
        phonectl("key", "enter")
        assert until(lambda: not locked())
    finally:
        adb("shell", "locksettings", "clear", "--old", PIN, check=False)


def test_unlock_with_a_password():
    try:
        lock_with("password", PASSWORD)
        phonectl("type", PASSWORD)
        phonectl("key", "enter")
        assert until(lambda: not locked())
    finally:
        adb("shell", "locksettings", "clear", "--old", shlex.quote(PASSWORD), check=False)


def test_unlock_with_a_pattern():
    try:
        lock_with("pattern", PATTERN)
        grid = until(lambda: node(resource_id="com.android.systemui:id/lockPatternView"))
        assert grid is not None
        left, top, right, bottom = bounds(grid)
        width, height = map(int, re.search(r"(\d+)x(\d+)", adb("shell", "wm", "size").stdout).groups())
        points = []
        for dot in map(int, PATTERN):   # dots are numbered 1-9, left to right, top to bottom
            column, row = (dot - 1) % 3, (dot - 1) // 3
            points += [round((left + (right - left) * (2 * column + 1) / 6) * SCREEN[0] / width),
                       round((top + (bottom - top) * (2 * row + 1) / 6) * SCREEN[1] / height)]
        phonectl("swipe", *points, "--ms", 900, "--hold-ms", 150)
        assert until(lambda: not locked())
    finally:
        adb("shell", "locksettings", "clear", "--old", PATTERN, check=False)


def test_long_press_opens_the_launcher_menu():
    phonectl("tap", SCREEN[0] // 2, SCREEN[1] // 2, "--hold-ms", 900)
    assert until(lambda: node(text="Wallpaper & style")) is not None
    phonectl("key", "back")


def test_swipe_scrolls():
    settings()
    before = bounds(node(text="Network & internet"))
    x = SCREEN[0] // 2
    phonectl("swipe", x, SCREEN[1] * 3 // 4, x, SCREEN[1] // 2, "--hold-ms", 200)

    def scrolled():
        after = node(text="Network & internet")
        return after is None or bounds(after)[1] < before[1] - 200
    assert until(scrolled)


def test_pinch_is_two_fingers():
    x, y = SCREEN[0] // 2, SCREEN[1] // 3
    log = events(lambda: phonectl("pinch", x, y, "--start", 80, "--end", 300))
    slots = set(re.findall(r"ABS_MT_SLOT\s+(\w+)", log))
    assert {"00000001"} <= slots, log[-2000:]
    assert log.count("ABS_MT_TRACKING_ID") >= 4   # two fingers down, two up


def test_mouse_points_clicks_and_scrolls():
    phonectl("mouse", "calibrate")   # on the home screen, which ignores hovering
    settings()
    phonectl("mouse", "scroll", 6)
    assert until(lambda: node(text="Network & internet") is None or bounds(node(text="Network & internet"))[1] < 300)
    phonectl("mouse", "scroll", -12)
    target = until(lambda: node(text="Network & internet"))
    assert target is not None
    phonectl("mouse", "to", *centre(target))
    phonectl("mouse", "click")
    assert until(lambda: node(text="Internet") is not None and node(text="Hotspot & tethering") is not None)
