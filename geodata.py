"""
Verified map positions (2026-11-02).

A setting's map.json used to carry hand-typed coordinates for every landmark and barracks. Some of
those were guesses. This module replaces guessing with lookup: each place names one or more search
terms (usually its Russian or English Wikidata label) plus a rough estimate, and the server resolves
it against Wikidata - search, then fetch the item's coordinate (P625) - accepting a result only if

  - its label contains one of the entry's `must_contain` stems (so a barracks search can't land on
    a same-named railway station), and
  - it lies within the setting's bounding box, and
  - it lies within `max_m` metres of the rough estimate (so a same-named place elsewhere can't win).

Anything that fails all three tests stays unplaced and is simply not drawn - a missing marker is
better than a confidently wrong one. Results (hits and misses) are cached in a JSON file next to the
database, so each place is looked up once per deploy disk, not once per page view.

map_status() also decides whether a setting's map is worth showing at all (see VIABILITY_*): the
Map button only appears when enough places verified and the map has a base layer.

Network calls go through _http_get_json, which tests patch; nothing here runs at import time.
"""

import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import db

USER_AGENT = "ForkingPaths/1.0 (historical RPG; https://forkingpaths.app)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
HTTP_TIMEOUT = 12
REQUEST_SPACING = 0.25          # seconds between Wikidata requests - polite, and plenty for ~40 places
SEARCH_LANGUAGES = ("ru", "en")
DEFAULT_MAX_M = {"landmark": 800, "unit": 3000}
MISS_RETRY_SECONDS = 7 * 24 * 3600   # retry places that failed once a week, in case Wikidata improves

# A map is shown only if at least this share of its landmarks and units verified.
VIABILITY_LANDMARKS = 0.75
VIABILITY_UNITS = 0.5

_lock = threading.Lock()
_running = set()


def cache_path():
    return Path(db.DB_FILE).parent / "geocache.json"


def _load_cache():
    try:
        return json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    path = cache_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _http_get_json(params):
    url = WIKIDATA_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_bbox(lat, lng, bbox):
    if not bbox:
        return True
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def _coords_for(qids):
    """{qid: (lat, lng, label)} for the items that have a P625 coordinate."""
    if not qids:
        return {}
    data = _http_get_json({
        "action": "wbgetentities", "ids": "|".join(qids), "props": "claims|labels",
        "languages": "ru|en", "format": "json",
    })
    out = {}
    for qid, ent in (data.get("entities") or {}).items():
        for claim in (ent.get("claims") or {}).get("P625", []):
            value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}
            if "latitude" in value and "longitude" in value:
                labels = ent.get("labels") or {}
                label = (labels.get("ru") or labels.get("en") or {}).get("value", "")
                out[qid] = (float(value["latitude"]), float(value["longitude"]), label)
                break
    return out


def resolve_place(entry, kind, bbox):
    """One place -> a cache record: {"ok": True, lat, lng, qid, label, source} or {"ok": False}."""
    stems = entry.get("must_contain") or []
    stems = [stems] if isinstance(stems, str) else stems
    stems = [x.lower() for x in stems if x]
    max_m = entry.get("max_m") or DEFAULT_MAX_M[kind]
    est = (entry.get("lat"), entry.get("lng"))
    for term in entry.get("search") or []:
        for lang in SEARCH_LANGUAGES:
            time.sleep(REQUEST_SPACING)
            found = _http_get_json({
                "action": "wbsearchentities", "search": term, "language": lang, "uselang": lang,
                "type": "item", "limit": 7, "format": "json",
            })
            def names(r):
                return " ".join([r.get("label") or "", (r.get("match") or {}).get("text") or "",
                                 *(r.get("aliases") or [])]).lower()
            candidates = [r for r in found.get("search") or [] if not stems or any(x in names(r) for x in stems)]
            if not candidates:
                continue
            time.sleep(REQUEST_SPACING)
            coords = _coords_for([c["id"] for c in candidates])
            best = None
            for c in candidates:
                if c["id"] not in coords:
                    continue
                lat, lng, label = coords[c["id"]]
                if not in_bbox(lat, lng, bbox):
                    continue
                dist = haversine_m(est[0], est[1], lat, lng) if None not in est else 0
                if dist > max_m:
                    continue
                if best is None or dist < best[0]:
                    best = (dist, c["id"], lat, lng, label or c.get("label", ""))
            if best:
                return {"ok": True, "lat": round(best[2], 6), "lng": round(best[3], 6), "qid": best[1],
                        "label": best[4], "source": "wikidata", "searched": term,
                        "distance_from_estimate_m": round(best[0])}
    return {"ok": False, "checked_at": time.time()}


def _key(setting_key, kind, place_id):
    return f"{setting_key}:{kind}:{place_id}"


def _needs_lookup(cache, key):
    rec = cache.get(key)
    if rec is None:
        return True
    return not rec.get("ok") and time.time() - rec.get("checked_at", 0) > MISS_RETRY_SECONDS


def _places(map_data):
    for lm in map_data.get("landmarks") or []:
        yield "landmark", lm["id"], lm
    for uid, u in (map_data.get("units") or {}).items():
        yield "unit", uid, u


def resolve_all(setting_key, map_data):
    """Looks up every place the cache doesn't have yet. Safe to call repeatedly; one run per setting
    at a time. A network failure leaves those places for the next call rather than caching a miss."""
    with _lock:
        if setting_key in _running:
            return
        _running.add(setting_key)
    try:
        cache = _load_cache()
        bbox = map_data.get("bbox")
        for kind, pid, entry in _places(map_data):
            key = _key(setting_key, kind, pid)
            if not _needs_lookup(cache, key):
                continue
            if not entry.get("search"):
                cache[key] = {"ok": False, "checked_at": time.time(), "reason": "no search terms"}
                continue
            try:
                cache[key] = resolve_place(entry, kind, bbox)
            except (OSError, ValueError) as e:  # offline, timeout, bad JSON: try again next time
                print(f"[geodata] lookup failed for {key}: {e}", flush=True)
                break
            _save_cache(cache)
        _save_cache(cache)
    finally:
        with _lock:
            _running.discard(setting_key)


def start_background_resolve(setting_key, map_data):
    threading.Thread(target=resolve_all, args=(setting_key, map_data), daemon=True,
                     name=f"geodata-{setting_key}").start()


def map_status(setting_key, map_data):
    """What the frontend draws: only verified places, plus whether the map is viable at all."""
    cache = _load_cache()
    landmarks, units, unplaced, pending = [], {}, [], 0
    for kind, pid, entry in _places(map_data):
        key = _key(setting_key, kind, pid)
        rec = cache.get(key)
        if rec is None:
            pending += 1
            continue
        if not rec.get("ok"):
            unplaced.append(entry.get("name") or pid)
            continue
        placed = {"lat": rec["lat"], "lng": rec["lng"], "source": rec.get("source"), "qid": rec.get("qid")}
        if kind == "landmark":
            landmarks.append({**{k: entry[k] for k in ("id", "name", "note", "kind", "label") if k in entry}, **placed})
        else:
            units[pid] = {**placed, "where": entry.get("where", "")}
    n_lm = len(map_data.get("landmarks") or [])
    n_un = len(map_data.get("units") or {})
    layers = map_data.get("layers") or []
    viable = (
        bool(layers) and pending == 0
        and (n_lm == 0 or len(landmarks) / n_lm >= VIABILITY_LANDMARKS)
        and (n_un == 0 or len(units) / n_un >= VIABILITY_UNITS)
    )
    return {
        "viable": viable,
        "resolving": pending > 0,
        "center": map_data.get("center"), "zoom": map_data.get("zoom"),
        "layers": layers,
        "default_layer": map_data.get("default_layer") or (layers[0]["id"] if layers else None),
        "landmarks": landmarks, "units": units, "unplaced": sorted(unplaced),
        "counts": {"landmarks": [len(landmarks), n_lm], "units": [len(units), n_un], "pending": pending},
    }


def setting_map_data(setting_dir):
    path = Path(setting_dir) / "map.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_comment", None)
    # A historic layer only counts if its tiles are actually there (tools/georeference_scan.py makes
    # them); otherwise it's dropped and the next layer becomes the default.
    static_root = Path(__file__).parent / "static"
    kept = []
    for layer in data.get("layers") or []:
        local = layer.get("local_tiles")
        if local:
            folder = static_root / local
            try:
                report = json.loads((folder / "fit.json").read_text(encoding="utf-8"))
                source = (folder / "SOURCE.txt").read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                continue  # not built yet
            if report.get("rms_m", 1e9) > report.get("max_rms_allowed", 35):
                continue
            layer = {**layer, "attribution": f"{source} (fit within ~{report['rms_m']:.0f} m)"}
        kept.append(layer)
    data["layers"] = kept
    if data.get("default_layer") not in {l["id"] for l in kept}:
        data["default_layer"] = kept[0]["id"] if kept else None
    return data
