#!/usr/bin/env python3
"""Coolest art entry -- make.fun competition coolest-art-01.

"Skywhale": a colossal whale gliding through a dusk sky above a
neon-lit city on the shore. Drawn with the Python standard library
only: zlib + struct encode the PNG by hand, math does the geometry.
No Pillow, no network, no randomness, no clock reads -- running the
program twice writes byte-identical PNG files.

Why this subject: one huge, instantly readable silhouette (the whale)
against a deep indigo-to-teal sky, a warm amber city below for scale,
a glowing moon and cyan bioluminescence for the light. It reads at
~300 px across because the composition is a single diagonal shape
plus a dark skyline band, nothing fiddly.

Usage:
    mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
Writes <out>/entry.png (512x512, well under the 1 MiB limit).
Without MAKEFUN_OUTPUT_DIR it writes /output/entry.png, as the grader does.
"""

import math
import os
import struct
import zlib

SIZE = 512          # final image is SIZE x SIZE
SS = 2              # supersampling per axis (4 samples per final pixel)

# ---------------------------------------------------------------- palette
SKY_STOPS = [       # (y, r, g, b) vertical gradient stops
    (0, 12, 18, 52),
    (150, 20, 38, 88),
    (300, 26, 78, 108),
    (400, 34, 96, 104),
    (468, 20, 52, 66),
]
MOON = (255, 232, 168)
MOON_CRATER = (233, 203, 138)
MOON_GLOW = (255, 224, 150)
STAR = (255, 255, 255)
CLOUD = (220, 232, 245)
WHALE_OUTLINE = (16, 32, 66)
WHALE = (46, 88, 148)
WHALE_DARK = (34, 64, 112)
WHALE_BELLY = (146, 188, 222)
WHALE_EYE = (10, 16, 32)
GLOW = (86, 226, 255)      # bioluminescent cyan
SPRAY = (190, 225, 245)
BIRD = (14, 20, 46)
CITY = (8, 12, 28)
CITY_EDGE = (16, 24, 48)
WINDOW_AMBER = (255, 176, 84)
WINDOW_CYAN = (120, 220, 235)
BEACON = (255, 64, 72)
WATER_TOP = (16, 48, 62)
WATER_BOTTOM = (8, 26, 42)
HORIZON_GLOW = (255, 148, 60)


# ---------------------------------------------------------------- canvas
class Canvas:
    """Final-resolution RGB buffer with alpha blending."""

    def __init__(self, size):
        self.size = size
        self.px = bytearray(size * size * 3)

    def blend(self, x, y, color, alpha):
        """Blend color (r, g, b) onto pixel (x, y); alpha 0..255."""
        if alpha <= 0 or x < 0 or y < 0 or x >= self.size or y >= self.size:
            return
        i = (y * self.size + x) * 3
        if alpha >= 255:
            self.px[i], self.px[i + 1], self.px[i + 2] = color
        else:
            inv = 255 - alpha
            self.px[i] = (color[0] * alpha + self.px[i] * inv) // 255
            self.px[i + 1] = (color[1] * alpha + self.px[i + 1] * inv) // 255
            self.px[i + 2] = (color[2] * alpha + self.px[i + 2] * inv) // 255

    def rect(self, x, y, w, h, color, alpha=255):
        """Crisp axis-aligned rectangle (no supersampling)."""
        for yy in range(max(0, y), min(self.size, y + h)):
            for xx in range(max(0, x), min(self.size, x + w)):
                self.blend(xx, yy, color, alpha)


class Rot:
    """Rotation of a local frame about a pivot (cx, cy).

    fwd maps a local point to canvas coords, inv maps a canvas point
    back to local coords for the shape test.
    """

    def __init__(self, cx, cy, deg):
        t = math.radians(deg)
        self.c = math.cos(t)
        self.s = math.sin(t)
        self.cx = cx
        self.cy = cy

    def fwd(self, lx, ly):
        return (self.cx + self.c * lx - self.s * ly,
                self.cy + self.s * lx + self.c * ly)

    def inv(self, x, y):
        dx, dy = x - self.cx, y - self.cy
        return (self.c * dx + self.s * dy,
                -self.s * dx + self.c * dy)


def clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


# ------------------------------------------------------------ shape tests
def circle_test(cx, cy, r):
    def test(x, y):
        return clamp01(r + 0.5 - math.hypot(x - cx, y - cy))
    return test


def ellipse_test(cx, cy, rx, ry):
    m = min(rx, ry)

    def test(x, y):
        n = math.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)
        return clamp01((1.0 - n) * m + 0.5)
    return test


def polygon_test(points):
    def test(x, y):
        inside = False
        n = len(points)
        j = n - 1
        for i in range(n):
            xi, yi = points[i]
            xj, yj = points[j]
            if (yi > y) != (yj > y):
                if x < xi + (y - yi) * (xj - xi) / (yj - yi):
                    inside = not inside
            j = i
        return 1.0 if inside else 0.0
    return test


def _seg_dist(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 == 0.0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def polyline_test(points, width):
    hw = width / 2.0
    segs = list(zip(points, points[1:]))

    def test(x, y):
        best = 0.0
        for (x1, y1), (x2, y2) in segs:
            c = clamp01(hw + 0.5 - _seg_dist(x, y, x1, y1, x2, y2))
            if c > best:
                best = c
                if best >= 1.0:
                    break
        return best
    return test


# -------------------------------------------------------------- painters
def paint(cv, test, bbox, color, alpha=255, tr=None):
    """Paint a shape over bbox (x0, y0, x1, y1) in canvas coords.

    Each final pixel samples SS x SS subpixels; coverage is their mean.
    tr is an optional Rot applied to the shape's local coords.
    """
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(cv.size, x1)
    y1 = min(cv.size, y1)
    step = 1.0 / SS
    for y in range(y0, y1):
        for x in range(x0, x1):
            cov = 0.0
            for sy in range(SS):
                py = y + (sy + 0.5) * step
                for sx in range(SS):
                    px = x + (sx + 0.5) * step
                    if tr is not None:
                        px, py = tr.inv(px, py)
                    cov += test(px, py)
            cov /= SS * SS
            if cov > 0.0:
                cv.blend(x, y, color, int(cov * alpha))


def tbbox(x0, y0, x1, y1, tr=None, pad=2):
    """Canvas bbox for a local rect, through an optional rotation."""
    corners = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    if tr is not None:
        corners = [tr.fwd(x, y) for (x, y) in corners]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (int(min(xs)) - pad, int(min(ys)) - pad,
            int(max(xs)) + pad + 1, int(max(ys)) + pad + 1)


def circle(cv, cx, cy, r, color, alpha=255, tr=None):
    paint(cv, circle_test(cx, cy, r),
          tbbox(cx - r, cy - r, cx + r, cy + r, tr),
          color, alpha, tr)


def ellipse(cv, cx, cy, rx, ry, color, alpha=255, tr=None):
    paint(cv, ellipse_test(cx, cy, rx, ry),
          tbbox(cx - rx, cy - ry, cx + rx, cy + ry, tr),
          color, alpha, tr)


def polygon(cv, points, color, alpha=255, tr=None):
    paint(cv, polygon_test(points), tbbox(
        min(p[0] for p in points), min(p[1] for p in points),
        max(p[0] for p in points), max(p[1] for p in points), tr),
        color, alpha, tr)


def polyline(cv, points, width, color, alpha=255, tr=None):
    paint(cv, polyline_test(points, width), tbbox(
        min(p[0] for p in points), min(p[1] for p in points),
        max(p[0] for p in points), max(p[1] for p in points), tr,
        pad=int(width) + 2),
        color, alpha, tr)


# -------------------------------------------------------------- geometry
def fill_gradient(cv, stops):
    """Vertical gradient through (y, r, g, b) stops."""
    for y in range(cv.size):
        for k in range(len(stops) - 1):
            y0, r0, g0, b0 = stops[k]
            y1, r1, g1, b1 = stops[k + 1]
            if y0 <= y <= y1:
                t = (y - y0) / max(1, y1 - y0)
                r = int(r0 + (r1 - r0) * t)
                g = int(g0 + (g1 - g0) * t)
                b = int(b0 + (b1 - b0) * t)
                break
        else:
            _, r, g, b = stops[-1]
        base = y * cv.size * 3
        for x in range(cv.size):
            i = base + x * 3
            cv.px[i] = r
            cv.px[i + 1] = g
            cv.px[i + 2] = b


def radial_glow(cv, cx, cy, r, color, max_alpha):
    x0, y0 = int(cx - r), int(cy - r)
    x1, y1 = int(cx + r) + 1, int(cy + r) + 1
    for y in range(max(0, y0), min(cv.size, y1)):
        for x in range(max(0, x0), min(cv.size, x1)):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy) / r
            if d < 1.0:
                a = int(max_alpha * (1.0 - d * d))
                if a > 0:
                    cv.blend(x, y, color, a)


def quad_bezier(p0, p1, p2, n=12):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return pts


def scale_about(points, cx, cy, k):
    return [(cx + (x - cx) * k, cy + (y - cy) * k) for (x, y) in points]


# Fixed star field (x, y, radius, alpha) -- hard-coded, deterministic.
STARS = [
    (28, 34, 1.6, 220), (64, 96, 1.2, 160), (96, 44, 1.8, 230),
    (140, 70, 1.1, 150), (176, 30, 1.5, 210), (210, 88, 1.2, 170),
    (248, 40, 1.7, 225), (288, 74, 1.1, 150), (330, 36, 1.5, 205),
    (356, 132, 1.2, 160), (452, 52, 1.6, 215), (486, 120, 1.3, 175),
    (470, 190, 1.1, 150), (44, 160, 1.2, 165), (120, 210, 1.1, 140),
    (206, 150, 1.4, 190), (262, 120, 1.1, 150), (318, 178, 1.2, 160),
    (420, 210, 1.3, 170), (492, 260, 1.2, 155), (36, 250, 1.1, 145),
    (88, 300, 1.2, 155), (160, 268, 1.1, 140), (440, 300, 1.2, 150),
    (480, 350, 1.1, 140), (20, 340, 1.2, 150), (240, 90, 1.5, 200),
    (380, 60, 1.2, 165), (150, 110, 1.1, 145), (300, 230, 1.1, 140),
]

# Buildings: (x, width, height above the water line at y=468).
BUILDINGS = [
    (0, 34, 42), (36, 26, 66), (64, 40, 38), (106, 24, 78), (132, 30, 52),
    (164, 22, 88), (188, 34, 46), (224, 28, 70), (254, 20, 96), (276, 36, 58),
    (314, 26, 82), (342, 30, 48), (374, 22, 74), (398, 36, 56), (436, 24, 90),
    (462, 28, 62), (492, 20, 44),
]


# ---------------------------------------------------------------- drawing
def draw_sky(cv):
    fill_gradient(cv, SKY_STOPS)
    # Warm dusk glow sitting on the horizon behind the skyline.
    radial_glow(cv, 256, 420, 230, HORIZON_GLOW, 34)
    # Moon, upper right, with a soft halo and two faint craters.
    radial_glow(cv, 398, 92, 96, MOON_GLOW, 46)
    circle(cv, 398, 92, 40, MOON)
    circle(cv, 384, 80, 8, MOON_CRATER)
    circle(cv, 410, 104, 6, MOON_CRATER)
    circle(cv, 402, 74, 4, MOON_CRATER)
    # Stars.
    for (x, y, r, a) in STARS:
        circle(cv, x, y, r, STAR, a)
    # Soft clouds drifting behind the whale.
    for (cx, cy, rx, ry, a) in (
            (110, 128, 74, 15, 34), (392, 296, 92, 17, 30),
            (88, 336, 82, 14, 28), (430, 180, 60, 12, 26)):
        ellipse(cv, cx, cy, rx, ry, CLOUD, a)


def draw_birds(cv):
    for (bx, by, s) in ((148, 148, 1.0), (182, 124, 0.8), (336, 152, 0.9),
                        (104, 196, 0.7), (296, 108, 0.8)):
        polyline(cv, [(bx - 9 * s, by + 3 * s), (bx, by - 3 * s),
                      (bx + 9 * s, by + 3 * s)], 2.5, BIRD, 220)


def whale_outline():
    """Whale body outline in local coords: fat rounded head (+x),
    tapering tail stock (-x). Built from bezier segments."""
    pts = []
    pts += quad_bezier((-168, -14), (-90, -52), (0, -58), n=8)
    pts += quad_bezier((0, -58), (110, -52), (172, -6), n=8)[1:]
    pts += quad_bezier((172, -6), (176, 10), (150, 28), n=6)[1:]
    pts += quad_bezier((150, 28), (60, 46), (-40, 44), n=8)[1:]
    pts += quad_bezier((-40, 44), (-120, 34), (-168, 14), n=6)[1:]
    return pts


def draw_whale(cv):
    # Local frame: origin at the body centre, tilted -16 degrees so the
    # whale climbs from lower-left to upper-right.
    w = Rot(252, 244, -16)
    outline = whale_outline()
    ox, oy = 0, 0  # outline centroid is ~ the origin already

    # Tail flukes: two broad rounded lobes fanning from the tail stock.
    for pts, col in (
            ([(-166, -10), (-200, -46), (-234, -62), (-240, -52),
              (-222, -30), (-196, -6)], WHALE),
            ([(-166, 8), (-198, 42), (-230, 56), (-236, 46),
              (-220, 26), (-194, 6)], WHALE_DARK)):
        big = scale_about(pts, -168, 0, 1.07)
        polygon(cv, big, WHALE_OUTLINE, tr=w)
        polygon(cv, pts, col, tr=w)

    # Body: dark outline (outline scaled up a touch), then the fill.
    polygon(cv, scale_about(outline, ox, oy, 1.035), WHALE_OUTLINE, tr=w)
    polygon(cv, outline, WHALE, tr=w)

    # Moonlit rim light along the back.
    rim = (quad_bezier((-120, -44), (-40, -58), (40, -56), n=10)
           + quad_bezier((40, -56), (110, -50), (160, -24), n=8)[1:])
    polyline(cv, rim, 3.5, (150, 196, 238), 170, tr=w)

    # Belly: a subtler lighter patch inside the body's lower half.
    ellipse(cv, 10, 28, 104, 15, (116, 160, 202), tr=w)

    # Pectoral fin under the body near the head, drawn over the belly.
    polygon(cv, [(56, 34), (110, 28), (100, 80), (70, 66)],
            WHALE_DARK, tr=w)

    # Eye with a bright highlight, and the mouth line along the jaw.
    circle(cv, 118, -10, 8.5, WHALE_EYE, tr=w)
    circle(cv, 115, -13, 2.8, STAR, tr=w)
    polyline(cv, quad_bezier((64, 30), (108, 34), (148, 12), n=10),
             3.5, WHALE_DARK, 200, tr=w)

    # Blowhole mist: a few tiny pale droplets right above the blowhole.
    for (dx, dy, r) in ((-10, -16, 2.4), (0, -22, 2.8), (10, -16, 2.2),
                        (-4, -28, 2.0), (6, -28, 2.0)):
        circle(cv, 128 + dx, -50 + dy, r, (214, 236, 250), 215, tr=w)

    # Bioluminescent dots along the back, each with a faint halo.
    for (dx, dy) in ((-110, -42), (-70, -52), (-28, -56), (14, -54),
                     (56, -48), (96, -38)):
        gx, gy = w.fwd(dx, dy)
        radial_glow(cv, gx, gy, 13, GLOW, 60)
        circle(cv, dx, dy, 3.6, GLOW, 255, tr=w)

    # One thin cloud crossing in front of the tail, for depth.
    ellipse(cv, 96, 300, 66, 13, CLOUD, 30)


def draw_city(cv):
    # Skyline silhouettes with a lit window grid on each building.
    for (bx, bw, bh) in BUILDINGS:
        top = 468 - bh
        cv.rect(bx, top, bw, bh, CITY)
        cv.rect(bx, top, bw, 2, CITY_EDGE)
        for wy in range(top + 8, 464, 13):
            for wx in range(bx + 5, bx + bw - 5, 11):
                h = (wx * 7 + wy * 13) % 5
                if h == 0:
                    continue
                col = WINDOW_AMBER if h % 2 else WINDOW_CYAN
                cv.rect(wx, wy, 5, 7, col, 235 if h < 4 else 150)
    # Red beacon on the tallest tower.
    cv.rect(261, 468 - 96 - 7, 4, 7, BEACON)
    circle(cv, 263, 468 - 96 - 9, 3.5, BEACON)

    # Water strip with warm reflection streaks from the city lights.
    for y in range(468, SIZE):
        t = (y - 468) / (SIZE - 468 - 1)
        r = int(WATER_TOP[0] + (WATER_BOTTOM[0] - WATER_TOP[0]) * t)
        g = int(WATER_TOP[1] + (WATER_BOTTOM[1] - WATER_TOP[1]) * t)
        b = int(WATER_TOP[2] + (WATER_BOTTOM[2] - WATER_TOP[2]) * t)
        base = y * SIZE * 3
        for x in range(SIZE):
            i = base + x * 3
            cv.px[i] = r
            cv.px[i + 1] = g
            cv.px[i + 2] = b
    for (x0, x1, y, col, a) in (
            (40, 130, 478, WINDOW_AMBER, 110), (180, 260, 486, WINDOW_AMBER, 90),
            (300, 380, 480, WINDOW_CYAN, 80), (60, 200, 496, WINDOW_AMBER, 70),
            (340, 470, 498, WINDOW_AMBER, 85), (120, 300, 506, WINDOW_CYAN, 60)):
        for x in range(x0, x1):
            cv.blend(x, y, col, a)
            cv.blend(x, y + 1, col, a // 2)


def draw():
    cv = Canvas(SIZE)
    draw_sky(cv)
    draw_birds(cv)
    draw_whale(cv)
    draw_city(cv)
    return cv


# ------------------------------------------------------------ PNG encoding
def encode_png(rgb, width, height):
    """Encode RGB bytes as a PNG (color type 2, filter 0 per row)."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", comp) + chunk(b"IEND", b""))


def main():
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    cv = draw()
    data = encode_png(cv.px, SIZE, SIZE)
    path = os.path.join(out_dir, "entry.png")
    with open(path, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes, %dx%d)" % (path, len(data), SIZE, SIZE))


if __name__ == "__main__":
    main()
