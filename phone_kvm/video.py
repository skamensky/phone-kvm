"""The phone's screen, from the HDMI capture device (any V4L2 capture device).

A phone mirrors a portrait screen into a landscape HDMI frame with black bars at the sides.
`picture` finds the screen inside the frame; the result is stored so that a dark screen
later cannot shrink it.
"""
import io
import subprocess

from PIL import Image

from . import state

DARK = 24  # HDMI black is not exactly zero after a capture card's colour conversion


def frame(device):
    """One current frame from the capture device, as an RGB image."""
    # The first frames out of a capture device can be stale buffers; take the third.
    done = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-i", device, "-vf", r"select=gte(n\,2)",
         "-frames:v", "1", "-f", "image2pipe", "-vcodec", "bmp", "-"],
        capture_output=True, timeout=15)
    if done.returncode or not done.stdout:
        raise RuntimeError(f"no video from {device}: {done.stderr.decode().strip() or 'no frame'}")
    return Image.open(io.BytesIO(done.stdout)).convert("RGB")


def detect(image):
    """The bounding box of everything brighter than black, or None for an all-dark frame."""
    return image.convert("L").point(lambda v: 255 if v > DARK else 0).getbbox()


def picture(image, recalibrate=False):
    """(left, top, right, bottom) of the phone's screen within the HDMI frame."""
    stored = state.load("geometry")
    if not recalibrate and stored.get("frame") == list(image.size):
        return tuple(stored["picture"])
    box = detect(image)
    if box is None:
        raise RuntimeError("the HDMI frame is all black; wake the phone (key power) and calibrate again")
    state.save("geometry", {"frame": list(image.size), "picture": list(box)})
    return box


def screen(device, recalibrate=False):
    """The phone's screen cropped out of a current frame."""
    image = frame(device)
    return image.crop(picture(image, recalibrate))
