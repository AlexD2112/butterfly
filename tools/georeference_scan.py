"""
Turn a scanned period street plan into web-map tiles the game's Map panel can show (2026-11-02).

    python tools/georeference_scan.py SCAN.jpg CONTROL_POINTS.csv static/maptiles/petrograd_1917 \
        --source "Plan of St Petersburg, 1914, <publisher>. Public domain. Scan: <URL>"

CONTROL_POINTS.csv has a header row `px,py,lat,lng` and one row per point you can find on both the
scan and the real world: a pixel position on the scan, and that spot's real coordinates (take them
from Wikidata or OpenStreetMap - bridge ends, church domes, corners of big squares work well).
Use 12 or more points spread over the whole sheet, not bunched in the middle.

What it does:
  1. Fits a second-order polynomial from Web Mercator metres to scan pixels (least squares), and
     reports how far each control point lands from where the fit puts it.
  2. Refuses to write anything if the fit is poor (RMS error over --max-rms metres, 35 by default):
     a warped plan that puts the Winter Palace in the river is worse than no plan.
  3. Cuts 256-px PNG tiles for --zooms (default 11-16) into OUT/{z}/{x}/{y}.png, writes
     OUT/SOURCE.txt (attribution, which the map shows) and OUT/fit.json (the accuracy report).

The game only offers the historic layer when OUT exists and fit.json shows a passing fit (see
geodata.setting_map_data). Needs numpy and Pillow, which the app itself does not.
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
R = 6378137.0
TILE = 256


def to_merc(lat, lng):
    x = math.radians(lng) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def poly_terms(x, y):
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=-1)


def fit(points):
    """points: [(px, py, mx, my)] -> (coef_px, coef_py, scale, residuals_m)."""
    pts = np.array(points, dtype=float)
    mx0, my0 = pts[:, 2].mean(), pts[:, 3].mean()
    scale = max(np.ptp(pts[:, 2]), np.ptp(pts[:, 3])) or 1.0
    X = (pts[:, 2] - mx0) / scale
    Y = (pts[:, 3] - my0) / scale
    A = poly_terms(X, Y)
    cpx, *_ = np.linalg.lstsq(A, pts[:, 0], rcond=None)
    cpy, *_ = np.linalg.lstsq(A, pts[:, 1], rcond=None)
    # residuals in metres, using the scan's average metres-per-pixel from a first-order fit
    a, *_ = np.linalg.lstsq(np.stack([np.ones(len(pts)), pts[:, 0], pts[:, 1]], axis=-1), pts[:, 2], rcond=None)
    b, *_ = np.linalg.lstsq(np.stack([np.ones(len(pts)), pts[:, 0], pts[:, 1]], axis=-1), pts[:, 3], rcond=None)
    m_per_px = math.sqrt(abs(a[1] * b[2] - a[2] * b[1]))
    pred_px = A @ cpx
    pred_py = A @ cpy
    resid_m = np.hypot(pred_px - pts[:, 0], pred_py - pts[:, 1]) * m_per_px
    return {"cpx": cpx, "cpy": cpy, "mx0": mx0, "my0": my0, "scale": scale}, resid_m


def merc_to_px(model, mx, my):
    X = (mx - model["mx0"]) / model["scale"]
    Y = (my - model["my0"]) / model["scale"]
    A = poly_terms(X, Y)
    return A @ model["cpx"], A @ model["cpy"]


def tile_bounds(z, x, y):
    n = 2 ** z
    size = 2 * math.pi * R / n
    minx = -math.pi * R + x * size
    maxy = math.pi * R - y * size
    return minx, maxy, size


def lat_lng_to_tile(lat, lng, z):
    n = 2 ** z
    x = int((lng + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scan")
    ap.add_argument("points")
    ap.add_argument("out")
    ap.add_argument("--source", required=True, help="Attribution line shown on the map (title, year, rights, scan URL).")
    ap.add_argument("--zooms", default="11-16")
    ap.add_argument("--max-rms", type=float, default=35.0)
    args = ap.parse_args(argv)

    with open(args.points, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if len(rows) < 8:
        sys.exit(f"Need at least 8 control points (12+ recommended); got {len(rows)}.")
    points = []
    for r in rows:
        mx, my = to_merc(float(r["lat"]), float(r["lng"]))
        points.append((float(r["px"]), float(r["py"]), mx, my))
    model, resid = fit(points)
    rms = float(math.sqrt(np.mean(resid ** 2)))
    worst = int(np.argmax(resid))
    print(f"Fit RMS error: {rms:.1f} m over {len(points)} points (worst: row {worst + 2}, {resid[worst]:.1f} m)")
    if rms > args.max_rms:
        sys.exit(f"Fit too poor (RMS {rms:.1f} m > {args.max_rms} m). Check the worst points and try again. Nothing written.")

    img = Image.open(args.scan).convert("RGBA")
    src = np.asarray(img)
    h, w = src.shape[:2]
    lats = [float(r["lat"]) for r in rows]
    lngs = [float(r["lng"]) for r in rows]
    pad_lat, pad_lng = (max(lats) - min(lats)) * 0.15, (max(lngs) - min(lngs)) * 0.15
    south, north = min(lats) - pad_lat, max(lats) + pad_lat
    west, east = min(lngs) - pad_lng, max(lngs) + pad_lng

    z0, z1 = (int(v) for v in args.zooms.split("-"))
    out = Path(args.out)
    written = 0
    for z in range(z0, z1 + 1):
        x0, y0 = lat_lng_to_tile(north, west, z)
        x1, y1 = lat_lng_to_tile(south, east, z)
        for tx in range(x0, x1 + 1):
            for ty in range(y0, y1 + 1):
                minx, maxy, size = tile_bounds(z, tx, ty)
                step = size / TILE
                gx = minx + (np.arange(TILE) + 0.5) * step
                gy = maxy - (np.arange(TILE) + 0.5) * step
                MX, MY = np.meshgrid(gx, gy)
                px, py = merc_to_px(model, MX.ravel(), MY.ravel())
                px = np.rint(px).astype(int).reshape(TILE, TILE)
                py = np.rint(py).astype(int).reshape(TILE, TILE)
                inside = (px >= 0) & (px < w) & (py >= 0) & (py < h)
                if not inside.any():
                    continue
                tile = np.zeros((TILE, TILE, 4), dtype=np.uint8)
                tile[inside] = src[py[inside], px[inside]]
                path = out / str(z) / str(tx) / f"{ty}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.fromarray(tile).save(path, optimize=True)
                written += 1
    (out / "SOURCE.txt").write_text(args.source.strip() + "\n", encoding="utf-8")
    (out / "fit.json").write_text(json.dumps({
        "rms_m": round(rms, 1), "max_m": round(float(resid.max()), 1), "points": len(points),
        "max_rms_allowed": args.max_rms, "zooms": args.zooms, "tiles": written,
    }, indent=2), encoding="utf-8")
    print(f"Wrote {written} tiles to {out}")


if __name__ == "__main__":
    main()
