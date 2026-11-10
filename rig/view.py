"""What the board's camera sees, live, for a person aiming the webcam: an HTTP server whose one
page is the camera's current picture as a continuous (MJPEG) stream, which any browser shows.
"""
import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--picture", required=True, type=Path, help="the JPEG file the camera view keeps current")
    parser.add_argument("--listen", required=True, metavar="HOST:PORT")
    args = parser.parse_args()

    class Stream(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=picture")
            self.end_headers()
            try:
                while True:
                    picture = args.picture.read_bytes()
                    self.wfile.write(b"--picture\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n"
                                     % len(picture) + picture + b"\r\n")
                    time.sleep(0.1)
            except OSError:
                pass

        def log_message(self, *_):
            pass

    host, port = args.listen.rsplit(":", 1)
    ThreadingHTTPServer((host, int(port)), Stream).serve_forever()


if __name__ == "__main__":
    main()
