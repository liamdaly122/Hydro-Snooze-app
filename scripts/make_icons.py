#!/usr/bin/env python3
"""Generate the PWA icons.

No Pillow, no build step, no binary blobs checked in without a way to regenerate
them. This writes PNGs directly: a black ground with the same low purple glow the
temperature card uses, and a crescent moon, which is the unit's own sleep schedule
button and the one thing this whole app exists to press.

    python3 scripts/make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "frontend" / "public" / "icons"

GLOW = (0x8E, 0x57, 0xB5)  # the purple from the middle of the temperature ramp
SUPERSAMPLE = 3


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


def crescent_coverage(u: float, v: float, scale: float) -> float:
    """1 inside the crescent, 0 outside. u and v are 0..1 across the icon."""
    cx, cy = 0.5, 0.485
    outer_r = 0.235 * scale
    # Offsetting a second circle up and to the right carves the crescent.
    bite_cx, bite_cy = cx + 0.105 * scale, cy - 0.075 * scale
    bite_r = 0.205 * scale

    in_outer = math.hypot(u - cx, v - cy) <= outer_r
    in_bite = math.hypot(u - bite_cx, v - bite_cy) <= bite_r
    return 1.0 if in_outer and not in_bite else 0.0


def render(size: int, *, maskable: bool) -> list[list[tuple[int, int, int]]]:
    # A maskable icon can be cropped to a circle by the launcher, so pull the mark
    # into the safe zone and let the glow run to the edges.
    scale = 0.78 if maskable else 1.0
    rows: list[list[tuple[int, int, int]]] = []
    step = 1.0 / (size * SUPERSAMPLE)

    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            glow_acc = 0.0
            moon_acc = 0.0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    u = (x * SUPERSAMPLE + sx + 0.5) * step
                    v = (y * SUPERSAMPLE + sy + 0.5) * step
                    # Same shape as the card: an ellipse anchored low, fading out.
                    d = math.hypot((u - 0.5) / 0.78, (v - 0.94) / 0.72)
                    glow_acc += max(0.0, 1.0 - d) ** 1.6
                    moon_acc += crescent_coverage(u, v, scale)
            n = SUPERSAMPLE * SUPERSAMPLE
            glow = glow_acc / n
            moon = moon_acc / n

            base = tuple(int(c * glow * 0.85) for c in GLOW)
            px = tuple(int(b + (255 - b) * moon) for b in base)
            row.append(px)  # type: ignore[arg-type]
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
