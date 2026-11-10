"""A real camera in front of the simulated board's: a webcam on this host looking at a real
phone. Its latest picture is kept in the JPEG file the board's camera
(sim/qemu/esp32s3_simcam.c) reads, exactly as the webcam compressed it.
"""
import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", required=True, help="the webcam, such as /dev/video0")
    parser.add_argument("--picture", required=True, type=Path, help="the JPEG file to keep current")
    parser.add_argument("--size", default="1920x1080", help="the resolution asked of the webcam")
    args = parser.parse_args()
    # Webcams send JPEGs without their Huffman tables; mjpeg2jpeg puts the standard ones back.
    os.execvp("ffmpeg", ["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
                         "-video_size", args.size, "-i", args.device, "-c", "copy", "-bsf:v", "mjpeg2jpeg",
                         "-f", "image2", "-update", "1", "-atomic_writing", "1", str(args.picture)])


if __name__ == "__main__":
    main()
