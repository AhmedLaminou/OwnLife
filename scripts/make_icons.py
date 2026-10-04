"""Renders the OwnLife logo (frontend/public/favicon.svg) as PNG icons, with
only the standard library: the app's notifications and the browser extension
need bitmaps. Run once after changing the logo:

    backend\\.venv\\Scripts\\python.exe scripts\\make_icons.py
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BG = (7, 11, 18)
CYAN = (34, 211, 238)
AMBER = (245, 165, 36)
SS = 4  # supersampling per axis


def _mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _sample(x: float, y: float):
    """Colour and alpha of the 64×64 logo at (x, y)."""
    # rounded square, rx 14
    r = 14
    cx, cy = min(max(x, r), 64 - r), min(max(y, r), 64 - r)
    if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
        return (0, 0, 0), 0.0
    d = math.hypot(x - 32, y - 32)
    if d <= 5:
        return AMBER, 1.0
    if math.hypot(x - 49, y - 32) <= 3.5:
        return CYAN, 1.0
    if 15 <= d <= 19:
        return _mix(CYAN, AMBER, max(0.0, min(1.0, (x + y) / 128))), 1.0
    return BG, 1.0


def render(size: int) -> bytes:
    rows = []
    n = size * SS
    for py in range(size):
        row = bytearray([0])  # filter: none
        for px in range(size):
            acc = [0.0, 0.0, 0.0]
            alpha = 0.0
            for sy in range(SS):
                for sx in range(SS):
                    x = (px * SS + sx + 0.5) * 64 / n
                    y = (py * SS + sy + 0.5) * 64 / n
                    c, a = _sample(x, y)
                    alpha += a
                    for i in range(3):
                        acc[i] += c[i] * a
            k = SS * SS
            if alpha:
                row += bytes(round(acc[i] / alpha) for i in range(3))
            else:
                row += b"\0\0\0"
            row.append(round(255 * alpha / k))
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")


def main() -> None:
    targets = {
        ROOT / "frontend" / "public" / "icon-192.png": 192,
        ROOT / "frontend" / "public" / "icon-512.png": 512,
        **{ROOT / "extension" / "icons" / f"icon-{s}.png": s for s in (16, 32, 48, 128)},
    }
    for path, size in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(render(size))
        print(f"{path.relative_to(ROOT)}  {size}x{size}")


if __name__ == "__main__":
    main()
