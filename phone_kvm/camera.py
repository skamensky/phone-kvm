"""The phone's screen as a camera above it sees it: a lit quadrilateral somewhere in the frame.

`find` locates the screen's four corners; `rectify` turns the quadrilateral back into the upright
rectangle the phone displays. A camera sees everything a person does, including the windows
Android blanks on its video output (lock-screen entry, banking apps).

Finding the screen needs it lit and lighter than what surrounds it, so calibrate on a light
screen with the phone on a dark surface. The corners are then stored; the camera and the phone
must not move afterwards.
"""
import math
from collections import deque

import numpy as np
from PIL import Image

COARSE = 320    # the long side of the image the screen is first located in
LINES = 48      # scanlines measured across each edge
MIN_AREA = 0.05  # of the frame; anything smaller is a reflection, not the screen


def _otsu(grey):
    """The brightness that best separates the lit screen from its surroundings."""
    counts = np.bincount(grey.ravel(), minlength=256).astype(float)
    total, levels = counts.sum(), np.arange(256)
    below = np.cumsum(counts)
    mean_below = np.cumsum(counts * levels) / np.maximum(below, 1)
    above = total - below
    mean_above = (np.sum(counts * levels) - np.cumsum(counts * levels)) / np.maximum(above, 1)
    return int(np.argmax(below * above * (mean_below - mean_above) ** 2))


def _largest(mask):
    """The bounding box (left, top, right, bottom) and area of the largest connected lit region."""
    height, width = mask.shape
    seen = np.zeros_like(mask)
    best = (0, None)
    for start in zip(*np.nonzero(mask)):
        if seen[start]:
            continue
        seen[start] = True
        queue, area = deque([start]), 0
        top, left, bottom, right = start[0], start[1], start[0], start[1]
        while queue:
            y, x = queue.popleft()
            area += 1
            top, bottom, left, right = min(top, y), max(bottom, y), min(left, x), max(right, x)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    queue.append((ny, nx))
        if area > best[0]:
            best = (area, (left, top, right + 1, bottom + 1))
    return best[1], best[0]


def _crossing(profile, threshold):
    """Where a brightness profile, read from outside the screen inwards, first reaches the
    threshold: a sub-pixel position, or None."""
    lit = np.nonzero(profile >= threshold)[0]
    if not len(lit) or lit[0] == 0:
        return None
    i = lit[0]
    return i - 1 + (threshold - profile[i - 1]) / max(profile[i] - profile[i - 1], 1e-6)


def _edge(grey, threshold, box, side):
    """A line fitted along one edge of the screen, as (a, b) with across = a * along + b.

    `along` runs down the frame for the left and right edges and across it for the top and
    bottom ones. Scanlines cover the middle of the edge only: the corners are rounded.
    """
    left, top, right, bottom = box
    vertical = side in ("left", "right")
    lo, hi = (top, bottom) if vertical else (left, right)
    span = (right - left) if vertical else (bottom - top)
    margin = max(4, span // 8)
    points = []
    for along in np.linspace(lo + (hi - lo) * 0.15, hi - (hi - lo) * 0.15, LINES).astype(int):
        line = grey[along, :] if vertical else grey[:, along]
        if side in ("left", "top"):
            start = max(0, (left if vertical else top) - margin)
            at = _crossing(line[start:start + 2 * margin + span // 4].astype(float), threshold)
            if at is not None:
                points.append((along, start + at))
        else:
            end = min(len(line), (right if vertical else bottom) + margin)
            at = _crossing(line[max(0, end - 2 * margin - span // 4):end][::-1].astype(float), threshold)
            if at is not None:
                points.append((along, end - 1 - at))
    points = np.array(points)
    if len(points) < LINES // 2:
        raise RuntimeError(f"the screen's {side} edge is not visible; calibrate on a light screen")
    # Dark content touching the edge puts some points too far in: refit without them.
    for _ in range(3):
        a, b = np.polyfit(points[:, 0], points[:, 1], 1)
        residual = np.abs(points[:, 1] - (a * points[:, 0] + b))
        keep = residual <= max(1.0, 2.5 * np.median(residual))
        if keep.all() or keep.sum() < LINES // 3:
            break
        points = points[keep]
    return a, b


def find(image):
    """The screen's corners in the frame: [top-left, top-right, bottom-right, bottom-left] as
    the camera sees them, each (x, y)."""
    grey = np.asarray(image.convert("L"))
    scale = max(image.size) / COARSE
    coarse = np.asarray(image.convert("L").resize(
        (round(image.width / scale), round(image.height / scale)), Image.BOX))
    threshold = _otsu(coarse)
    box, area = _largest(coarse > threshold)
    if box is None or area < MIN_AREA * coarse.size:
        raise RuntimeError("no lit screen in the camera's view; wake the phone, show a light screen "
                           "and calibrate again")
    box = tuple(round(v * scale) for v in box)
    lines = {side: _edge(grey, threshold, box, side) for side in ("left", "top", "right", "bottom")}

    def corner(vertical, horizontal):
        # x = a * y + b meets y = c * x + d
        (a, b), (c, d) = lines[vertical], lines[horizontal]
        y = (c * b + d) / (1 - a * c)
        return [round(float(a * y + b), 2), round(float(y), 2)]

    return [corner("left", "top"), corner("right", "top"), corner("right", "bottom"), corner("left", "bottom")]


def upright(quad, rotate):
    """The phone's own corners (top-left first, clockwise) given how far clockwise, in degrees,
    the phone appears turned in the camera's frame."""
    turns = rotate // 90 % 4
    return quad[turns:] + quad[:turns]


def size(quad, rotate):
    """The rectified screen's size: the lengths of the quadrilateral's sides."""
    tl, tr, br, bl = upright(quad, rotate)
    return (round((math.dist(tl, tr) + math.dist(bl, br)) / 2),
            round((math.dist(tl, bl) + math.dist(tr, br)) / 2))


def rectify(image, quad, rotate):
    """The phone's screen, upright, cut out of a camera frame."""
    width, height = size(quad, rotate)
    target = [(0, 0), (width, 0), (width, height), (0, height)]
    rows, values = [], []
    for (x, y), (u, v) in zip(target, upright(quad, rotate)):
        rows += [[x, y, 1, 0, 0, 0, -u * x, -u * y], [0, 0, 0, x, y, 1, -v * x, -v * y]]
        values += [u, v]
    coefficients = np.linalg.solve(np.array(rows, float), np.array(values, float))
    return image.transform((width, height), Image.PERSPECTIVE, tuple(coefficients), Image.BICUBIC)
