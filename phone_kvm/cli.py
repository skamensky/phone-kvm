"""phonectl: operate the phone. Every command prints one JSON object; errors exit 1 with
{"error": ...}. Positions are pixels in the image `phonectl screenshot` writes."""
import argparse
import json
import sys

from . import hid, keymap, state
from .phone import Phone


def main(argv=None):
    parser = argparse.ArgumentParser(prog="phonectl", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="gadget, capture device and screen geometry")
    p = sub.add_parser("setup", help="choose the HDMI capture device")
    p.add_argument("--video", required=True, metavar="/dev/videoN")
    p = sub.add_parser("screenshot", help="write the phone's screen to a PNG")
    p.add_argument("path")
    p.add_argument("--settle-ms", type=int, default=2000,
                   help="wait up to this long for the screen to stop changing; 0 captures at once")
    sub.add_parser("calibrate", help="find the screen inside the HDMI frame again (after rotating)")
    p = sub.add_parser("tap")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--hold-ms", type=int, default=60, help="600 or more is a long press")
    p = sub.add_parser("swipe", help="drag one finger through two or more points (an unlock pattern "
                       "is one swipe through its dots); --hold-ms rests at the end (no fling)")
    p.add_argument("points", type=int, nargs="+", metavar="X Y")
    p.add_argument("--ms", type=int, default=300)
    p.add_argument("--hold-ms", type=int, default=0)
    p = sub.add_parser("pinch", help="two fingers around (x, y), from --start to --end pixels apart")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    p.add_argument("--ms", type=int, default=400)
    p = sub.add_parser("type", help="type text on the keyboard (US layout characters)")
    p.add_argument("text")
    p = sub.add_parser("key", help="press keys in turn: " + ", ".join([*hid.CONSUMER, *keymap.KEYS])
                       + ", or combinations such as ctrl+a, meta+n")
    p.add_argument("keys", nargs="+")
    p.add_argument("--hold-ms", type=int, default=0)
    p = sub.add_parser("mouse", help="mouse: to X Y | move DX DY | click [BUTTON] | scroll NOTCHES | calibrate")
    msub = p.add_subparsers(dest="action", required=True)
    m = msub.add_parser("to", help="put the cursor on a screenshot pixel")
    m.add_argument("x", type=int)
    m.add_argument("y", type=int)
    msub.add_parser("calibrate", help="measure cursor travel per mouse count (on a still screen)")
    m = msub.add_parser("move", help="move by mouse counts")
    m.add_argument("dx", type=int)
    m.add_argument("dy", type=int)
    m = msub.add_parser("click")
    m.add_argument("button", nargs="?", default="left", choices=list(Phone.BUTTONS))
    m = msub.add_parser("scroll")
    m.add_argument("notches", type=int)

    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (RuntimeError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}))
        return 1
    print(json.dumps(result))
    return 0


def run(args):
    if args.command == "setup":
        state.save("config", {"video": args.video})
        state.save("geometry", {})
        state.save("mouse", {})
        return {"video": args.video}
    phone = Phone()
    if args.command == "status":
        return phone.status()
    if args.command == "screenshot":
        width, height = phone.screenshot(args.path, args.settle_ms)
        return {"path": args.path, "width": width, "height": height}
    if args.command == "calibrate":
        phone._calibrate()
        width, height = phone.size()
        return {"width": width, "height": height, **state.load("geometry")}
    if args.command == "tap":
        phone.tap(args.x, args.y, args.hold_ms)
    elif args.command == "swipe":
        if len(args.points) < 4 or len(args.points) % 2:
            raise ValueError("swipe takes two or more points: X Y X Y [X Y ...]")
        phone.swipe(list(zip(args.points[::2], args.points[1::2])), args.ms, args.hold_ms)
    elif args.command == "pinch":
        phone.pinch(args.x, args.y, args.start, args.end, args.ms)
    elif args.command == "type":
        phone.type(args.text)
    elif args.command == "key":
        for key in args.keys:
            phone.key(key, args.hold_ms)
    elif args.command == "mouse":
        if args.action == "to":
            phone.mouse_to(args.x, args.y)
        elif args.action == "calibrate":
            return phone.mouse_calibrate()
        elif args.action == "move":
            phone.mouse_move(args.dx, args.dy)
        elif args.action == "click":
            phone.mouse_click(args.button)
        else:
            phone.mouse_scroll(args.notches)
    return {"ok": True}


if __name__ == "__main__":
    sys.exit(main())
