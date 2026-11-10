#!/usr/bin/env python3
"""Builds the KiCad project from hardware/circuit.py: schematic, placed and routed PCB.

  tools/generate.py

Writes hardware/phone-kvm.kicad_{pro,sch,pcb}. The schematic connects pins by net labels (one per
pin), so it is a faithful netlist rather than a drawing; the PCB places parts as PLACEMENT below,
routes it (see Router) and pours a ground plane on the bottom layer. UUIDs are derived from reference
designators, so regenerating an unchanged design gives identical files.

Needs KiCad's Python module (pcbnew); kicad-cli on PATH or KICAD10_{SYMBOL,FOOTPRINT}_DIR set.
"""
import collections
import heapq
import json
import math
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hardware"))
sys.path.insert(0, str(ROOT / "tools"))

import circuit  # noqa: E402
from library import library_dir, pins, symbol  # noqa: E402
from sexp import Str, dump  # noqa: E402

HW = ROOT / "hardware"
NAME = "phone-kvm"
FONT = ["effects", ["font", ["size", 1.27, 1.27]]]
HIDDEN = ["effects", ["font", ["size", 1.27, 1.27]], ["hide", "yes"]]

# The board, in mm from its top-left corner (y down, as KiCad draws it), and where each part
# goes: ref -> (x, y, rotation in degrees). Everything is on the top side.
#
# The module's antenna is at the top edge, over a strip with no copper on either layer. The
# USB-C plug to the phone leaves the bottom edge. The camera flex leaves the left edge.
ORIGIN = (100.0, 100.0)
SIZE = (32.0, 50.0)
PLACEMENT = {
    "U1": (20.5, 12.75, 0),
    # 3.3 V decoupling at the module's supply pin (pin 2, with ground on pin 1 above it).
    "C5": (9.0, 8.15, 90),
    "C15": (7.3, 8.15, 90),
    "C4": (5.4, 8.15, 90),
    # Camera connector: opening to the left edge, pin 1 at the top.
    "J3": (4.5, 19.75, 270),
    # Camera supplies below the connector. The 3.3 V track runs up the left edge past them.
    "U4": (3.5, 31.25, 0), "C8": (6.5, 31.25, 90),
    "C7": (3.0, 34.0, 0), "C9": (6.1, 34.0, 0),
    "U5": (3.5, 36.75, 0), "C11": (6.5, 36.75, 90),
    "C10": (3.0, 39.5, 0), "C12": (6.1, 39.5, 0),
    # Camera option jumpers, pull-ups and reset RC; the EN RC.
    "C13": (9.5, 30.3, 0), "JP3": (9.6, 32.6, 0), "JP1": (9.6, 35.4, 0), "JP2": (10.25, 38.2, 0),
    "R5": (17.6, 27.4, 0), "R6": (17.6, 29.1, 0), "R7": (17.6, 30.8, 0), "C14": (17.6, 32.5, 0),
    "R8": (17.6, 34.2, 0),
    # 3.3 V regulator in the bottom left corner, its fuse and capacitors beside it.
    "U3": (3.95, 45.1, 90),
    "C3": (8.6, 42.5, 90), "F1": (9.0, 46.9, 270),
    "C2": (11.0, 44.7, 90), "C1": (11.0, 47.9, 90),
    # USB-C at the bottom edge, its protection and CC resistors just above it.
    "J1": (17.55, 46.1, 0),
    "U2": (17.55, 37.3, 270),
    "R3": (14.3, 39.8, 0), "R4": (21.0, 39.8, 0),
    # Buttons, console header, LED.
    "SW1": (23.5, 29.7, 0), "SW2": (23.5, 35.7, 0),
    "J2": (29.9, 28.1, 0),
    "R1": (24.7, 39.6, 0), "C6": (24.7, 41.3, 0),
    "R2": (25.5, 44.5, 0), "D1": (29.5, 44.5, 0),
}

# Net classes: (track width, clearance) in mm, and the nets in each non-default class.
CLASSES = {"Default": (0.2, 0.15), "Power": (0.5, 0.2)}
CLASS_OF = {"VBUS": "Power", "5V": "Power", "3V3": "Power"}
VIA = (0.6, 0.3)  # diameter, drill
MIN_DRILL = 0.3
# Footprints kept in the project's own library (name -> the KiCad library they come from):
# the module's ground pad has 0.2 mm thermal vias, smaller than the fab's smallest hole.
ADAPTED_FOOTPRINTS = {"ESP32-S3-WROOM-1": "RF_Module"}
SMALL_DRILL = 0.2
EDGE_CLEARANCE = 0.3
HOLE_CLEARANCE = 0.25
PLANE_CLEARANCE = 0.3
PLANE_MIN_WIDTH = 0.25

# Routing (see Router). Everything keeps CLEARANCE, the larger of the classes' clearances.
GRID = 0.1
CLEARANCE = 0.2
MARGIN = 0.02        # spare distance for what the lattice cannot see between its nodes
VIA_TO_PAD = 0.1     # a via stays this far from any pad, so solder does not drain into it
REACH = 0.7          # the copper maps record distances up to here
STEP_COST = (10, 12)       # per grid step on the top and the bottom layer
DIAGONAL_COST = (14, 17)
TURN_COST = 4
VIA_COST = 300
# Nets routed before the rest, in this order; the rest follow, shortest first.
ROUTE_FIRST = ["USB_DP", "USB_DM", "3V3", "VBUS"]
# When a connection has no free way, the tracks in its way are ripped up and routed again.
# RIP_COST is what passing a node held by another net costs when choosing whom to rip up
# (more for nets ripped up before); MAX_RIPS bounds the whole run.
RIP_COST = 100
MAX_RIPS = 400
PLANE_ROUNDS = 10


def uid(*key):
    return Str(uuid.uuid5(uuid.NAMESPACE_URL, "phone-kvm/" + "/".join(key)))


def resolve(ref_pin):
    """'U1.IO4' -> ('U1', ['4']): a pin by number, or every pin with that name."""
    ref, pin = ref_pin.split(".", 1)
    lib_pins = pins(circuit.PARTS[ref][0])
    numbers = [p[0] for p in lib_pins if p[0] == pin] or [p[0] for p in lib_pins if p[1] == pin]
    if not numbers:
        sys.exit(f"{ref_pin}: no such pin")
    return ref, numbers


def connections():
    """{(ref, pin number): net} for every connected pin, and the set of no-connect pins."""
    net_of = {}
    for net, members in circuit.NETS.items():
        for m in members:
            ref, numbers = resolve(m)
            for n in numbers:
                if (ref, n) in net_of:
                    sys.exit(f"{ref}.{n} is in both {net_of[ref, n]} and {net}")
                net_of[ref, n] = net
    nc = {(ref, n) for m in circuit.NO_CONNECT for ref, ns in [resolve(m)] for n in ns}
    for ref, (lib_id, *_rest) in circuit.PARTS.items():
        for number, name, *_ in pins(lib_id):
            if (ref, number) not in net_of and (ref, number) not in nc:
                sys.exit(f"{ref}.{number} ({name}) is neither connected nor marked no-connect")
            if (ref, number) in net_of and (ref, number) in nc:
                sys.exit(f"{ref}.{number} ({name}) is both connected and marked no-connect")
    return net_of, nc


def schematic_layout():
    """Schematic position of every symbol: the three big parts on the left, the rest on a grid."""
    pos = {"J1": (50.8, 63.5), "J3": (50.8, 177.8), "U1": (127.0, 127.0)}
    small = [r for r in circuit.PARTS if r not in pos]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, ref in enumerate(sorted(small, key=lambda r: (r.lstrip("#").rstrip("0123456789"), int(r.lstrip("#").lstrip(letters))))):
        pos[ref] = (203.2 + 25.4 * (i % 8), 50.8 + 30.48 * (i // 8))
    return pos


def write_schematic(net_of, nc):
    root = uid("schematic")
    items = [
        "kicad_sch", ["version", 20250114], ["generator", Str("eeschema")],
        ["generator_version", Str("9.0")], ["uuid", root], ["paper", Str("A3")],
        ["title_block", ["title", Str("phone-kvm")], ["comment", 1, Str("Generated by tools/generate.py from hardware/circuit.py")]],
        ["lib_symbols", *[symbol(lib) for lib in sorted({p[0] for p in circuit.PARTS.values()})]],
    ]
    pos = schematic_layout()
    placed = set()
    for ref, (lib_id, value, footprint, lcsc) in circuit.PARTS.items():
        x, y = pos[ref]
        props = [("Reference", ref, (x, y - 6.35), False), ("Value", value, (x, y + 6.35), False),
                 ("Footprint", footprint or "", (x, y), True), ("Datasheet", "", (x, y), True)]
        if lcsc:
            props.append(("LCSC", lcsc, (x, y), True))
        items.append([
            "symbol", ["lib_id", Str(lib_id)], ["at", x, y, 0], ["unit", 1],
            ["exclude_from_sim", "no"], ["in_bom", "yes" if footprint and ref not in circuit.COPPER_ONLY else "no"],
            ["on_board", "yes" if footprint else "no"],
            ["dnp", "yes" if ref in circuit.NOT_ASSEMBLED else "no"], ["uuid", uid("sym", ref)],
            *[["property", Str(k), Str(v), ["at", px, py, 0], HIDDEN if hide else FONT] for k, v, (px, py), hide in props],
            *[["pin", Str(p[0]), ["uuid", uid("pin", ref, p[0])]] for p in pins(lib_id)],
            ["instances", ["project", Str(NAME), ["path", Str(f"/{root}"), ["reference", Str(ref)], ["unit", 1]]]],
        ])
        for number, _name, px, py, _type in pins(lib_id):
            at = (round(x + px, 2), round(y - py, 2))
            if at in placed:
                continue  # stacked pins share one endpoint
            placed.add(at)
            if (ref, number) in nc:
                items.append(["no_connect", ["at", *at], ["uuid", uid("nc", ref, number)]])
                continue
            left = px < 0 or (px == 0 and py > 0)
            angle = 180 if left else 0
            items.append(["label", Str(net_of[ref, number]), ["at", *at, angle],
                          ["effects", ["font", ["size", 1.27, 1.27]], ["justify", "right" if left else "left", "bottom"]],
                          ["uuid", uid("label", ref, number)]])
    items.append(["sheet_instances", ["path", Str("/"), ["page", Str("1")]]])
    items.append(["embedded_fonts", "no"])
    (HW / f"{NAME}.kicad_sch").write_text(dump(items) + "\n")


def write_footprints():
    """The project's own footprint library: KiCad footprints with the fab's smallest hole."""
    folder = HW / f"{NAME}.pretty"
    folder.mkdir(exist_ok=True)
    for name, lib in sorted(ADAPTED_FOOTPRINTS.items()):
        text = (library_dir("FOOTPRINT") / f"{lib}.pretty" / f"{name}.kicad_mod").read_text()
        small = f"(drill {SMALL_DRILL:g})"
        if small not in text:
            raise SystemExit(f"{lib}:{name} no longer has {SMALL_DRILL} mm holes: look at it again")
        (folder / f"{name}.kicad_mod").write_text(text.replace(small, f"(drill {MIN_DRILL:g})"))


def write_lib_tables():
    """Project library tables naming exactly the libraries the design uses."""
    syms = sorted({p[0].split(":")[0] for p in circuit.PARTS.values()})
    fps = sorted({p[2].split(":")[0] for p in circuit.PARTS.values() if p[2]})
    (HW / "sym-lib-table").write_text("(sym_lib_table\n  (version 7)\n" + "".join(
        f'  (lib (name "{n}")(type "KiCad")(uri "${{KICAD10_SYMBOL_DIR}}/{n}.kicad_sym")(options "")(descr ""))\n'
        for n in syms) + ")\n")
    (HW / "fp-lib-table").write_text("(fp_lib_table\n  (version 7)\n" + "".join(
        f'  (lib (name "{n}")(type "KiCad")(uri "{"${KIPRJMOD}" if n == NAME else "${KICAD10_FOOTPRINT_DIR}"}/{n}.pretty")(options "")(descr ""))\n'
        for n in fps) + ")\n")


def write_project():
    """Net classes and the fab's minimums (JLCPCB standard 2-layer, 1 oz copper)."""
    def net_class(name, width, clearance):
        return {
            "name": name, "clearance": clearance, "track_width": width, "via_diameter": VIA[0], "via_drill": VIA[1],
            "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
            "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12, "line_style": 0,
            "priority": 2147483647 if name == "Default" else 0,
            "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
        }

    project = {
        "board": {"design_settings": {
            "defaults": {"board_outline_line_width": 0.1, "copper_line_width": 0.2},
            "rules": {
                "min_clearance": 0.127, "min_track_width": 0.127, "min_via_diameter": 0.45,
                "min_via_annular_width": 0.13, "min_through_hole_diameter": 0.3, "min_hole_to_hole": 0.25,
                "min_hole_clearance": 0.25, "min_copper_edge_clearance": 0.3, "min_silk_clearance": 0.0,
                "min_text_height": 0.8, "min_text_thickness": 0.15, "min_connection": 0.0,
            },
            "track_widths": [0.0, *sorted({w for w, _ in CLASSES.values()})],
            "via_dimensions": [{"diameter": 0.0, "drill": 0.0}, {"diameter": VIA[0], "drill": VIA[1]}],
        }},
        "net_settings": {
            "classes": [net_class(name, *rules) for name, rules in CLASSES.items()],
            "meta": {"version": 4},
            "netclass_patterns": [{"netclass": cls, "pattern": f"/{net}"} for net, cls in sorted(CLASS_OF.items())],
        },
        "meta": {"filename": f"{NAME}.kicad_pro", "version": 3},
    }
    (HW / f"{NAME}.kicad_pro").write_text(json.dumps(project, indent=2) + "\n")


def write_pcb(net_of, nc):
    import pcbnew

    # New items take their UUIDs from KiCad's generator; seeded, it repeats run after run.
    pcbnew.KIID.SeedGenerator(1)
    names = {(ref, p[0]): p[1] for ref, part in circuit.PARTS.items() for p in pins(part[0])}

    mm = pcbnew.FromMM
    ox, oy = ORIGIN
    path = HW / f"{NAME}.kicad_pcb"
    path.write_text('(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0"))\n')
    board = pcbnew.LoadBoard(str(path))
    nets = {}
    for net in sorted(set(net_of.values())):
        # Local labels on the root sheet name their nets "/NAME".
        nets[net] = pcbnew.NETINFO_ITEM(board, f"/{net}")
        board.Add(nets[net])

    fp_dir = library_dir("FOOTPRINT")
    for ref, (lib_id, value, footprint, lcsc) in circuit.PARTS.items():
        if not footprint:
            continue
        lib, name = footprint.split(":")
        fp = pcbnew.FootprintLoad(str((HW if lib == NAME else fp_dir) / f"{lib}.pretty"), name)
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(ref)
        fp.SetValue(value)
        if lcsc:
            fp.SetField("LCSC", lcsc)
            fp.GetField("LCSC").SetVisible(False)
            fp.GetField("LCSC").SetLayer(pcbnew.F_Fab)
        fp.SetDNP(ref in circuit.NOT_ASSEMBLED)
        fp.SetPath(pcbnew.KIID_PATH(f"/{uid('sym', ref)}"))
        fp.SetSheetname("Root")
        fp.SetSheetfile(f"{NAME}.kicad_sch")
        x, y, rot = PLACEMENT[ref]
        fp.SetPosition(pcbnew.VECTOR2I(mm(ox + x), mm(oy + y)))
        fp.SetOrientationDegrees(rot)
        for pad in fp.Pads():
            key = (ref, pad.GetNumber())
            if key in net_of:
                pad.SetNet(nets[net_of[key]])
            elif key in nc:
                # The name eeschema gives the single-pin net of a no-connect pin.
                pin = names[key].replace("/", "{slash}")
                nc_net = pcbnew.NETINFO_ITEM(board, f"unconnected-({ref}-{pin}-Pad{key[1]})")
                board.Add(nc_net)
                pad.SetNet(nc_net)
        # Reference designators go on the fab layer: the parts are too close for silkscreen
        # labels, and assembly works from the placement file.
        fp.Reference().SetLayer(pcbnew.F_Fab)
        board.Add(fp)

    # The camera connector is a top-contact part: its pad N is the camera module's pin N only
    # with the opening to the left and pad 1 at the top.
    j3 = {p.GetNumber(): p.GetPosition() for p in board.FindFootprintByReference("J3").Pads()}
    assert j3["1"].y < j3["24"].y and j3["MP"].x < j3["1"].x, "J3 is the wrong way round"

    w, h = SIZE
    edge = pcbnew.PCB_SHAPE(board)
    edge.SetShape(pcbnew.SHAPE_T_RECT)
    edge.SetStart(pcbnew.VECTOR2I(mm(ox), mm(oy)))
    edge.SetEnd(pcbnew.VECTOR2I(mm(ox + w), mm(oy + h)))
    edge.SetLayer(pcbnew.Edge_Cuts)
    edge.SetWidth(mm(0.1))
    board.Add(edge)

    # Gerbers, drill files and the placement file all measure from the bottom-left corner.
    board.GetDesignSettings().SetAuxOrigin(pcbnew.VECTOR2I(mm(ox), mm(oy + h)))

    text = pcbnew.PCB_TEXT(board)
    text.SetText("phone-kvm")
    text.SetLayer(pcbnew.B_SilkS)
    text.SetMirrored(True)
    text.SetTextSize(pcbnew.VECTOR2I(mm(1.0), mm(1.0)))
    text.SetTextThickness(mm(0.15))
    text.SetPosition(pcbnew.VECTOR2I(mm(ox + w / 2), mm(oy + h - 2)))
    board.Add(text)

    router = Router(board)
    router.route_all()
    layer_of = (pcbnew.F_Cu, pcbnew.B_Cu)
    for layer, ax, ay, bx, by, width, net, _unit in router.tracks:
        track = pcbnew.PCB_TRACK(board)
        track.SetStart(pcbnew.VECTOR2I(mm(ox + ax), mm(oy + ay)))
        track.SetEnd(pcbnew.VECTOR2I(mm(ox + bx), mm(oy + by)))
        track.SetWidth(mm(width))
        track.SetLayer(layer_of[layer])
        track.SetNet(nets[net])
        board.Add(track)
    for x, y, net, _unit in router.vias:
        via = pcbnew.PCB_VIA(board)
        via.SetViaType(pcbnew.VIATYPE_THROUGH)
        via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        via.SetPosition(pcbnew.VECTOR2I(mm(ox + x), mm(oy + y)))
        via.SetWidth(pcbnew.PADSTACK.ALL_LAYERS, mm(VIA[0]))
        via.SetDrill(mm(VIA[1]))
        via.SetNet(nets[net])
        board.Add(via)

    # The bottom layer is the ground plane. The only pads on it are through-hole ones, which
    # join it solidly. The top layer has no pour: every ground pad there has its own via.
    zone = pcbnew.ZONE(board)
    zone.SetLayer(pcbnew.B_Cu)
    zone.SetNet(nets["GND"])
    zone.SetLocalClearance(mm(PLANE_CLEARANCE))
    zone.SetMinThickness(mm(PLANE_MIN_WIDTH))
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    outline = zone.Outline()
    outline.NewOutline()
    for px, py in ((0, 0), (w, 0), (w, h), (0, h)):
        outline.Append(mm(ox + px), mm(oy + py))
    board.Add(zone)
    pcbnew.ZONE_FILLER(board).Fill(board.Zones())
    pcbnew.SaveBoard(str(path), board)
    if router.failed:
        sys.exit("\n".join(router.failed))


class Router:
    """A two-layer grid router.

    Copper is kept as two distance maps per layer on a GRID-pitch lattice: the distance from each
    node to the nearest copper, whose net it is, and the distance to the nearest copper of any
    other net. A track of some net may pass a node when the other nets' copper is at least half
    its width plus the clearance away. Each net is grown as a tree: an A* search (45 degree
    steps, turns of at most 45 degrees, a via to change layer) from everything already connected
    to the nearest pad not yet connected. Ground comes first: every ground pad on the top layer
    gets a short track to a via into the bottom plane, or to a ground pad that has one.

    The work is done in units: a net, or one ground pad's via. A unit with no free way finds
    the cheapest way through other units' copper, rips those units up and takes it; they are
    routed again straight after. At the end, ground vias that the bottom tracks have cut off
    from the rest of the plane are moved to where the plane is in one piece.
    """

    def __init__(self, board):
        import pcbnew

        mm = pcbnew.ToMM
        ox, oy = ORIGIN
        self.nx, self.ny = round(SIZE[0] / GRID) + 1, round(SIZE[1] / GRID) + 1
        n = self.nx * self.ny
        self.near = [[REACH] * n for _ in range(2)]     # distance to the nearest copper
        self.owner = [[None] * n for _ in range(2)]     # its net
        self.second = [[REACH] * n for _ in range(2)]   # distance to the nearest copper of another net
        self.pad_near = [REACH] * n                     # distance to the nearest pad, either layer
        self.limit = [0.0] * n                          # room before the edge, a hole or the keep-out
        self.tracks, self.vias = [], []                 # (layer, ax, ay, bx, by, width, net, unit), (x, y, net, unit)
        self.unit = None                                # the unit whose copper is being laid
        self.queue = collections.deque()                # units still to route
        self.rips = collections.Counter()               # net -> times its copper was ripped up
        self.ripped = 0
        self.failed = []                                # what could not be routed
        self.pads = {}                                  # net -> [(x, y, half width, half height, layers, ref)]

        holes, keepouts = [], []
        for fp in board.Footprints():
            for zone in fp.Zones():
                if zone.GetIsRuleArea():
                    box = zone.GetBoundingBox()
                    keepouts.append((mm(box.GetLeft()) - ox, mm(box.GetTop()) - oy, mm(box.GetRight()) - ox, mm(box.GetBottom()) - oy))
            for pad in fp.Pads():
                box = pad.GetBoundingBox()
                x, y = mm(pad.GetPosition().x) - ox, mm(pad.GetPosition().y) - oy
                rect = (mm(box.GetLeft()) - ox, mm(box.GetTop()) - oy, mm(box.GetRight()) - ox, mm(box.GetBottom()) - oy)
                if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    holes.append((x, y, mm(pad.GetDrillSizeX()) / 2))
                    continue
                layers = tuple(k for k, layer in enumerate((pcbnew.F_Cu, pcbnew.B_Cu)) if pad.IsOnLayer(layer))
                if not layers:
                    continue  # a paste-only aperture
                name = pad.GetNetname()
                net = name[1:] if name.startswith("/") else f"{fp.GetReference()}.{pad.GetNumber()} (no net)"
                self.pads.setdefault(net, []).append((x, y, (rect[2] - rect[0]) / 2, (rect[3] - rect[1]) / 2, layers, fp.GetReference()))
                for layer in layers:
                    self.add_rect(layer, net, rect)
                self.add_rect(None, None, rect)
            for item in fp.GraphicalItems():
                # Copper drawn in a footprint: the bridge of a closed solder jumper.
                if item.GetLayer() == pcbnew.F_Cu:
                    box = item.GetBoundingBox()
                    self.add_rect(0, f"{fp.GetReference()}.copper",
                                  (mm(box.GetLeft()) - ox, mm(box.GetTop()) - oy, mm(box.GetRight()) - ox, mm(box.GetBottom()) - oy))
        for i in range(self.nx):
            for j in range(self.ny):
                x, y = i * GRID, j * GRID
                room = min(x, y, SIZE[0] - x, SIZE[1] - y) - EDGE_CLEARANCE
                for hx, hy, r in holes:
                    room = min(room, math.hypot(x - hx, y - hy) - r - HOLE_CLEARANCE)
                for x0, y0, x1, y1 in keepouts:
                    room = min(room, math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1)))
                self.limit[i * self.ny + j] = room
        self.fixed = [[list(m) for m in maps] for maps in (self.near, self.owner, self.second)]

    # --- the copper maps ---

    def window(self, x0, y0, x1, y1):
        i0, i1 = max(0, math.floor((x0 - REACH) / GRID)), min(self.nx - 1, math.ceil((x1 + REACH) / GRID))
        j0, j1 = max(0, math.floor((y0 - REACH) / GRID)), min(self.ny - 1, math.ceil((y1 + REACH) / GRID))
        return ((i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1))

    def mark(self, layer, net, p, d):
        if layer is None:
            self.pad_near[p] = min(self.pad_near[p], d)
            return
        near, owner, second = self.near[layer], self.owner[layer], self.second[layer]
        if owner[p] == net:
            near[p] = min(near[p], d)
        elif d < near[p]:
            second[p], near[p], owner[p] = near[p], d, net
        else:
            second[p] = min(second[p], d)

    def add_rect(self, layer, net, rect):
        x0, y0, x1, y1 = rect
        for i, j in self.window(x0, y0, x1, y1):
            x, y = i * GRID, j * GRID
            d = math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1))
            if d < REACH:
                self.mark(layer, net, i * self.ny + j, d)

    def add_segment(self, layer, net, ax, ay, bx, by, radius):
        """A track, or with both ends the same a via's pad."""
        length = math.hypot(bx - ax, by - ay)
        pieces = max(1, math.ceil(length))
        for k in range(pieces):
            px, py = ax + (bx - ax) * k / pieces, ay + (by - ay) * k / pieces
            qx, qy = ax + (bx - ax) * (k + 1) / pieces, ay + (by - ay) * (k + 1) / pieces
            ux, uy, l2 = qx - px, qy - py, (qx - px) ** 2 + (qy - py) ** 2
            for i, j in self.window(min(px, qx) - radius, min(py, qy) - radius, max(px, qx) + radius, max(py, qy) + radius):
                x, y = i * GRID, j * GRID
                t = max(0.0, min(1.0, ((x - px) * ux + (y - py) * uy) / l2)) if l2 else 0.0
                d = max(0.0, math.hypot(x - px - t * ux, y - py - t * uy) - radius)
                if d < REACH:
                    self.mark(layer, net, i * self.ny + j, d)

    def others(self, layer, net, p):
        return self.second[layer][p] if self.owner[layer][p] == net else self.near[layer][p]

    def fixed_others(self, layer, net, p):
        """As others, counting only the pads: what would be left with every track ripped up."""
        near, owner, second = self.fixed
        return second[layer][p] if owner[layer][p] == net else near[layer][p]

    def rebuild(self):
        """Draws the copper maps afresh, after tracks were removed."""
        self.near, self.owner, self.second = ([list(m) for m in maps] for maps in self.fixed)
        for layer, ax, ay, bx, by, width, net, _unit in self.tracks:
            self.add_segment(layer, net, ax, ay, bx, by, width / 2)
        for x, y, net, _unit in self.vias:
            for layer in (0, 1):
                self.add_segment(layer, net, x, y, x, y, VIA[0] / 2)

    def add_track(self, layer, net, ax, ay, bx, by, width):
        if (ax, ay) != (bx, by):
            self.tracks.append((layer, round(ax, 4), round(ay, 4), round(bx, 4), round(by, 4), width, net, self.unit))
            self.add_segment(layer, net, ax, ay, bx, by, width / 2)

    def add_via(self, net, x, y):
        self.vias.append((round(x, 4), round(y, 4), net, self.unit))
        for layer in (0, 1):
            self.add_segment(layer, net, x, y, x, y, VIA[0] / 2)

    # --- searching ---

    def pad_nodes(self, pad, width=CLASSES["Default"][0]):
        """The nodes far enough inside a pad for a track to end there without sticking out of
        its sides; else the node nearest its centre."""
        x, y, hw, hh = pad[:4]
        inset = max(0.1, 0.6 * min(hw, hh), width / 2)
        i0, i1 = math.ceil((x - hw + inset) / GRID - 1e-6), math.floor((x + hw - inset) / GRID + 1e-6)
        j0, j1 = math.ceil((y - hh + inset) / GRID - 1e-6), math.floor((y + hh - inset) / GRID + 1e-6)
        if i0 > i1:
            i0 = i1 = round(x / GRID)
        if j0 > j1:
            j0 = j1 = round(y / GRID)
        return [i * self.ny + j for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    def search(self, net, sources, targets, width, via_sites=None, soft=False):
        """The cheapest path from any source node to any target node, as [(layer, node)].

        sources, targets: {(layer, node)}. With via_sites, a predicate on nodes, the path may
        also end wherever a via fits, and the search stays on its starting layer. With soft,
        nodes held only by other nets' tracks and vias may be passed, at a price.
        """
        nx, ny, n = self.nx, self.ny, self.nx * self.ny
        need = width / 2 + CLEARANCE + MARGIN
        via_need = VIA[0] / 2 + CLEARANCE + MARGIN

        free = [bytearray(n), bytearray(n)]  # 0 unknown, 1 free, 2 blocked, 3 free once tracks are ripped up
        via_free = bytearray(n)

        def is_free(layer, p):
            state = free[layer][p]
            if not state:
                if self.limit[p] < width / 2 + MARGIN:
                    state = 2
                elif self.others(layer, net, p) >= need:
                    state = 1
                else:
                    state = 3 if soft and self.fixed_others(layer, net, p) >= need else 2
                free[layer][p] = state
            return state

        def via_fits(p):
            state = via_free[p]
            if not state:
                if self.limit[p] < VIA[0] / 2 + MARGIN or self.pad_near[p] < VIA[0] / 2 + VIA_TO_PAD:
                    state = 2
                elif self.others(0, net, p) >= via_need and self.others(1, net, p) >= via_need:
                    state = 1
                else:
                    state = 3 if soft and min(self.fixed_others(0, net, p), self.fixed_others(1, net, p)) >= via_need else 2
                via_free[p] = state
            return state

        def toll(layer, p):
            return RIP_COST * (1 + self.rips[self.owner[layer][p]])

        # For the estimate of what remains: one box per target pad (its nodes are adjacent).
        boxes = []
        if via_sites is None:
            node_set, seen = {p for _layer, p in targets}, set()
            for p in sorted(node_set):
                if p in seen:
                    continue
                group, stack = [], [p]
                seen.add(p)
                while stack:
                    q = stack.pop()
                    group.append(q)
                    for r in (q - 1, q + 1, q - ny, q + ny):
                        if r in node_set and r not in seen:
                            seen.add(r)
                            stack.append(r)
                boxes.append((min(q // ny for q in group), min(q % ny for q in group),
                              max(q // ny for q in group), max(q % ny for q in group)))
        estimate = {}

        def remaining(p):
            if not boxes:
                return 0
            if p not in estimate:
                i, j = divmod(p, ny)
                best = None
                for i0, j0, i1, j1 in boxes:
                    dx, dy = max(i0 - i, 0, i - i1), max(j0 - j, 0, j - j1)
                    d = 10 * max(dx, dy) + 4 * min(dx, dy)
                    best = d if best is None or d < best else best
                estimate[p] = best
            return estimate[p]

        steps = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
        offsets = [dx * ny + dy for dx, dy in steps]
        best, parent, heap = {}, {}, []
        for layer, p in sorted(sources):
            state = (layer * n + p) * 9 + 8
            best[state] = 0
            heap.append((remaining(p), 0, state))
        heapq.heapify(heap)
        while heap:
            _f, cost, state = heapq.heappop(heap)
            if cost > best[state]:
                continue
            rest, heading = divmod(state, 9)
            layer, p = divmod(rest, n)
            done = (layer, p) in targets or (via_sites is not None and via_sites(p) and via_fits(p) != 2)
            if done:
                path = [(layer, p)]
                while state in parent:
                    state = parent[state]
                    rest = state // 9
                    if (rest // n, rest % n) != path[-1]:
                        path.append((rest // n, rest % n))
                return path[::-1]
            moves = range(8) if heading == 8 else ((heading - 1) % 8, heading, (heading + 1) % 8)
            for h in moves:
                q = p + offsets[h]
                held = is_free(layer, q)
                if held == 2:
                    continue
                dx, dy = steps[h]
                if dx and dy and (is_free(layer, p + dx * ny) == 2 or is_free(layer, p + dy) == 2):
                    continue
                step = (STEP_COST[layer] if not (dx and dy) else DIAGONAL_COST[layer]) + (TURN_COST if heading not in (8, h) else 0)
                if held == 3:
                    step += toll(layer, q)
                nxt = (layer * n + q) * 9 + h
                if cost + step < best.get(nxt, 1 << 60):
                    best[nxt] = cost + step
                    parent[nxt] = state
                    heapq.heappush(heap, (cost + step + remaining(q), cost + step, nxt))
            if via_sites is None and via_fits(p) != 2:
                step = VIA_COST + (5 * toll(1 - layer, p) if via_fits(p) == 3 else 0)
                nxt = ((1 - layer) * n + p) * 9 + 8
                if cost + step < best.get(nxt, 1 << 60):
                    best[nxt] = cost + step
                    parent[nxt] = state
                    heapq.heappush(heap, (cost + step + remaining(p), cost + step, nxt))
        return None

    def lay(self, net, path, width):
        """Adds a path's tracks and vias; returns the nodes it covers."""
        ny = self.ny
        start = 0
        for k in range(1, len(path) + 1):
            last = k == len(path)
            turn = not last and k - start >= 2 and (path[k][1] - path[k - 1][1] != path[k - 1][1] - path[k - 2][1])
            hop = not last and path[k][0] != path[k - 1][0]
            if last or turn or hop:
                (layer, a), (_, b) = path[start], path[k - 1]
                self.add_track(layer, net, a // ny * GRID, a % ny * GRID, b // ny * GRID, b % ny * GRID, width)
                start = k - 1
            if hop:
                p = path[k][1]
                self.add_via(net, p // ny * GRID, p % ny * GRID)
                start = k
        covered = set(path)
        for (la, a), (lb, b) in zip(path, path[1:]):
            if la != lb:
                covered |= {(0, a), (1, a)}
        return covered

    def in_the_way(self, net, path, width, via_at_end):
        """The units whose copper is too close to a path for it to be laid."""
        ny = self.ny
        need, via_need = width / 2 + CLEARANCE + MARGIN, VIA[0] / 2 + CLEARANCE + MARGIN
        points = [[], []]  # per layer: (x, y, room needed around it)
        for k, (layer, p) in enumerate(path):
            x, y = p // ny * GRID, p % ny * GRID
            points[layer].append((x, y, need))
            if k and path[k - 1][0] == layer and abs(path[k - 1][1] - p) not in (1, ny):
                # A diagonal step also needs the two nodes beside it free.
                q = path[k - 1][1]
                points[layer].append((q // ny * GRID, y, need))
                points[layer].append((x, q % ny * GRID, need))
            if path[k + 1][0] != layer if k + 1 < len(path) else via_at_end:
                points[0].append((x, y, via_need))
                points[1].append((x, y, via_need))
        units = set()
        for layer, ax, ay, bx, by, w, other, unit in self.tracks:
            if other == net or unit in units:
                continue
            ux, uy = bx - ax, by - ay
            l2 = ux * ux + uy * uy
            for x, y, room in points[layer]:
                t = max(0.0, min(1.0, ((x - ax) * ux + (y - ay) * uy) / l2))
                if math.hypot(x - ax - t * ux, y - ay - t * uy) - w / 2 < room:
                    units.add(unit)
                    break
        for vx, vy, other, unit in self.vias:
            if other != net and unit not in units and any(
                    math.hypot(x - vx, y - vy) - VIA[0] / 2 < room for layer in points for x, y, room in layer):
                units.add(unit)
        return sorted(units)

    def rip(self, unit):
        """Removes a unit's copper and queues it again, with the ground pads that hang on it."""
        self.tracks = [t for t in self.tracks if t[7] != unit]
        self.vias = [v for v in self.vias if v[3] != unit]
        self.rips["GND" if unit in self.ground else unit] += 1
        if self.grounded.pop(unit, None):
            for other in [o for o, to in self.joined.items() if to == unit]:
                self.rip(other)
        self.joined.pop(unit, None)
        if unit not in self.queue:
            self.queue.appendleft(unit)

    def connect(self, net, sources, targets, width, via_sites=None):
        """A path for the current unit, ripping up other units when there is no free way."""
        while True:
            path = self.search(net, sources, targets, width, via_sites)
            if path is not None or self.ripped >= MAX_RIPS:
                return path
            wanted = self.search(net, sources, targets, width, via_sites, soft=True)
            units = wanted and self.in_the_way(net, wanted, width, wanted[-1] not in targets)
            if not units:
                return None
            for unit in units:
                self.ripped += 1
                self.rip(unit)
            self.rebuild()

    def route_net(self, net):
        width = CLASSES[CLASS_OF.get(net, "Default")][0]
        pads = self.pads[net]
        regions = [{(layer, p) for layer in pad[4] for p in self.pad_nodes(pad, width)} for pad in pads]
        centre = {node: pad for pad, region in zip(pads, regions) for node in region}
        tree, todo = set(regions[0]), list(range(1, len(pads)))
        while todo:
            targets = {node: k for k in todo for node in regions[k]}
            path = self.connect(net, tree, set(targets), width)
            if path is None:
                names = ", ".join(f"({pads[k][0]:.2f}, {pads[k][1]:.2f})" for k in todo)
                self.failed.append(f"cannot route {net}: no way to the pads at {names}")
                self.tracks = [t for t in self.tracks if t[7] != net]
                self.vias = [v for v in self.vias if v[3] != net]
                self.rebuild()
                return
            for layer, p in (path[0], path[-1]):
                # Carry the track from the node where it met the pad to the pad's centre.
                if (layer, p) in centre:
                    pad = centre[layer, p]
                    self.add_track(layer, net, pad[0], pad[1], p // self.ny * GRID, p % self.ny * GRID, width)
            tree |= self.lay(net, path, width)
            k = targets[path[-1]]
            tree |= regions[k]
            todo.remove(k)

    def plane(self):
        """The nodes of the largest connected piece of the bottom ground plane."""
        nx, ny = self.nx, self.ny
        room = PLANE_CLEARANCE + PLANE_MIN_WIDTH / 2 + MARGIN
        inside = bytearray(1 if self.others(1, "GND", p) >= room and self.limit[p] >= PLANE_MIN_WIDTH / 2 else 0
                           for p in range(nx * ny))
        largest = set()
        for p in range(nx * ny):
            if inside[p] != 1:
                continue
            piece, stack = set(), [p]
            inside[p] = 2
            while stack:
                q = stack.pop()
                piece.add(q)
                for r in (q - 1, q + 1, q - ny, q + ny):
                    if 0 <= r < nx * ny and inside[r] == 1:
                        inside[r] = 2
                        stack.append(r)
            if len(piece) > len(largest):
                largest = piece
        return largest

    def route_ground(self, unit):
        """Gives a top-layer ground pad a track to a via, or to a ground pad that has one.

        Once self.whole is set, vias go only there: where the bottom plane is in one piece.
        """
        width = CLASSES["Default"][0]
        pad = self.ground[unit]
        region = {(0, p) for p in self.pad_nodes(pad)}
        done = {node: other for other, nodes in self.grounded.items() for node in nodes}
        vias = [(x, y, other) for x, y, net, other in self.vias if net == "GND"]
        for x, y, other in vias:
            done[0, round(x / GRID) * self.ny + round(y / GRID)] = other  # a ground via is as good as a pad
        touching = sorted(done[node] for node in region if node in done)
        if touching:
            self.joined[unit] = touching[0]  # it lies on a pad that is grounded already
        else:
            whole, ny = self.whole, self.ny

            def site(p):
                # Where the plane is whole, and clear of the other ground vias' holes.
                return (whole is None or p in whole) and all(
                    math.hypot(p // ny * GRID - x, p % ny * GRID - y) >= VIA[0] + CLEARANCE for x, y, _other in vias)

            path = self.connect("GND", region, set(done), width, via_sites=site)
            if path is None:
                self.failed.append(f"no room for a ground via near ({pad[0]:.2f}, {pad[1]:.2f})")
                return
            p = path[0][1]
            self.add_track(0, "GND", pad[0], pad[1], p // self.ny * GRID, p % self.ny * GRID, width)
            self.lay("GND", path, width)
            if path[-1] in done:
                self.joined[unit] = done[path[-1]]
            else:
                end = path[-1][1]
                self.add_via("GND", end // self.ny * GRID, end % self.ny * GRID)
        self.grounded[unit] = region

    def drain(self):
        while self.queue:
            self.unit = self.queue.popleft()
            if self.unit in self.ground:
                self.route_ground(self.unit)
            else:
                self.route_net(self.unit)

    def route_all(self):
        def span(net):
            xs, ys = [p[0] for p in self.pads[net]], [p[1] for p in self.pads[net]]
            return max(xs) - min(xs) + max(ys) - min(ys)

        def node(x, y):
            return round(x / GRID) * self.ny + round(y / GRID)

        nets = [n for n in self.pads if len(self.pads[n]) > 1 and n != "GND" and not n.endswith("(no net)")]
        missing = [n for n in ROUTE_FIRST if n not in nets]
        if missing:
            raise SystemExit(f"ROUTE_FIRST names nets that do not exist: {missing}")
        # Ground pads: the big ones first, so a small pad beside a big one can simply join it.
        pads = sorted(self.pads["GND"], key=lambda pad: (-pad[2] * pad[3], pad[1], pad[0]))
        self.ground = {f"ground of {pad[5]} at ({pad[0]:.2f}, {pad[1]:.2f})": pad for pad in pads if len(pad[4]) == 1}
        # Through-hole ground pads are in the plane already, unless bottom tracks cut them off.
        plated = {f"ground of {pad[5]} at ({pad[0]:.2f}, {pad[1]:.2f})": pad for pad in pads if len(pad[4]) == 2}
        self.grounded = {unit: {(0, p) for p in self.pad_nodes(pad)} for unit, pad in plated.items()}  # unit -> its pad's nodes
        self.joined = {}                                                                 # unit -> the unit it hangs on
        self.whole = None
        self.queue.extend(ROUTE_FIRST)
        self.queue.extend(self.ground)
        self.queue.extend(sorted((n for n in nets if n not in ROUTE_FIRST), key=lambda n: (span(n), n)))
        self.drain()
        for _ in range(PLANE_ROUNDS):
            self.whole = self.plane()
            cut_off = sorted({unit for x, y, net, unit in self.vias if net == "GND" and node(x, y) not in self.whole})
            for unit, pad in plated.items():
                if unit not in self.ground and node(pad[0], pad[1]) not in self.whole:
                    self.ground[unit] = pad  # from now on a pad like the others: it needs a track to a via
                    cut_off.append(unit)
            if not cut_off:
                break
            for unit in cut_off:
                self.rip(unit)
            self.rebuild()
            self.drain()
        self.whole = self.plane()
        for x, y, net, _unit in self.vias:
            if net == "GND" and node(x, y) not in self.whole:
                self.failed.append(f"the ground via at ({x:.2f}, {y:.2f}) does not reach the ground plane")
        for unit, pad in plated.items():
            if unit not in self.ground and node(pad[0], pad[1]) not in self.whole:
                self.failed.append(f"the ground pad at ({pad[0]:.2f}, {pad[1]:.2f}) does not reach the ground plane")
        print(f"routed {len(nets)} nets and {len(self.ground)} ground pads with {self.ripped} rip-ups")


def main():
    net_of, nc = connections()
    write_project()
    write_schematic(net_of, nc)
    write_footprints()
    write_lib_tables()
    write_pcb(net_of, nc)
    print(f"wrote {HW}/{NAME}.kicad_{{pro,sch,pcb}}")


if __name__ == "__main__":
    main()
