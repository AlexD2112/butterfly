"""Tests for the 2026-11-03 geography pass: the movement graph, routing, timed movement on the clock,
bridges/lines/pickets, and fights limited to the units actually near the place."""

import json
import random
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import geodata
import geography as G
import server
from tests.test_hard_mechanics import fresh_petrograd, run

GEO = G.load("petrograd_1917")


def fresh():
    state = fresh_petrograd()
    G.ensure_initialized(state, GEO)
    return state


class TestGraph(unittest.TestCase):
    def test_every_place_is_reachable_and_edges_are_sound(self):
        state = fresh()
        for e in GEO["edges"].values():
            self.assertIn(e["a"], GEO["nodes"])
            self.assertIn(e["b"], GEO["nodes"])
            self.assertGreater(e["km"], 0)
        unreachable = [n for n in GEO["nodes"] if G.route(GEO, state, "kresty_prison", n) is None]
        self.assertEqual(unreachable, [])

    def test_banks_only_meet_at_bridges(self):
        for eid, e in GEO["edges"].items():
            if e["mode"] == "street":
                self.assertEqual(GEO["nodes"][e["a"]]["area"], GEO["nodes"][e["b"]]["area"], eid)

    def test_every_unit_starts_somewhere(self):
        state = fresh()
        self.assertEqual(set(state["geo"]["positions"]), set(G.force_units(state)))
        self.assertEqual(state["geo"]["positions"]["kronstadt_sailors"], "kronstadt")
        self.assertEqual(state["geo"]["player"], "kresty_prison")

    def test_travel_times_are_plausible(self):
        state = fresh()
        walk = G.route(GEO, state, "kresty_prison", "smolny_institute")["hours"]
        self.assertTrue(0.5 < walk < 2, walk)
        sea = G.route(GEO, state, "kronstadt", "winter_palace", "kronstadt_sailors")["hours"]
        self.assertTrue(3 < sea < 7, sea)
        moscow = G.route(GEO, state, "winter_palace", "moscow")["hours"]
        self.assertTrue(24 < moscow < 48, moscow)

    def test_place_names(self):
        state = fresh()
        self.assertEqual(G.resolve_place(GEO, state, "Smolny")[0], "smolny_institute")
        self.assertEqual(G.resolve_place(GEO, state, "the winter palace")[0], "winter_palace")
        self.assertEqual(G.resolve_place(GEO, state, "red_guards")[0], "red_guards")
        node, err = G.resolve_place(GEO, state, "Atlantis")
        self.assertIsNone(node)
        self.assertIn("Unknown place", err)


class TestBlocking(unittest.TestCase):
    def test_raised_bridges_force_the_long_way_round(self):
        state = fresh()
        short = G.route(GEO, state, "strelka", "winter_palace")["hours"]
        state["geo"]["edges"]["palace_bridge"] = {"status": "raised"}
        r = G.route_noted(GEO, state, "strelka", "winter_palace")
        self.assertGreater(r["hours"], short + 0.2)
        self.assertNotIn("palace_bridge", [l[0] for l in r["legs"]])
        self.assertTrue(any("Palace Bridge" in a for a in r["avoided"]))

    def test_unrelated_blocks_are_not_mentioned(self):
        state = fresh()
        state["geo"]["edges"]["palace_bridge"] = {"status": "raised"}
        self.assertEqual(G.route_noted(GEO, state, "finland_station", "moscow")["avoided"], [])

    def test_setting_a_route_needs_someone_there(self):
        state = fresh()
        res, _ = G.handle(GEO, state, "set_route", {"route": "Palace Bridge", "status": "raised", "by": "junkers"})
        self.assertIn("error", res)
        G.handle(GEO, state, "move_unit", {"unit": "junkers", "to": "Palace Bridge (Strelka end)"})
        res, _ = G.handle(GEO, state, "set_route", {"route": "Palace Bridge", "status": "raised", "by": "junkers"})
        self.assertEqual(res["status"], "raised")
        res, _ = G.handle(GEO, state, "set_route", {"route": "Sampsonievsky Bridge", "status": "raised", "by": "railwaymen"})
        self.assertIn("error", res)

    def test_railwaymen_can_cut_a_line_and_men_still_march(self):
        state = fresh()
        res, _ = G.handle(GEO, state, "set_route", {"route": "Gatchina - Luga", "status": "cut", "by": "railwaymen"})
        self.assertEqual(res["status"], "cut")
        r = G.route(GEO, state, "gatchina", "luga", "don_cossacks")
        self.assertTrue(all(how == "foot" for _, how, _ in r["legs"]))

    def test_a_picket_stops_the_other_side_only(self):
        state = fresh()
        G.handle(GEO, state, "move_unit", {"unit": "junkers", "to": "Liteyny Bridge (Liteyny end)"})
        res, _ = G.handle(GEO, state, "set_route", {"route": "Liteyny Bridge", "hold": True, "by": "junkers"})
        self.assertEqual(res["held_by"], "junkers")
        red = G.route(GEO, state, "liteyny_bridge_n", "liteyny_bridge_s", "red_guards")
        self.assertNotIn("liteyny_bridge", [l[0] for l in red["legs"]])
        cossacks = G.route(GEO, state, "liteyny_bridge_n", "liteyny_bridge_s", "don_cossacks")
        self.assertEqual([l[0] for l in cossacks["legs"]], ["liteyny_bridge"])


class TestMovement(unittest.TestCase):
    def test_short_moves_finish_now(self):
        state = fresh()
        res, changes = G.handle(GEO, state, "move_unit", {"unit": "kronstadt_sailors", "to": "Winter Palace"})
        self.assertTrue(res["arrived"])
        self.assertEqual(state["geo"]["positions"]["kronstadt_sailors"], "winter_palace")

    def test_long_moves_arrive_as_the_clock_runs(self):
        state = fresh()
        _, events, _ = run(state, "move_unit", {"unit": "don_cossacks", "to": "Moscow"})
        self.assertIn("don_cossacks", state["geo"]["moving"])
        self.assertEqual(events[0]["type"], "geography")
        _, events, result = run(state, "propose_state_update", {"reasoning": "travel", "days_elapsed": 2})
        self.assertEqual(state["geo"]["positions"]["don_cossacks"], "moscow")
        self.assertNotIn("don_cossacks", state["geo"]["moving"])
        self.assertTrue(any("arrived at Moscow" in n for n in result.get("world_events", [])))

    def test_a_line_cut_behind_a_moving_train_halts_it(self):
        state = fresh()
        G.handle(GEO, state, "move_unit", {"unit": "don_cossacks", "to": "Pskov"})
        G.handle(GEO, state, "set_route", {"route": "Gatchina - Luga", "status": "cut", "by": "railwaymen"})
        changes = G.tick_day(GEO, state)
        self.assertEqual(state["geo"]["positions"]["don_cossacks"], "gatchina")
        self.assertEqual(changes[0]["status"], "halted")

    def test_the_player_moves_and_is_told_the_days(self):
        state = fresh()
        res, _ = G.handle(GEO, state, "move_unit", {"unit": "player", "to": "Helsingfors"})
        self.assertEqual(state["geo"]["player"], "helsingfors")
        self.assertIn("days_elapsed", res["note"])

    def test_new_units_are_placed_directly(self):
        state = fresh()
        units = state["mechanics"]["petrograd_garrison"]["data"]["units"]
        units.append({"id": "third_cavalry", "name": "3rd Cavalry Corps", "kind": "cossack", "strength": 5000, "readiness": 3, "loyalty": -2})
        res, _ = G.handle(GEO, state, "move_unit", {"unit": "third_cavalry", "to": "Pskov"})
        self.assertEqual(res["placed_at"], "Pskov")


class TestConfrontationReach(unittest.TestCase):
    def test_far_away_units_are_left_out(self):
        state = fresh()
        _, events, result = run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "Take the Winter Palace", "player_side_attacking": True,
            "player_units": ["red_guards", "kronstadt_sailors"], "opposing_units": ["junkers"], "terrain": "fortified",
            "place": "Winter Palace"})
        self.assertIn("kronstadt_sailors", result["absent"])
        self.assertIn("Kronstadt", result["absent"]["kronstadt_sailors"])

    def test_place_is_required_and_cut_off_units_cannot_come(self):
        state = fresh()
        _, _, result = run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "x", "player_side_attacking": True,
            "player_units": ["red_guards"], "terrain": "street"})
        self.assertIn("error", result)
        for eid, e in GEO["edges"].items():
            if e["mode"] in ("bridge", "rail") and e["a"] != "peter_and_paul_fortress" and e["b"] != "peter_and_paul_fortress":
                state["geo"]["edges"][eid] = {"status": "blocked"}
        _, _, result = run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "x", "player_side_attacking": True,
            "player_units": ["red_guards"], "terrain": "street", "place": "Winter Palace"})
        self.assertIn("error", result)
        self.assertIn("cut off", result["absent"]["red_guards"])

    def test_winners_hold_the_place(self):
        state = fresh()
        with patch("random.random", return_value=0.99):
            _, _, result = run(state, "resolve_confrontation", {
                "instance_id": "petrograd_garrison", "objective": "Take the Telephone Exchange", "player_side_attacking": True,
                "player_units": ["red_guards", "pavlovsky"], "opposing_units": [], "terrain": "street",
                "place": "Central Telephone Exchange"})
        if result["player_outcome"] in ("uncontested_success", "victory", "decisive_victory"):
            self.assertEqual(state["geo"]["positions"]["red_guards"], "central_telephone_exchange")


class TestPromptAndTools(unittest.TestCase):
    def test_prompt_has_geography_not_the_raw_dump(self):
        state = fresh()
        prompt = engine.build_system_prompt(state, "petrograd_1917", "red_admiral")
        self.assertIn("# GEOGRAPHY", prompt)
        self.assertIn("Kronstadt sailors [kronstadt_sailors] at Kronstadt", prompt)
        self.assertNotIn('"positions"', prompt)

    def test_tools_are_offered_and_fights_need_a_place(self):
        specs = engine.build_tool_specs(engine.get_system("red_admiral"), engine.mechanics.load_catalog("petrograd_1917"),
                                        engine.dynamics.load("petrograd_1917"), True, geo=GEO)
        names = {s["name"] for s in specs}
        self.assertTrue({"route_info", "move_unit", "set_route"} <= names)
        conf = next(s for s in specs if s["name"] == "resolve_confrontation")
        self.assertIn("place", conf["parameters"]["required"])

    def test_settings_without_geography_are_untouched(self):
        self.assertIsNone(G.load("rome_59_bce"))


class TestMapEndpoint(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "t.db"
        db.init_db()
        server.app.config["TESTING"] = True
        self.client = server.app.test_client()

    def tearDown(self):
        db.DB_FILE = self._orig
        self._tmpdir.cleanup()

    def test_map_carries_the_graph_and_state_carries_positions(self):
        with patch.object(geodata, "cache_path", return_value=Path(self._tmpdir.name) / "geo.json"):
            data = self.client.get("/api/settings/petrograd_1917/map").get_json()
        self.assertTrue(any(e["mode"] == "bridge" for e in data["graph"]["edges"]))
        self.assertFalse(any(e["mode"] == "street" for e in data["graph"]["edges"]))
        cid = self.client.post("/api/characters", json={"type": "premade", "setting_key": "petrograd_1917"}).get_json()["character_id"]
        state = self.client.get(f"/api/character/{cid}/state").get_json()["state"]
        self.assertEqual(state["geo"]["player"], "kresty_prison")


if __name__ == "__main__":
    unittest.main()
