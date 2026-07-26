"""يولّد أيقونة التطبيق (ui/app.ico) بلا أي مكتبة رسم خارجية.

نرسم البكسلات حسابياً ثم نغلّفها في PNG داخل ملف ICO. السبب أن ويندوز
يعرض أيقونة بايثون الافتراضية لأي اختصار، فلا يميّز المستخدم تطبيقه
عن أي سكربت آخر.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

SIZE = 256
BG = (29, 78, 137)        # الأزرق الحبري — نفس لون واجهة التطبيق
PAPER = (255, 255, 255)
FLAP = (214, 228, 244)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, ...]:
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _rounded_rect(x: float, y: float, box: tuple[float, float, float, float],
                  radius: float) -> float:
    """مسافة موقّعة إلى مستطيل بأركان دائرية — سالبة داخله."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hw, hh = (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius
    dx = abs(x - cx) - hw
    dy = abs(y - cy) - hh
    outside = math.hypot(max(dx, 0), max(dy, 0))
    return outside + min(max(dx, dy), 0) - radius


def _seg_dist(px: float, py: float, ax: float, ay: float,
              bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    t = 0.0 if (vx * vx + vy * vy) == 0 else (wx * vx + wy * vy) / (vx * vx + vy * vy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + vx * t), py - (ay + vy * t))


def _cover(distance: float) -> float:
    """تنعيم الحواف: 1 داخل الشكل، 0 خارجه، تدرّج على بكسل واحد."""
    return max(0.0, min(1.0, 0.5 - distance))


def render() -> bytes:
    body = (52.0, 84.0, 204.0, 172.0)      # جسم الظرف
    rows: list[bytes] = []

    for py in range(SIZE):
        row = bytearray()
        row.append(0)                       # بايت المرشّح لكل سطر في PNG
        for px in range(SIZE):
            x, y = px + 0.5, py + 0.5

            bg_a = _cover(_rounded_rect(x, y, (8, 8, SIZE - 8, SIZE - 8), 52))
            if bg_a <= 0:
                row += bytes((0, 0, 0, 0))
                continue

            color = BG
            env_a = _cover(_rounded_rect(x, y, body, 10))
            if env_a > 0:
                color = _mix(color, PAPER, env_a)

                # طيّة الظرف: خطّان من الركنين العلويين يلتقيان في المنتصف
                d = min(
                    _seg_dist(x, y, body[0] + 4, body[1] + 4, 128, 138),
                    _seg_dist(x, y, body[2] - 4, body[1] + 4, 128, 138),
                )
                flap_a = _cover(d - 5) * env_a
                if flap_a > 0:
                    color = _mix(color, FLAP, flap_a)

            row += bytes((*color, round(255 * bg_a)))
        rows.append(bytes(row))

    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> None:
    png = render()
    # ICONDIR + إدخال واحد؛ الصفر في العرض/الارتفاع يعني 256 بكسل
    ico = (struct.pack("<HHH", 0, 1, 1)
           + struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
           + png)
    out = Path(__file__).resolve().parent / "app.ico"
    out.write_bytes(ico)
    print(f"أُنشئت {out}  ({len(ico):,} بايت)")


if __name__ == "__main__":
    main()
