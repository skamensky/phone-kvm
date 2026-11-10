"""How much camera resolution does reading a phone screen take?

Each phone display image (1080x2400 PNGs given as arguments) goes through the rig's camera scene
(rig/camera.py: placement, keystone, lens blur, sensor noise) at several sensor resolutions and
lens qualities, optionally through JPEG as a cheap camera module delivers it, then through the
device's own screen finding and rectifying (phone_kvm/camera.py). Tesseract reads the result;
the score is the share of the words it reads on the original display that it still reads.

Not modelled: glare, moire against the pixel grid, exposure, focus falloff, lens distortion.

  python -m tools.camera_study OUT_DIR DISPLAY.png...     prints one JSON object
"""
import io
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

from phone_kvm import camera
from rig.camera import still

SENSORS = {"0.3 MP 640x480 (VGA)": (640, 480), "0.9 MP 1280x720 (720p webcam)": (1280, 720),
           "2 MP 1600x1200 (OV2640)": (1600, 1200), "2 MP 1920x1080 (1080p webcam)": (1920, 1080),
           "5 MP 2592x1944 (OV5647)": (2592, 1944)}
LENSES = {"sharp": 0.8, "soft": 1.5}   # Gaussian blur sigma, in sensor pixels
FILL, NOISE, FRAMES = 0.8, 6, 4


def words(image):
    """Words of three or more letters or digits that Tesseract is confident of."""
    scale = max(1, round(2400 / image.height))   # Tesseract wants text about 30 pixels tall
    image = image.resize((image.width * scale, image.height * scale), Image.LANCZOS)
    data = io.BytesIO()
    image.save(data, "PNG")
    tsv = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "11", "tsv"], input=data.getvalue(),
                         capture_output=True, check=True).stdout.decode()
    found = []
    for line in tsv.splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) == 12 and float(cells[10]) >= 60:
            word = "".join(c for c in cells[11].lower() if c.isalnum())
            if len(word) >= 3:
                found.append(word)
    return found


def seen(display, sensor, blur, jpeg):
    """The display as the device would hand it to an agent through this camera."""
    frames = []
    for _ in range(FRAMES):
        frame = still(display, sensor, FILL, blur, NOISE)
        if jpeg:
            data = io.BytesIO()
            frame.save(data, "JPEG", quality=jpeg)
            frame = Image.open(data).convert("RGB")
        frames.append(frame)
    a, b, c, d = frames
    frame = Image.blend(Image.blend(a, b, 0.5), Image.blend(c, d, 0.5), 0.5)
    return camera.rectify(frame, camera.find(frame), 90)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    displays = {Path(p).stem: Image.open(p).convert("RGB") for p in sys.argv[2:]}
    truth = {name: set(words(image)) for name, image in displays.items()}
    results = {"words_on_display": {name: len(t) for name, t in truth.items()}, "cameras": []}
    hdmi = {name: image.resize((486, 1080), Image.LANCZOS) for name, image in displays.items()}
    results["hdmi_486x1080"] = {name: round(len(truth[name] & set(words(image))) / len(truth[name]), 3)
                                for name, image in hdmi.items()}
    for sensor_name, sensor in SENSORS.items():
        for lens, blur in LENSES.items():
            for jpeg in (None, 80):
                row = {"sensor": sensor_name, "lens": lens, "jpeg": jpeg, "read": {}}
                for name, display in displays.items():
                    image = seen(display, sensor, blur, jpeg)
                    row["screen_pixels"] = list(image.size)
                    row["read"][name] = round(len(truth[name] & set(words(image))) / len(truth[name]), 3)
                    image.save(out / f"{name}-{sensor[0]}x{sensor[1]}-{lens}{'-jpeg' if jpeg else ''}.png")
                results["cameras"].append(row)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
