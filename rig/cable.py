"""The virtual USB cable: one end in the simulated board's USB port, the other in this host's
USB stack, from where the Android emulator takes the device as it would from a real port.

The board end is the emulator's USB controller (sim/qemu/esp32s3_usb_otg.c), which speaks in
frames, each `type (1 byte), endpoint (1 byte), length (2 bytes, little endian), data`:

  to the board    R bus reset · S SETUP packet · O OUT packet · K the host took the IN packet
  from the board  C pulled up (attached) · D released · I IN packet · A took the OUT packet
                  T STALL · F dropped the IN packets it had not sent

The host end is a USB/IP server: `usbip attach` hands the connection to the kernel's vhci-hcd,
which then sends every USB transfer here. The cable plays the host controller's part: it
turns transfers into packets and packets into transfers, and holds an IN packet back until the
host asks for it, as a real bus does.
"""
import argparse
import queue
import socket
import struct
import threading
import time

BUSID = b"1-1"
SPEED_FULL = 2
EPIPE, ECONNRESET, ETIMEDOUT = 32, 104, 110
TIMEOUT = 2.0


class Board:
    """The board's end: frames in and out, sorted by what they answer."""

    def __init__(self, address):
        host, port = address.rsplit(":", 1)
        while True:
            try:
                self.socket = socket.create_connection((host, int(port)))
                break
            except OSError:
                time.sleep(0.2)
        # Each frame is a few bytes and waits on the one before: batching them would stall USB.
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.lock = threading.Lock()         # guards everything below and writes to the socket
        self.attached = False
        self.control = queue.Queue()         # endpoint 0's I, A and T frames, in order
        self.ready = {}                      # endpoint -> the IN packet the board is holding out
        self.waiting = {}                    # endpoint -> [callback] of transfers wanting a packet
        threading.Thread(target=self._read, daemon=True).start()

    def send(self, kind, endpoint=0, data=b""):
        self.socket.sendall(struct.pack("<cBH", kind, endpoint, len(data)) + data)

    def _exactly(self, length):
        data = b""
        while len(data) < length:
            chunk = self.socket.recv(length - len(data))
            if not chunk:
                raise SystemExit("the simulated board is gone")
            data += chunk
        return data

    def _read(self):
        while True:
            kind, endpoint, length = struct.unpack("<cBH", self._exactly(4))
            data = self._exactly(length)
            with self.lock:
                if kind == b"C":
                    self.attached = True
                elif kind == b"D":
                    self.attached = False
                elif endpoint == 0:
                    if kind != b"F":
                        self.control.put((kind, data))
                elif kind == b"I":
                    if self.waiting.get(endpoint):
                        self.waiting[endpoint].pop(0)(data)
                        self.send(b"K", endpoint)
                    else:
                        self.ready[endpoint] = data
                elif kind == b"F":
                    self.ready.pop(endpoint, None)

    # -- transfers -------------------------------------------------------------------
    def _expect(self, *kinds):
        """The next endpoint 0 frame, which must be one of `kinds`; None on a STALL or silence."""
        try:
            kind, data = self.control.get(timeout=TIMEOUT)
        except queue.Empty:
            return None
        return data if kind in kinds else None

    def transfer(self, setup, data=b"", packet=64):
        """One control transfer: (status, the data the board returned)."""
        while not self.control.empty():
            self.control.get()
        direction_in, length = setup[0] & 0x80, struct.unpack("<H", setup[6:8])[0]
        with self.lock:
            self.send(b"S", 0, setup)
        if direction_in and length:
            reply = b""
            while True:
                part = self._expect(b"I")
                if part is None:
                    return -EPIPE, b""
                reply += part
                with self.lock:
                    self.send(b"K")
                if len(part) < packet or len(reply) >= length:
                    break
            with self.lock:
                self.send(b"O")                       # status stage
            return (0, reply) if self._expect(b"A") is not None else (-EPIPE, b"")
        for at in range(0, len(data), packet):
            with self.lock:
                self.send(b"O", 0, data[at:at + packet])
            if self._expect(b"A") is None:
                return -EPIPE, b""
        if self._expect(b"I") is None:                # status stage
            return -EPIPE, b""
        with self.lock:
            self.send(b"K")
        return 0, b""

    def reset(self):
        with self.lock:
            self.ready.clear()
            self.send(b"R")
        time.sleep(0.05)

    def take(self, endpoint, callback):
        """Give `callback` the next IN packet from an endpoint, now or when the board has one."""
        with self.lock:
            if endpoint in self.ready:
                callback(self.ready.pop(endpoint))
                self.send(b"K", endpoint)
            else:
                self.waiting.setdefault(endpoint, []).append(callback)

    def forget(self, endpoint, callback):
        """Withdraw a waiting transfer; whether it was still waiting."""
        with self.lock:
            if callback in self.waiting.get(endpoint, []):
                self.waiting[endpoint].remove(callback)
                return True
            return False


def describe(board):
    """The device as USB/IP lists it, read from its own descriptors: (device record, interfaces)."""
    board.reset()
    status, device = board.transfer(struct.pack("<BBHHH", 0x80, 6, 0x0100, 0, 18))
    status2, head = board.transfer(struct.pack("<BBHHH", 0x80, 6, 0x0200, 0, 9))
    if status or status2:
        raise RuntimeError("the board did not return its descriptors")
    _, configuration = board.transfer(struct.pack("<BBHHH", 0x80, 6, 0x0200, 0, struct.unpack("<H", head[2:4])[0]))
    interfaces, at = [], 0
    while at < len(configuration):
        if configuration[at + 1] == 4:
            interfaces.append(configuration[at + 5:at + 8])
        at += configuration[at]
    record = struct.pack(">256s32sIIIHHHBBBBBB", b"/sys/devices/phone-kvm/sim/" + BUSID, BUSID, 1, 2, SPEED_FULL,
                         *struct.unpack("<HHH", device[8:14]), device[4], device[5], device[6],
                         configuration[5], device[17], configuration[4])
    return record, interfaces


def serve(board, connection):
    """One USB/IP connection: a device listing, or an attachment and then its transfers."""
    def exactly(length):
        data = b""
        while len(data) < length:
            chunk = connection.recv(length - len(data))
            if not chunk:
                raise ConnectionError
            data += chunk
        return data

    _, code, _ = struct.unpack(">HHI", exactly(8))
    if not board.attached:
        connection.sendall(struct.pack(">HHI", 0x0111, code & 0xFF, 0 if code == 0x8005 else 1)
                           + (struct.pack(">I", 0) if code == 0x8005 else b""))
        return
    record, interfaces = describe(board)
    if code == 0x8005:
        connection.sendall(struct.pack(">HHII", 0x0111, 0x0005, 0, 1) + record
                           + b"".join(i + b"\0" for i in interfaces))
        return
    if exactly(32).rstrip(b"\0") != BUSID:
        connection.sendall(struct.pack(">HHI", 0x0111, 0x0003, 1))
        return
    board.reset()
    connection.sendall(struct.pack(">HHI", 0x0111, 0x0003, 0) + record)

    write = threading.Lock()
    pending = {}   # sequence number -> (endpoint, callback) of transfers waiting for a packet

    def returned(sequence, status, data=b"", length=None):
        with write:
            connection.sendall(struct.pack(">IIIIIiiiii8x", 3, sequence, 0, 0, 0, status,
                                           len(data) if length is None else length, 0, 0, 0) + data)

    while True:
        command, sequence, _, direction, endpoint = struct.unpack(">IIIII", exactly(20))
        if command == 2:   # unlink
            target, = struct.unpack(">I24x", exactly(28))
            waiting = pending.pop(target, None)
            unlinked = waiting is not None and board.forget(*waiting)
            with write:
                connection.sendall(struct.pack(">IIIIIi24x", 4, sequence, 0, 0, 0, -ECONNRESET if unlinked else 0))
            continue
        _, length, _, _, _, setup = struct.unpack(">IiiiI8s", exactly(28))
        data = exactly(length) if direction == 0 else b""
        if endpoint == 0:
            status, reply = board.transfer(setup, data)
            returned(sequence, status, reply, None if direction else len(data))
        elif direction:
            def arrived(packet, sequence=sequence):
                pending.pop(sequence, None)
                returned(sequence, 0, packet)
            pending[sequence] = (endpoint, arrived)
            board.take(endpoint, arrived)
        else:
            returned(sequence, -EPIPE)   # the device has no OUT endpoints


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", required=True, metavar="HOST:PORT", help="the emulator's USB port")
    parser.add_argument("--port", required=True, type=int, help="where to serve USB/IP")
    args = parser.parse_args()
    board = Board(args.board)
    listener = socket.create_server(("127.0.0.1", args.port))

    def connected(connection):
        try:
            serve(board, connection)
        except (ConnectionError, OSError):
            pass
        finally:
            connection.close()

    while True:
        connection, _ = listener.accept()
        connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=connected, args=(connection,), daemon=True).start()


if __name__ == "__main__":
    main()
