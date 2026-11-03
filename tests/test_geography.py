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
        self.assertNotIn(("rail:gatchina-luga", "train"), [(e, how) for e, how, _ in r["legs"]])
        state["geo"]["edges"]["rail:tsarskoe_selo-dno"] = {"status": "cut"}
        state["geo"]["edges"]["rail:bologoye-dno"] = {"status": "cut"}
        r = G.route(GEO, state, "gatchina", "luga", "don_cossacks")
        self.assertTrue(all(how == "foot" for _, how, _ in r["legs"]))  # no train way round: they march

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
        self.assertIn("red_guards", result["absent"])  # the long way round, by road, is days

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
        self.assertIn("kronstadt_sailors at Kronstadt", prompt)
        self.assertIn("czechoslovak_corps at Kiev", prompt)
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


class TestCountryAndLifecycle(unittest.TestCase):
    def test_the_country_order_of_battle_is_on_the_map(self):
        state = fresh()
        self.assertIn("russia_forces", state["mechanics"])
        pos = state["geo"]["positions"]
        self.assertEqual(pos["krasnov_corps"], "ostrov")
        self.assertEqual(pos["czechoslovak_corps"], "kiev")
        self.assertTrue(all(u in pos for u in G.force_units(state)))

    def test_old_saves_get_the_country_tracker(self):
        state = fresh_petrograd()
        state["mechanics"].pop("russia_forces")
        engine.mechanics.ensure_always_present(state, engine.mechanics.load_catalog("petrograd_1917"))
        self.assertIn("russia_forces", state["mechanics"])

    def test_raise_merge_split_disband(self):
        state = fresh()
        res, _ = G.handle(GEO, state, "reorganize_units", {"action": "raise", "at": "Putilov Works", "name": "Putilov Red Guard", "strength": 9000})
        new = res["raised"]["id"]
        self.assertEqual(res["raised"]["strength"], G.MAX_RAISE)
        self.assertLessEqual(res["raised"]["readiness"], 2)
        self.assertEqual(res["tracker"], "petrograd_garrison")
        again, _ = G.handle(GEO, state, "reorganize_units", {"action": "raise", "at": "Putilov Works", "name": "More", "strength": 100})
        self.assertIn("error", again)
        # merge needs them together
        bad, _ = G.handle(GEO, state, "reorganize_units", {"action": "merge", "units": [new, "red_guards"]})
        self.assertIn("error", bad)
        G.handle(GEO, state, "move_unit", {"unit": "red_guards", "to": "Putilov Works"})
        before = G.force_units(state)["red_guards"]["strength"]
        res, _ = G.handle(GEO, state, "reorganize_units", {"action": "merge", "units": ["red_guards", new]})
        self.assertEqual(res["strength"], before + G.MAX_RAISE)
        self.assertNotIn(new, G.force_units(state))
        self.assertNotIn(new, state["geo"]["positions"])
        res, _ = G.handle(GEO, state, "reorganize_units", {"action": "split", "unit": "red_guards", "strength": 2000, "name": "Narva Gate detachment"})
        det = res["new_unit"]["id"]
        self.assertEqual(state["geo"]["positions"][det], "putilov_works")
        G.handle(GEO, state, "reorganize_units", {"action": "disband", "unit": det})
        self.assertNotIn(det, G.force_units(state))

    def test_ai_cannot_conjure_a_big_unit_by_update(self):
        state = fresh_petrograd()
        data = json.loads(json.dumps(state["mechanics"]["petrograd_garrison"]["data"]))
        data["units"].append({"id": "ghost_army", "name": "Ghost army", "strength": 90000, "readiness": 5, "loyalty": 3})
        engine.mechanics.apply_mechanic_update(state, engine.mechanics.load_catalog("petrograd_1917"),
                                               {"action": "update", "type_key": "petrograd_garrison", "instance_id": "petrograd_garrison", "data": data})
        ghost = G.force_units(state)["ghost_army"]
        self.assertEqual(ghost["strength"], engine.mechanics.MAX_NEW_UNIT_STRENGTH)
        self.assertLessEqual(ghost["readiness"], 3)

    def test_history_happens_unless_the_campaign_changed_it(self):
        state = fresh()
        state["date"] = "1917-11-18"
        G.tick_day(GEO, state)  # catches up everything dated before today
        self.assertIn("volunteer_army", G.force_units(state))
        state["date"] = "1917-11-19"
        G.tick_day(GEO, state)
        self.assertIn("tekke_regiment", state["geo"]["moving"])
        self.assertTrue(all(how in ("foot", "muster") for _, how, _ in state["geo"]["moving"]["tekke_regiment"]["legs"]))  # rides, no train
        won = fresh()
        G.find_unit(won, "czechoslovak_corps")[1]["loyalty"] = 2  # the player won the Legion over
        won["date"] = "1918-05-12"
        G.tick_day(GEO, won)
        self.assertNotIn("czech_chelyabinsk", G.force_units(won))
        self.assertEqual(won["geo"]["positions"]["czechoslovak_corps"], "kiev")

    def test_fights_mix_trackers(self):
        state = fresh()
        G.handle(GEO, state, "move_unit", {"unit": "player", "to": "Gatchina"})
        state["geo"]["positions"]["red_guards"] = "gatchina"
        state["geo"]["positions"]["krasnov_corps"] = "gatchina"
        _, _, result = run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "Hold Gatchina", "player_side_attacking": False,
            "player_units": ["red_guards"], "opposing_units": ["krasnov_corps"], "terrain": "open", "place": "Gatchina"})
        self.assertNotIn("error", result)
        self.assertEqual(result.get("absent", {}), {})


class TestOffTheRails(unittest.TestCase):
    def test_marching_is_days_not_hours(self):
        state = fresh()
        r = G.route(GEO, state, "pskov", "winter_palace", "krasnov_corps", foot_only=True)
        self.assertTrue(4 * 24 < r["hours"] < 9 * 24, r["hours"])  # ~280 km of cavalry marching
        train = G.route(GEO, state, "pskov", "winter_palace", "krasnov_corps")
        self.assertLess(train["hours"], 30)

    def test_kornilov_rides_to_the_don_in_about_the_time_he_took(self):
        state = fresh()
        r = G.route(GEO, state, "bykhov", "novocherkassk", "tekke_regiment", foot_only=True)
        self.assertTrue(12 * 24 < r["hours"] < 30 * 24, r["hours"])  # he took 17 days

    def test_rivers_freeze_and_the_ice_bears_men(self):
        state = fresh()
        state["date"] = "1918-07-01"
        summer = G.route(GEO, state, "kazan", "samara", "kazan_garrison")
        state["date"] = "1918-01-15"
        winter = G.route(GEO, state, "kazan", "samara", "kazan_garrison")
        self.assertFalse(any(GEO["edges"][e]["mode"] == "river" for e, _, _ in winter["legs"] if e in GEO["edges"]))
        ice = G.route(GEO, state, "oranienbaum", "kronstadt")
        self.assertEqual(ice["legs"][0][1], "ice")
        state["date"] = "1917-10-01"
        self.assertEqual(G.route(GEO, state, "oranienbaum", "kronstadt")["legs"][0][1], "ship")

    def test_units_take_time_to_turn_out(self):
        state = fresh()
        res, _ = G.handle(GEO, state, "move_unit", {"unit": "kronstadt_sailors", "to": "Winter Palace"})
        self.assertIn("muster", res)
        self.assertTrue(5 < res["hours"] < 10, res["hours"])  # about half a day from order to embankment
        res, _ = G.handle(GEO, state, "move_unit", {"unit": "player", "to": "Smolny"})
        self.assertNotIn("muster", res)

    def test_sway_needs_the_player_there(self):
        state = fresh()
        _, _, result = run(state, "sway_unit", {"instance_id": "russia_forces", "unit_id": "czechoslovak_corps",
                                                 "attribute": "oration", "description": "x"})
        self.assertIn("isn't with", result["error"])


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
