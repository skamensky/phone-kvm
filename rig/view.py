"""What the board's camera sees, live, for a person aiming the webcam: an HTTP server whose one
page is the camera's current picture as a continuous (MJPEG) stream, which any browser shows.
`--rotate` turns the picture so that a phone lying sideways in the frame is shown upright.
"""
import argparse
import io
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--picture", required=True, type=Path, help="the JPEG file the camera view keeps current")
    parser.add_argument("--listen", required=True, metavar="HOST:PORT")
    parser.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                        help="how far clockwise the phone appears turned in the camera's picture")
    args = parser.parse_args()

    def picture():
        data = args.picture.read_bytes()
        if not args.rotate:
            return data
        out = io.BytesIO()
        Image.open(io.BytesIO(data)).rotate(args.rotate, expand=True).save(out, "JPEG", quality=85)
        return out.getvalue()

    class Stream(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=picture")
            self.end_headers()
            try:
                while True:
                    data = picture()
                    self.wfile.write(b"--picture\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                     % len(data) + data + b"\r\n")
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *_):
            pass

    host, port = args.listen.rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), Stream).serve_forever()


if __name__ == "__main__":
    main()
