"""The device, over its TCP service (firmware/main/server.c): send input, take a picture.

A request is an operation byte, a 4-byte little-endian length and a payload; so is a reply,
whose first byte is 0 for done or 1 for failed (the payload is then the reason).
"""
import io
import json
import os
import socket
import struct

from PIL import Image

PORT = 7777
MAX_INPUT = 32768   # bytes of input in one request; the device takes twice that


class Device:
    def __init__(self, address):
        host, _, port = address.partition(":")
        try:
            self.socket = socket.create_connection((host, int(port or PORT)), timeout=5)
        except OSError as error:
            raise RuntimeError(f"cannot reach the device at {address}: {error}") from None
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # The real board is on a network, so it wants its token first; never stored in files.
        token = os.environ.get("PHONE_KVM_TOKEN")
        if token:
            self._request(b"A", token.encode())

    def _read(self, length):
        data = bytearray()
        while len(data) < length:
            chunk = self.socket.recv(length - len(data))
            if not chunk:
                raise RuntimeError("the device closed the connection")
            data += chunk
        return bytes(data)

    def _request(self, operation, payload=b"", seconds=10):
        self.socket.settimeout(seconds)
        try:
            self.socket.sendall(operation + struct.pack("<I", len(payload)) + payload)
            failed, length = struct.unpack("<BI", self._read(5))
            reply = self._read(length)
        except socket.timeout:
            raise RuntimeError("the device did not answer") from None
        if failed:
            raise RuntimeError(reply.decode())
        return reply

    def input(self, reports):
        """Send HID reports, each (milliseconds to wait before it, interface number, report).
        The device paces them and answers once the phone has taken the last."""
        payload = b"".join(struct.pack("<HBB", wait, interface, len(report)) + report
                           for wait, interface, report in reports)
        self._request(b"H", payload, 10 + sum(wait for wait, _, _ in reports) / 1000)

    def capture(self):
        """One picture from the device's camera."""
        return Image.open(io.BytesIO(self._request(b"C"))).convert("RGB")

    def status(self):
        return json.loads(self._request(b"S"))
