"""The phone's screen, from the device's camera looking at it (phone_kvm/camera.py).

Where the screen is in the camera's frame is found once and stored, so that a dark screen
later cannot change it.
"""
import time

from PIL import Image, ImageChops

from . import camera, state

AVERAGED = 4   # pictures averaged into one image: sensor noise drops by half


def watch(device, config, seconds):
    """Yield (time taken, screen) for picture after picture over `seconds`."""
    geometry = state.load("geometry")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        taken = time.time()
        yield taken, camera.rectify(device.capture(), geometry["quad"], config["rotate"])


def difference(a, b):
    """How much two captures of the screen differ: the largest change in any 8x8 block's average
    brightness. Blocks average away sensor noise; a cursor, a caret or a letter still shows."""
    return ImageChops.difference(a, b).convert("L").reduce(8).getextrema()[1]


def changed(a, b):
    """Whether the screen itself differs between two captures, beyond the camera's own noise
    (measured by `calibrate pointer`)."""
    return difference(a, b) > max(8, 2.5 * state.load("calibration").get("noise", 0))


def frame(device):
    """The camera's whole frame: the average of several pictures, which removes sensor noise."""
    a, b, c, d = (device.capture() for _ in range(AVERAGED))
    return Image.blend(Image.blend(a, b, 0.5), Image.blend(c, d, 0.5), 0.5)


def locate(config, image):
    """Find the screen in a frame and store where it is."""
    quad = camera.find(image)
    geometry = {"quad": quad, "size": list(camera.size(quad, config["rotate"])), "frame": list(image.size)}
    state.save("geometry", geometry)
    return geometry


def screen(device, config, recalibrate=False):
    """The phone's screen, upright, cut out of a current frame."""
    image = frame(device)
    geometry = state.load("geometry")
    if recalibrate or geometry.get("frame") != list(image.size):
        geometry = locate(config, image)
    return camera.rectify(image, geometry["quad"], config["rotate"])
