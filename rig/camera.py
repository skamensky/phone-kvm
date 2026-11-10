"""The scene in front of the simulated board's camera: the phone lying on a desk, as a camera
above it sees it. The latest picture is kept in a JPEG file, which the board's camera
(sim/qemu/esp32s3_simcam.c) reads whenever the firmware takes one.

A camera sees what a person sees, including the windows Android blanks on its video output:
lock-screen entry and banking apps. So the picture comes from the emulator's own display (its
console's screenshot), not from Android's screencap.

The scene: the phone lies on its side so that its long edge runs along the sensor's long edge,
fills part of the frame, has a black bezel, sits on a dark desk and is seen slightly off-axis
(keystone). The lens blurs and the sensor adds noise. A real camera also adds glare, moire
against the pixel grid and exposure changes, which this does not model.
"""
import argparse
import subprocess
import threading
import time
from pathlib import Path

from PIL import Image

FPS = 10
USB_QUIET = 0.15   # seconds without a USB report before the display is taken again
DESK = "0x2a2622"


def scene(width, height, sensor, fill, blur, noise):
    """The ffmpeg filter graph from the phone's upright display ([0:v], rgba) to the camera frame."""
    out_width, out_height = sensor
    screen_width = round(out_width * fill / 2) * 2
    screen_height = round(screen_width * width / height / 2) * 2
    bezel = round(screen_width * 0.03)
    w, h = out_width, out_height
    # Where the frame's corners land: a few percent of keystone and turn.
    corners = f"x0={.02 * w}:y0={.015 * h}:x1={w}:y1=0:x2=0:y2={h}:x3={.985 * w}:y3={.98 * h}"
    lens = (f",gblur=sigma={blur}" if blur else "") + (f",noise=alls={noise}:allf=t" if noise else "")
    return (f"[0:v]format=gbrap,transpose=1,scale={screen_width}:{screen_height}:flags=area,"
            f"pad={screen_width + 2 * bezel}:{screen_height + 2 * bezel}:{bezel}:{bezel}:black,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black@0,perspective={corners}:sense=destination[phone];"
            f"color=c={DESK}:s={w}x{h}:r={FPS}[desk];[desk][phone]overlay=shortest=1{lens}")


def still(image, sensor, fill, blur, noise):
    """One camera frame of a display image, without a loopback device (for measurements)."""
    done = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % image.size,
         "-r", str(FPS), "-i", "-", "-filter_complex", scene(*image.size, sensor, fill, blur, noise),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        input=image.convert("RGBA").tobytes(), capture_output=True, check=True)
    return Image.frombytes("RGB", sensor, done.stdout)


def displays(adb, scratch, activity):
    """Yield the emulator's display, as RGBA images, as fast as its console hands them over.

    Taking the display stops the emulator for some 60 ms, and it then hands the phone the board's
    USB reports late and in a burst, which a real phone never sees: a swipe turns into a jump.
    So no picture is taken while reports are flowing (rig/cable.py touches `activity` for each).
    """
    scratch.mkdir(parents=True, exist_ok=True)
    while True:
        while activity.exists() and time.time() - activity.stat().st_mtime < USB_QUIET:
            time.sleep(0.01)
        for old in scratch.glob("*.png"):
            old.unlink()
        subprocess.run([*adb, "emu", "screenrecord", "screenshot", str(scratch)], capture_output=True)
        try:
            yield Image.open(next(scratch.glob("*.png"))).convert("RGBA")
        except (StopIteration, OSError):
            time.sleep(0.5)  # emulator not ready, or gone; the caller's supervisor decides


def encoder(size, args):
    sensor = tuple(map(int, args.size.split("x")))
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", "%dx%d" % size,
         "-r", str(FPS), "-i", "-", "-filter_complex", scene(*size, sensor, args.fill, args.blur, args.noise),
         "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "image2", "-update", "1", "-atomic_writing", "1",
         str(args.picture)],
        stdin=subprocess.PIPE)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--picture", required=True, type=Path, help="the JPEG file to keep current")
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--activity", required=True, type=Path, help="the file rig/cable.py touches per report")
    parser.add_argument("--size", default="1600x1200", help="the sensor's resolution")
    parser.add_argument("--fill", type=float, default=0.8, help="how much of the frame's width the screen spans")
    parser.add_argument("--blur", type=float, default=0.8, help="lens blur, as a Gaussian sigma in sensor pixels")
    parser.add_argument("--noise", type=int, default=6, help="sensor noise strength")
    args = parser.parse_args()

    latest = {}

    def grab():
        for image in displays([args.adb, "-s", args.serial], args.scratch, args.activity):
            latest["image"] = image

    threading.Thread(target=grab, daemon=True).start()
    process, shape = None, None
    while True:
        started = time.monotonic()
        if "image" in latest:
            image = latest["image"]
            if image.size != shape:
                if process:
                    process.stdin.close()
                    process.wait()
                process, shape = encoder(image.size, args), image.size
            process.stdin.write(image.tobytes())
        time.sleep(max(0, 1 / FPS - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
