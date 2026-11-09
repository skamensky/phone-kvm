"""The virtual HDMI cable: the emulator's display as an HDMI signal on a V4L2 loopback device.

A phone mirrors its screen over USB-C DisplayPort as a fixed landscape frame with the portrait
screen centred between black bars. This does the same: it reads the emulator's framebuffer
and writes a continuous WIDTHxHEIGHT stream, which the device then captures exactly as it
would capture a real HDMI dongle. adb is used here only as the cable.
"""
import argparse
import struct
import subprocess
import threading
import time

FPS = 10


def framebuffers(adb):
    """Yield (width, height, rgba bytes) as fast as the emulator hands them over."""
    while True:
        raw = subprocess.run([*adb, "exec-out", "screencap"], capture_output=True).stdout
        if len(raw) < 16:
            time.sleep(0.5)  # emulator not ready, or gone; the caller's supervisor decides
            continue
        width, height = struct.unpack_from("<II", raw)
        yield width, height, raw[len(raw) - width * height * 4:]


def encoder(width, height, out_width, out_height, device):
    return subprocess.Popen(
        ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
         "-r", str(FPS), "-i", "-",
         "-vf", f"scale={out_width}:{out_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={out_width}:{out_height}:(ow-iw)/2:(oh-ih)/2:black",
         "-pix_fmt", "yuyv422", "-f", "v4l2", device],
        stdin=subprocess.PIPE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--size", default="1920x1080")
    args = parser.parse_args()
    out_width, out_height = map(int, args.size.split("x"))

    latest = {}

    def grab():
        for frame in framebuffers([args.adb, "-s", args.serial]):
            latest["frame"] = frame

    threading.Thread(target=grab, daemon=True).start()
    process, shape = None, None
    while True:
        started = time.monotonic()
        if "frame" in latest:
            width, height, pixels = latest["frame"]
            if (width, height) != shape:  # first frame, or the phone rotated
                if process:
                    process.stdin.close()
                    process.wait()
                process, shape = encoder(width, height, out_width, out_height, args.device), (width, height)
            process.stdin.write(pixels)
        time.sleep(max(0, 1 / FPS - (time.monotonic() - started)))


if __name__ == "__main__":
    main()
