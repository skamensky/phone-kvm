"""The phone's screen, from a V4L2 capture device, in one of two views.

  hdmi    an HDMI capture device. A phone mirrors a portrait screen into a landscape frame with
          black bars at the sides; `picture` is the screen's box inside the frame.
  camera  a camera looking at the phone (phone_kvm/camera.py); `quad` is the screen's corners.

Where the screen is in the frame is found once and stored, so that a dark screen later cannot
change it.
"""
import io
import subprocess
import time

from PIL import Image, ImageChops

from . import camera, state

DARK = 24      # HDMI black is not exactly zero after a capture card's colour conversion
AVERAGED = 4   # camera frames averaged into one image: sensor noise drops by half


def _source(config):
    """ffmpeg's input arguments for the capture device."""
    options = []
    if config.get("size"):
        options += ["-video_size", config["size"]]
    if config.get("input_format"):
        options += ["-input_format", config["input_format"]]
    return ["ffmpeg", "-loglevel", "error", "-f", "v4l2", *options, "-i", config["video"]]


def _bitmaps(read):
    """RGB images from a stream of BMP files."""
    while True:
        head = read(6)
        if len(head) < 6:
            return
        yield Image.open(io.BytesIO(head + read(int.from_bytes(head[2:6], "little") - 6))).convert("RGB")


def frames(config, count):
    """`count` consecutive current frames from the capture device, as RGB images."""
    # The first frames out of a capture device can be stale buffers; skip two.
    done = subprocess.run(
        [*_source(config), "-vf", r"select=gte(n\,2)", "-frames:v", str(count),
         "-f", "image2pipe", "-vcodec", "bmp", "-"], capture_output=True, timeout=20)
    images = list(_bitmaps(io.BytesIO(done.stdout).read))
    if done.returncode or len(images) < count:
        raise RuntimeError(f"no video from {config['video']}: {done.stderr.decode().strip() or 'no frame'}")
    return images


def watch(config, seconds):
    """Yield (arrival time, screen) for each frame the device delivers over `seconds`."""
    geometry = state.load("geometry")
    process = subprocess.Popen([*_source(config), "-t", str(seconds), "-f", "image2pipe", "-vcodec", "bmp", "-"],
                               stdout=subprocess.PIPE)
    try:
        for number, image in enumerate(_bitmaps(process.stdout.read)):
            if number >= 2:
                yield time.time(), _cut(config, geometry, image)
    finally:
        process.kill()
        process.wait()


def _cut(config, geometry, image):
    if config["view"] == "hdmi":
        return image.crop(tuple(geometry["picture"]))
    return camera.rectify(image, geometry["quad"], config["rotate"])


def difference(a, b):
    """How much two captures of the screen differ: the largest change in any 8x8 block's average
    brightness. Blocks average away sensor noise; a cursor, a caret or a letter still shows."""
    return ImageChops.difference(a, b).convert("L").reduce(8).getextrema()[1]


def changed(a, b):
    """Whether the screen itself differs between two captures, beyond the capture's own noise
    (measured by `calibrate pointer`)."""
    return difference(a, b) > max(8, 2.5 * state.load("calibration").get("noise", 0))


def frame(config):
    """One current frame. A camera's is the average of several, which removes sensor noise."""
    if config["view"] == "hdmi":
        return frames(config, 1)[0]
    a, b, c, d = frames(config, AVERAGED)
    return Image.blend(Image.blend(a, b, 0.5), Image.blend(c, d, 0.5), 0.5)


def locate(config, image):
    """Find the screen in a frame and store where it is."""
    if config["view"] == "hdmi":
        box = image.convert("L").point(lambda v: 255 if v > DARK else 0).getbbox()
        if box is None:
            raise RuntimeError("the HDMI frame is all black; wake the phone (key power) and calibrate again")
        geometry = {"picture": list(box), "size": [box[2] - box[0], box[3] - box[1]]}
    else:
        quad = camera.find(image)
        geometry = {"quad": quad, "size": list(camera.size(quad, config["rotate"]))}
    geometry["frame"] = list(image.size)
    state.save("geometry", geometry)
    return geometry


def screen(config, recalibrate=False):
    """The phone's screen, upright, cut out of a current frame."""
    image = frame(config)
    geometry = state.load("geometry")
    if recalibrate or geometry.get("frame") != list(image.size):
        geometry = locate(config, image)
    return _cut(config, geometry, image)
