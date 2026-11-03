"""geodata.py: map positions are accepted only when Wikidata confirms them (2026-11-02)."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import engine
import geodata

BBOX = [59.75, 29.6, 60.15, 30.6]


def fake_wikidata(items):
    """items: {qid: (label, lat, lng)}; search matches any item whose label contains the term."""
    def get(params):
        if params["action"] == "wbsearchentities":
            term = params["search"].lower()
            return {"search": [{"id": q, "label": lab} for q, (lab, _, _) in items.items() if term in lab.lower()]}
        ids = params["ids"].split("|")
        return {"entities": {q: {"labels": {"ru": {"value": items[q][0]}},
                                 "claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": items[q][1], "longitude": items[q][2]}}}}]}}
                             for q in ids if q in items}}
    return get


class TestResolvePlace(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(geodata.time, "sleep")
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def resolve(self, items, entry, kind="unit"):
        with patch.object(geodata, "_http_get_json", side_effect=fake_wikidata(items)):
            return geodata.resolve_place(entry, kind, BBOX)

    def test_accepts_a_match_near_the_estimate(self):
        rec = self.resolve({"Q1": ("Казармы лейб-гвардии Павловского полка", 59.9441, 30.3307)},
                           {"search": ["Казармы лейб-гвардии Павловского полка"], "must_contain": ["павловск"], "lat": 59.9437, "lng": 30.3317})
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["qid"], "Q1")
        self.assertEqual((rec["lat"], rec["lng"]), (59.9441, 30.3307))

    def test_rejects_a_namesake_far_away(self):
        rec = self.resolve({"Q2": ("Казармы Павловского полка", 55.75, 37.6)},  # Moscow
                           {"search": ["Казармы Павловского полка"], "must_contain": ["павловск"], "lat": 59.9437, "lng": 30.3317})
        self.assertFalse(rec["ok"])

    def test_rejects_a_match_too_far_from_the_estimate(self):
        rec = self.resolve({"Q3": ("Зимний дворец", 59.99, 30.40)},
                           {"search": ["Зимний дворец"], "must_contain": ["зимн"], "lat": 59.9399, "lng": 30.3146}, kind="landmark")
        self.assertFalse(rec["ok"])

    def test_rejects_a_label_without_the_stem(self):
        rec = self.resolve({"Q4": ("Павловск (станция)", 59.944, 30.331)},
                           {"search": ["Павловск"], "must_contain": ["казарм"], "lat": 59.9437, "lng": 30.3317})
        self.assertFalse(rec["ok"])


class TestMapStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cache = patch.object(geodata, "cache_path", return_value=Path(self.tmp.name) / "geo.json")
        self.cache.start()
        self.data = geodata.setting_map_data(engine.setting_dir("petrograd_1917"))

    def tearDown(self):
        self.cache.stop()
        self.tmp.cleanup()

    def write(self, ok_landmarks, ok_units):
        cache = {}
        for i, lm in enumerate(self.data["landmarks"]):
            cache[f"petrograd_1917:landmark:{lm['id']}"] = (
                {"ok": True, "lat": 59.94, "lng": 30.31, "qid": f"Q{i}", "source": "wikidata"} if i < ok_landmarks else {"ok": False, "checked_at": 0})
        for i, uid in enumerate(self.data["units"]):
            cache[f"petrograd_1917:unit:{uid}"] = (
                {"ok": True, "lat": 59.95, "lng": 30.33, "qid": f"U{i}", "source": "wikidata"} if i < ok_units else {"ok": False, "checked_at": 0})
        geodata._save_cache(cache)

    def test_viable_only_with_enough_verified_places(self):
        n_lm, n_un = len(self.data["landmarks"]), len(self.data["units"])
        self.write(n_lm, n_un)
        self.assertTrue(geodata.map_status("petrograd_1917", self.data)["viable"])
        self.write(n_lm // 2, n_un)
        self.assertFalse(geodata.map_status("petrograd_1917", self.data)["viable"])
        self.write(n_lm, 2)
        self.assertFalse(geodata.map_status("petrograd_1917", self.data)["viable"])

    def test_only_verified_places_are_returned(self):
        self.write(3, 4)
        status = geodata.map_status("petrograd_1917", self.data)
        self.assertEqual(len(status["landmarks"]), 3)
        self.assertEqual(len(status["units"]), 4)
        self.assertEqual(len(status["unplaced"]), len(self.data["landmarks"]) - 3 + len(self.data["units"]) - 4)
        self.assertTrue(all(lm["source"] == "wikidata" for lm in status["landmarks"]))

    def test_historic_layer_only_when_tiles_exist(self):
        self.assertNotIn("historic", [l["id"] for l in self.data["layers"]])
        self.assertEqual(self.data["default_layer"], "terrain")

    def test_resolve_all_caches_and_survives_network_failure(self):
        calls = {"n": 0}

        def flaky(params):
            calls["n"] += 1
            raise OSError("offline")
        with patch.object(geodata, "_http_get_json", side_effect=flaky), patch.object(geodata.time, "sleep"):
            geodata.resolve_all("petrograd_1917", self.data)
        self.assertEqual(calls["n"], 1)  # stops at the first failure instead of hammering
        self.assertTrue(geodata.map_status("petrograd_1917", self.data)["resolving"])  # nothing cached as a miss


if __name__ == "__main__":
    unittest.main()


class TestGeoreferenceTool(unittest.TestCase):
    """tools/georeference_scan.py's fit must catch bad control points (needs numpy, a dev-only dep)."""

    def setUp(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest("numpy not installed")
        import importlib.util
        spec = importlib.util.spec_from_file_location("georef", Path(__file__).parent.parent / "tools" / "georeference_scan.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)

    def points(self, jitter_px):
        import random
        rnd = random.Random(1)
        out = []
        for lat, lng in [(59.9399, 30.3146), (59.9488, 30.3958), (59.95, 30.3167), (59.9313, 30.3094), (59.9475, 30.376),
                         (59.9557, 30.3561), (59.9349, 30.2898), (59.9517, 30.349), (59.912, 30.3055), (59.96, 30.333)]:
            mx, my = self.tool.to_merc(lat, lng)
            out.append(((mx - 3360000) / 4 + rnd.gauss(0, jitter_px), (8400000 - my) / 4 + rnd.gauss(0, jitter_px), mx, my))
        return out

    def test_clean_points_fit_closely_and_noisy_ones_do_not(self):
        _, good = self.tool.fit(self.points(1))
        _, bad = self.tool.fit(self.points(80))
        rms = lambda r: float((r ** 2).mean() ** 0.5)
        self.assertLess(rms(good), 10)
        self.assertGreater(rms(bad), 35)
