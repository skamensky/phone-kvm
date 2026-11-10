"""A debugging cable to a real phone: one end in the simulated board's USB port, the other a
HID device created inside the phone's own kernel (uhid) over adb.

This host has no USB port that can be a device, so the board cannot be plugged into a real
phone. Android's shell user may create HID devices, so the cable does that: it is the USB host
the board expects, and for each of the board's interfaces the phone gets a HID device with that
interface's own report descriptor, fed every report the firmware sends. The phone's requests
come back the same way: output reports (the keyboard's lights) and feature report reads.

What stays the board's own: the descriptors, the reports and their timing, and the phone's HID
drivers and input stack. What is not exercised: USB itself, and a phone with debugging off.
"""
import argparse
import json
import queue
import struct
import subprocess
import threading
from pathlib import Path

from rig.cable import Board

EVENT = 4376                      # sizeof(struct uhid_event): every write and read is one
CREATE2, DESTROY, OUTPUT, GET_REPORT, GET_REPORT_REPLY, INPUT2, SET_REPORT, SET_REPORT_REPLY = 11, 1, 6, 9, 10, 12, 13, 14
BUS_USB = 3
VENDOR, PRODUCT = 0x1209, 0x0001
# One file descriptor both ways: the reader in the background, the writer on adb's input.
# uhid takes one whole event a write: dd gathers what arrives into blocks of that size.
REMOTE = (f"exec 3<>/dev/uhid; dd bs={EVENT} <&3 2>/dev/null & "
          f"dd ibs={EVENT // 2} obs={EVENT} >&3 2>/dev/null; kill $!")


def get(board, request_type, request, value, index, length):
    status, data = board.transfer(struct.pack("<BBHHH", request_type, request, value, index, length))
    if status:
        raise SystemExit(f"the board refused USB request {request:#x} {value:#x}")
    return data


def enumerate_board(board):
    """Configure the board as a USB host would: [(interface, endpoint, report descriptor)].

    The cable's frames carry no device address, so none is assigned."""
    board.reset()
    head = get(board, 0x80, 6, 0x0200, 0, 9)
    configuration = get(board, 0x80, 6, 0x0200, 0, struct.unpack("<H", head[2:4])[0])
    interfaces, at = [], 0
    while at < len(configuration):
        kind = configuration[at + 1]
        if kind == 4:
            interfaces.append({"number": configuration[at + 2]})
        elif kind == 0x21:
            interfaces[-1]["length"] = struct.unpack("<H", configuration[at + 7:at + 9])[0]
        elif kind == 5:
            interfaces[-1]["endpoint"] = configuration[at + 2] & 0x7F
        at += configuration[at]
    status, _ = board.transfer(struct.pack("<BBHHH", 0x00, 9, configuration[5], 0, 0))
    if status:
        raise SystemExit("the board refused its configuration")
    return [(i["number"], i["endpoint"], get(board, 0x81, 6, 0x2200, i["number"], i["length"]))
            for i in interfaces]


class Device:
    """One of the board's interfaces as a HID device in the phone."""

    def __init__(self, adb, board, number, descriptor):
        self.board, self.number = board, number
        self.shell = subprocess.Popen([*adb, "shell", "-T", REMOTE], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.lock = threading.Lock()
        self.write(CREATE2, struct.pack("<128s64s64sHHIIII", f"phone-kvm interface {number}".encode(), b"", b"",
                                        len(descriptor), BUS_USB, VENDOR, PRODUCT, 0, 0) + descriptor)
        threading.Thread(target=self.requests, daemon=True).start()

    def write(self, kind, body):
        with self.lock:
            self.shell.stdin.write((struct.pack("<I", kind) + body).ljust(EVENT, b"\0"))
            self.shell.stdin.flush()

    def report(self, data):
        self.write(INPUT2, struct.pack("<H", len(data)) + data)

    def requests(self):
        """Pass the phone's requests on to the board, as control transfers on its USB port."""
        while True:
            event = self.shell.stdout.read(EVENT)
            if len(event) < EVENT:
                return
            kind, body = struct.unpack("<I", event[:4])[0], event[4:]
            if kind == OUTPUT:
                size, report_type = struct.unpack("<HB", body[4096:4099])
                self.board.transfer(struct.pack("<BBHHH", 0x21, 9, 2 << 8, self.number, size), body[:size])
            elif kind == GET_REPORT:
                request, report, report_type = struct.unpack("<IBB", body[:6])
                status, data = self.board.transfer(
                    struct.pack("<BBHHH", 0xA1, 1, (report_type + 1) << 8 | report, self.number, 64))
                self.write(GET_REPORT_REPLY, struct.pack("<IHH", request, 5 if status else 0, len(data)) + data)
            elif kind == SET_REPORT:
                request, report, report_type, size = struct.unpack("<IBBH", body[:8])
                status, _ = self.board.transfer(
                    struct.pack("<BBHHH", 0x21, 9, (report_type + 1) << 8 | report, self.number, size), body[8:8 + size])
                self.write(SET_REPORT_REPLY, struct.pack("<IH", request, 5 if status else 0))


def pump(board, endpoint, device):
    """Take the endpoint's reports from the board as fast as it sends them, as a host polling
    it would, and pass each on."""
    arrived = queue.Queue()
    while True:
        board.take(endpoint, arrived.put)
        device.report(arrived.get())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--board", required=True, metavar="HOST:PORT", help="the emulator's USB port")
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True, help="the phone, as adb names it")
    parser.add_argument("--status", required=True, type=Path, help="the JSON file written once the phone has the devices")
    args = parser.parse_args()

    board = Board(args.board, lambda: None)
    while not board.attached:   # until the board has pulled its USB port up
        threading.Event().wait(0.1)
    devices = []
    for number, endpoint, descriptor in enumerate_board(board):
        device = Device([args.adb, "-s", args.serial], board, number, descriptor)
        threading.Thread(target=pump, args=(board, endpoint, device), daemon=True).start()
        devices.append(device)
    args.status.write_text(json.dumps({"serial": args.serial, "interfaces": len(devices)}))
    for device in devices:
        device.shell.wait()
    raise SystemExit("the connection to the phone ended")


if __name__ == "__main__":
    main()
