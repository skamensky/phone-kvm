#!/usr/bin/env python3
"""Board checks, fabrication outputs and pictures, from the generated KiCad project.

  tools/board.py

Runs KiCad's electrical rules check on the schematic and its design rules check on the PCB
against JLCPCB's limits (hardware/phone-kvm.kicad_pro), including schematic parity: every pad on
the net the schematic gives it. Any violation of any severity fails. On success it writes the
JLCPCB order files to build/fab/ (Gerbers and drill files zipped, BOM, pick-and-place) and
pictures to build/board/ (the schematic, both copper layers, 3D views of both sides).
Prints one line per check and exits non-zero if any fails.
"""
import csv
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HW = ROOT / "hardware"
BUILD = ROOT / "build"
FAB = BUILD / "fab"
PICTURES = BUILD / "board"
NAME = "phone-kvm"
SCH, PCB = HW / f"{NAME}.kicad_sch", HW / f"{NAME}.kicad_pcb"
LAYERS = "F.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts"


def kicad(*args):
    done = subprocess.run(["kicad-cli", *args], cwd=HW, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"kicad-cli {' '.join(args)}\n{done.stdout}{done.stderr}")


# Silkscreen that touches a pad opening, another part's silkscreen or the board edge is clipped by
# the fab (the Gerbers are exported with --subtract-soldermask); it cannot affect the circuit.
COSMETIC = {"silk_over_copper", "silk_overlap", "silk_edge_clearance"}


def violations(report, keys):
    found = []
    for key in keys:
        for v in report.get(key, []):
            if v["type"] in COSMETIC:
                continue
            found.append(f"{v['severity']} {v['type']}: {v['description']} "
                         f"[{'; '.join(i['description'] for i in v['items'])}]")
    return found


def erc():
    out = BUILD / "erc.json"
    kicad("sch", "erc", "--format", "json", "--severity-all", "-o", str(out), SCH.name)
    report = json.loads(out.read_text())
    found = [x for sheet in report["sheets"] for x in violations(sheet, ["violations"])]
    return "ERC (schematic)", found


def drc():
    out = BUILD / "drc.json"
    kicad("pcb", "drc", "--schematic-parity", "--format", "json", "--severity-all", "-o", str(out), PCB.name)
    report = json.loads(out.read_text())
    return [("DRC (JLCPCB rules)", violations(report, ["violations"])),
            ("Unrouted connections", violations(report, ["unconnected_items"])),
            ("Schematic/PCB parity", violations(report, ["schematic_parity"]))]


def fab_outputs():
    """JLCPCB's formats: Gerber X2 off, Protel extensions, Excellon drill; BOM and CPL CSVs."""
    import circuit  # noqa: PLC0415 - hardware/ is on sys.path via run()

    shutil.rmtree(FAB, ignore_errors=True)
    gerbers = FAB / "gerbers"
    gerbers.mkdir(parents=True)
    kicad("pcb", "export", "gerbers", "--layers", LAYERS, "--no-x2", "--use-drill-file-origin",
          "--subtract-soldermask", "-o", str(gerbers), PCB.name)
    kicad("pcb", "export", "drill", "--format", "excellon", "--drill-origin", "plot",
          "--excellon-units", "mm", "--excellon-separate-th", "-o", str(gerbers) + "/", PCB.name)
    with zipfile.ZipFile(FAB / f"{NAME}-gerbers.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(gerbers.iterdir()):
            z.write(f, f.name)

    pos = BUILD / "pos.csv"
    kicad("pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both",
          "--smd-only", "--use-drill-file-origin", "-o", str(pos), PCB.name)
    lcsc = {ref: p[3] for ref, p in circuit.PARTS.items() if p[3]}
    rows = [r for r in csv.DictReader(pos.open()) if r["Ref"] in lcsc]
    with (FAB / f"{NAME}-cpl.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for r in rows:
            w.writerow([r["Ref"], f"{float(r['PosX']):.3f}mm", f"{float(r['PosY']):.3f}mm",
                        "Top" if r["Side"] == "top" else "Bottom", r["Rot"]])
    groups = {}
    for ref, (_, value, footprint, part) in circuit.PARTS.items():
        if part:
            groups.setdefault((value, footprint.split(":")[1], part), []).append(ref)
    with (FAB / f"{NAME}-bom.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (value, footprint, part), refs in sorted(groups.items(), key=lambda g: g[1][0]):
            w.writerow([value, ",".join(refs), footprint, part])
    placed = {r["Ref"] for r in rows}
    missing = sorted(set(lcsc) - placed)
    return "Fab outputs (Gerbers, drill, BOM, CPL)", [f"{m} has an LCSC part but no SMD placement" for m in missing]


def pictures():
    shutil.rmtree(PICTURES, ignore_errors=True)
    PICTURES.mkdir(parents=True)
    kicad("sch", "export", "pdf", "-o", str(PICTURES / "schematic.pdf"), SCH.name)
    kicad("sch", "export", "svg", "-o", str(PICTURES), SCH.name)
    (PICTURES / f"{NAME}.svg").rename(PICTURES / "schematic.svg")
    for side, layers in (("top", "F.Cu,F.Silkscreen,F.Fab,Edge.Cuts"), ("bottom", "B.Cu,B.Silkscreen,Edge.Cuts")):
        kicad("pcb", "export", "svg", "--layers", layers, "--mode-single", "--page-size-mode", "2",
              "--exclude-drawing-sheet", *(["--mirror"] if side == "bottom" else []),
              "-o", str(PICTURES / f"copper-{side}.svg"), PCB.name)
        kicad("pcb", "render", "--side", side, "--width", "1200", "--height", "1600", "--quality", "basic",
              "-o", str(PICTURES / f"{side}.png"), PCB.name)
    want = ["schematic.pdf", "schematic.svg", "copper-top.svg", "copper-bottom.svg", "top.png", "bottom.png"]
    return "Pictures (schematic, copper, 3D views)", [f"{n} was not written" for n in want if not (PICTURES / n).exists()]


def run():
    sys.path.insert(0, str(HW))
    BUILD.mkdir(exist_ok=True)
    results = [erc(), *drc()]
    if not any(found for _, found in results):
        results += [fab_outputs(), pictures()]
    for name, found in results:
        for line in found[:20]:
            print(f"      {line}")
        if len(found) > 20:
            print(f"      ... {len(found) - 20} more")
        yield name, len(found), "issues", not found


def main():
    ok = True
    for name, value, unit, passed in run():
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {value} {unit}")
        ok &= passed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
