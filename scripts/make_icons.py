#!/usr/bin/env python3
"""Generate the PWA icons.

No Pillow, no build step, no binary blobs checked in without a way to regenerate
them. This writes PNGs directly: a black ground, a small crescent moon, and the
night curve, which is what the whole app is for. The line drops from Drift into
Deep, comes back up through REM and climbs to Wake, and it is coloured from the
temperature ramp the app uses everywhere else, so cool parts are blue and warm
ones rose.

    python3 scripts/make_icons.py

Everything is drawn in a 512 by 512 space and scaled to each size. Edges are
smoothed from distances rather than by supersampling: how far a pixel is from
the line or a circle edge says how much of it is covered.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icons"

SPACE = 512.0

#: The night, as cubic curves in the 512 space: Drift, down into Deep, up
#: through REM, Wake.
CURVE = [
    ((104, 226), (164, 226), (168, 360), (236, 360)),
    ((236, 360), (290, 360), (300, 298), (336, 266)),
    ((336, 266), (364, 242), (384, 206), (408, 206)),
]
LINE_RADIUS = 20.0
#: Points per curve. Enough that the segments never show at 512.
STEPS = 64

#: Along the line, left to right: Drift 27, Deep 15, REM 23, Wake 31. The same
#: stops as tint() in frontend/src/domain.ts.
LINE_FROM, LINE_TO = 104.0, 408.0
RAMP = [
    (0.0, (0xA9, 0x3F, 0xB0)),
    (0.3, (0x2A, 0x5F, 0xEA)),
    (0.7, (0x83, 0x48, 0xD2)),
    (1.0, (0xC4, 0x3C, 0x86)),
]

#: The moon: a circle with a smaller one taken out of its top right.
MOON = ((132.0, 150.0), 36.0)
BITE = ((150.0, 136.0), 30.0)


def write_png(path: Path, size: int, rows: list[list[tuple[int, int, int]]]) -> None:
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def polyline() -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for p0, p1, p2, p3 in CURVE:
        for i in range(STEPS + 1):
            if points and i == 0:
                continue
            t = i / STEPS
            a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
            points.append(
                (
                    a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
                )
            )
    return points


def ramp(x: float) -> tuple[float, float, float]:
    t = min(1.0, max(0.0, (x - LINE_FROM) / (LINE_TO - LINE_FROM)))
    for (t0, c0), (t1, c1) in zip(RAMP, RAMP[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * k for a, b in zip(c0, c1))  # type: ignore[return-value]
    return RAMP[-1][1]


def covered(edge_distance: float, px_per_unit: float) -> float:
    """How much of a pixel is inside, from how far inside the edge it is."""
    return min(1.0, max(0.0, edge_distance * px_per_unit + 0.5))


def render(size: int, *, maskable: bool) -> list[list[tuple[int, int, int]]]:
    # A maskable icon can be cropped to a circle by the launcher, so pull the
    # whole drawing into the safe zone around the middle.
    scale = 0.78 if maskable else 1.0
    px_per_unit = size / SPACE * scale

    def to_space(pixel: float) -> float:
        """A pixel centre, in the 512 space before scaling about the middle."""
        return ((pixel + 0.5) / size * SPACE - SPACE / 2) / scale + SPACE / 2

    # Nearest distance to the line for every pixel, filled in one segment at a
    # time over only the pixels a segment can reach.
    far = 1e9
    nearest = [[far] * size for _ in range(size)]
    reach = LINE_RADIUS + 2 / px_per_unit
    points = polyline()
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy or 1e-9

        def to_pixel(u: float) -> float:
            return ((u - SPACE / 2) * scale + SPACE / 2) / SPACE * size

        x0 = max(0, int(to_pixel(min(ax, bx) - reach)))
        x1 = min(size - 1, int(to_pixel(max(ax, bx) + reach)) + 1)
        y0 = max(0, int(to_pixel(min(ay, by) - reach)))
        y1 = min(size - 1, int(to_pixel(max(ay, by) + reach)) + 1)
        for y in range(y0, y1 + 1):
            v = to_space(y)
            row = nearest[y]
            for x in range(x0, x1 + 1):
                u = to_space(x)
                t = min(1.0, max(0.0, ((u - ax) * dx + (v - ay) * dy) / length2))
                d = math.hypot(u - (ax + t * dx), v - (ay + t * dy))
                if d < row[x]:
                    row[x] = d

    (mx, my), mr = MOON
    (bx_, by_), br = BITE
    rows: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        v = to_space(y)
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            u = to_space(x)
            line = covered(LINE_RADIUS - nearest[y][x], px_per_unit)
            r, g, b = ramp(u)
            px = [r * line, g * line, b * line]
            moon = covered(mr - math.hypot(u - mx, v - my), px_per_unit)
            moon *= 1.0 - covered(br - math.hypot(u - bx_, v - by_), px_per_unit)
            px = [c + (255 - c) * moon for c in px]
            row.append(tuple(round(c) for c in px))  # type: ignore[arg-type]
        rows.append(row)
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size, maskable, name in [
        (180, False, "icon-180.png"),
        (192, False, "icon-192.png"),
        (512, False, "icon-512.png"),
        (512, True, "icon-512-maskable.png"),
    ]:
        write_png(OUT / name, size, render(size, maskable=maskable))
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
