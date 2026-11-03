"""Tests for the 2026-10-26 hard-mechanics pass: degrees of success, at-stake resources, broken
commitments, validated trackers, code-resolved votes/appeals/confrontations (operations.py), and the
clock/world/canon gates (dynamics.py) - mostly exercised through the Petrograd 1917 setting, which is
the one setting with a dynamics.json so far."""

import copy
import json
import random
import unittest
from unittest.mock import patch

import dynamics
import engine
import mechanics
import operations
import providers
from systems._shared import core
from systems.red_admiral import mechanics as red_admiral


class FakeProvider:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.prompts = []
        self.tool_specs_seen = []
        self.tool_results = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation):
        self.prompts.append(system_prompt)
        self.tool_specs_seen.append(tool_specs)
        result = self.script[self.calls]
        self.calls += 1
        return result

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        self.tool_results.append(results)
        conversation.append(("tool_results", results))


def one_tool_turn(name, inp):
    return [
        providers.TurnResult(text_parts=["..."], tool_calls=[{"id": "1", "name": name, "input": inp}], usage=None),
        providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=None),
    ]


def fresh_petrograd():
    state = engine.load_premade_character_state("petrograd_1917")
    state.pop("_is_new", None)
    return state


def run(state, name, inp, allow_time_advance=True):
    provider = FakeProvider(one_tool_turn(name, inp))
    narration, events = engine.run_turn(
        provider, "m", state, "petrograd_1917", "red_admiral", [], "go", allow_time_advance=allow_time_advance,
    )
    return provider, events, json.loads(provider.tool_results[0][0]["content"]) if provider.tool_results else None


def fixed_check(degree, roll=10, total=15, dc=12):
    def _roll(character, attribute, skill, dc_, target_npc=None):
        return {"attribute": attribute, "skill": skill, "tier": "untrained", "attribute_score": 10,
                "attribute_modifier": 0, "proficiency_bonus": 2, "feat_bonus": 0, "feat_bonus_source": None,
                "modifier": 0, "roll": roll, "total": total, "dc": dc_, "margin": total - dc_, "degree": degree,
                "gm_instruction": "", "target_npc": target_npc}
    return _roll


class TestDegreesOfSuccess(unittest.TestCase):
    def test_margin_bands(self):
        cases = [
            (20, 5, 30, "critical_success"),   # natural 20 regardless of total
            (1, 30, 12, "critical_failure"),   # natural 1 regardless of total
            (15, 25, 15, "critical_success"),  # beat by 10
            (15, 20, 15, "strong_success"),    # beat by 5
            (15, 19, 15, "success"),           # beat by 4
            (15, 15, 15, "success"),           # met exactly
            (15, 14, 15, "success_at_cost"),   # missed by 1
            (15, 12, 15, "success_at_cost"),   # missed by 3
            (15, 11, 15, "failure"),           # missed by 4
            (15, 6, 15, "failure"),            # missed by 9
            (15, 5, 15, "critical_failure"),   # missed by 10
        ]
        for roll, total, dc, expected in cases:
            with self.subTest(roll=roll, total=total, dc=dc):
                self.assertEqual(core.degree_for(roll, total, dc), expected)

    def test_roll_check_reports_margin_and_guidance(self):
        character = {"attributes": {a: 10 for a in core.ATTRIBUTES}, "level": 1, "skills": {}}
        with patch("random.randint", return_value=11):
            result = core.roll_check(character, "oration", None, 12)
        self.assertEqual(result["margin"], -1)
        self.assertEqual(result["degree"], "success_at_cost")
        self.assertIn("cost", result["gm_instruction"])

    def test_success_at_cost_does_not_trigger_failure_liabilities(self):
        state = {"enmity": {"kamenev": {"pips": 0}}, "character": {"liabilities": [{"id": "arrogance", "name": "Arrogance"}]}}
        check = {"degree": "success_at_cost", "target_npc": "kamenev", "attribute": "presence"}
        self.assertEqual(red_admiral.apply_auto_liability_effects(state, check), [])
        check["degree"] = "failure"
        self.assertEqual(len(red_admiral.apply_auto_liability_effects(state, check)), 1)


class TestAtStake(unittest.TestCase):
    def test_failure_charges_the_named_resource(self):
        state = fresh_petrograd()
        before = state["resources"]["party_standing"]
        with patch.object(red_admiral, "roll_check", fixed_check("failure")):
            _, _, result = run(state, "request_check", {"attribute": "presence", "dc": 12, "description": "x", "at_stake": "party_standing"})
        self.assertEqual(state["resources"]["party_standing"], before + core.AT_STAKE_DELTAS["failure"])
        self.assertEqual(result["at_stake_applied"], {"party_standing": core.AT_STAKE_DELTAS["failure"]})

    def test_derived_resource_cannot_be_at_stake(self):
        state = fresh_petrograd()
        before = state["resources"]["garrison_loyalty"]
        with patch.object(red_admiral, "roll_check", fixed_check("critical_failure")):
            run(state, "request_check", {"attribute": "oration", "dc": 12, "description": "x", "at_stake": "garrison_loyalty"})
        self.assertEqual(state["resources"]["garrison_loyalty"], before)


class TestBrokenCommitments(unittest.TestCase):
    def test_breaking_an_active_commitment_costs_trust_and_enmity(self):
        state = {
            "character": {"flags": {}, "liabilities": [], "xp": 0, "level": 1}, "resources": {},
            "relationships": {"spiridonova": {"trust": 2}}, "enmity": {"spiridonova": {"pips": 0}},
            "commitments": {"land": {"description": "Land decree", "parties": ["Spiridonova", "the Left SRs"], "status": "active"}},
            "canon_events": {}, "divergence_log": [], "turn_number": 1,
        }
        update = {"commitments_upserted": {"land": {"description": "Land decree", "parties": ["Spiridonova", "the Left SRs"], "status": "broken"}}, "reasoning": "x"}
        core.apply_state_update(state, update, {"resources": {}})
        self.assertEqual(state["relationships"]["spiridonova"]["trust"], 0)
        self.assertEqual(state["enmity"]["spiridonova"]["pips"], 1)
        self.assertNotIn("the_left_srs", state["relationships"])  # prose parties aren't invented as NPCs

    def test_creating_a_commitment_as_broken_costs_nothing(self):
        state = {"character": {"flags": {}}, "relationships": {"x": {"trust": 2}}, "enmity": {}, "commitments": {},
                 "canon_events": {}, "divergence_log": [], "turn_number": 1, "resources": {}}
        core.apply_state_update(state, {"commitments_upserted": {"c": {"parties": ["x"], "status": "broken"}}}, {"resources": {}})
        self.assertEqual(state["relationships"]["x"]["trust"], 2)


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.catalog = mechanics.load_catalog("petrograd_1917")
        self.state = fresh_petrograd()

    def test_lean_moves_one_step_per_update(self):
        data = copy.deepcopy(self.state["mechanics"]["petrograd_soviet"]["data"])
        mensheviks = next(f for f in data["factions"] if f["name"].startswith("Mensheviks"))
        mensheviks["lean"] = 3
        changes = mechanics.apply_mechanic_update(self.state, self.catalog, {
            "action": "update", "type_key": "petrograd_soviet_composition", "instance_id": "petrograd_soviet", "data": data, "reasoning": "x"})
        self.assertEqual(changes[0]["kind"], "mechanic_updated")
        after = next(f for f in self.state["mechanics"]["petrograd_soviet"]["data"]["factions"] if f["name"].startswith("Mensheviks"))
        self.assertEqual(after["lean"], -2)
        self.assertTrue(changes[0]["adjusted"])

    def test_large_seat_swings_are_rejected(self):
        data = copy.deepcopy(self.state["mechanics"]["petrograd_soviet"]["data"])
        data["factions"][0]["seats"] += 300
        data["factions"][-1]["seats"] -= 155
        data["factions"][-2]["seats"] -= 145
        changes = mechanics.apply_mechanic_update(self.state, self.catalog, {
            "action": "update", "type_key": "petrograd_soviet_composition", "instance_id": "petrograd_soviet", "data": data, "reasoning": "x"})
        self.assertEqual(changes[0]["kind"], "mechanic_rejected")

    def test_fixed_chart_properties_survive_updates(self):
        data = copy.deepcopy(self.state["mechanics"]["petrograd_soviet"]["data"])
        data["influence_resource"] = "party_standing"
        mechanics.apply_mechanic_update(self.state, self.catalog, {
            "action": "update", "type_key": "petrograd_soviet_composition", "instance_id": "petrograd_soviet", "data": data, "reasoning": "x"})
        self.assertEqual(self.state["mechanics"]["petrograd_soviet"]["data"]["influence_resource"], "soviet_authority")

    def test_unit_loyalty_and_strength_are_rate_limited(self):
        data = copy.deepcopy(self.state["mechanics"]["petrograd_garrison"]["data"])
        junkers = next(u for u in data["units"] if u["id"] == "junkers")
        junkers["loyalty"] = 3
        guards = next(u for u in data["units"] if u["id"] == "red_guards")
        guards["strength"] = 100000
        changes = mechanics.apply_mechanic_update(self.state, self.catalog, {
            "action": "update", "type_key": "petrograd_garrison", "instance_id": "petrograd_garrison", "data": data, "reasoning": "x"})
        units = {u["id"]: u for u in self.state["mechanics"]["petrograd_garrison"]["data"]["units"]}
        self.assertEqual(units["junkers"]["loyalty"], -2)
        self.assertLess(units["red_guards"]["strength"], 16000)
        self.assertTrue(changes[0]["adjusted"])


class TestVotes(unittest.TestCase):
    def setUp(self):
        self.state = fresh_petrograd()

    def test_tallies_cover_every_seat(self):
        result, _ = operations.call_vote(self.state, red_admiral, {
            "instance_id": "petrograd_soviet", "motion": "No confidence", "player_position": "for"}, rng=random.Random(1))
        self.assertEqual(result["yes"] + result["no"] + result["abstain"], 1000)

    def test_the_presidium_vote_usually_passes_but_not_always(self):
        passes = 0
        for seed in range(200):
            state = fresh_petrograd()
            result, _ = operations.call_vote(state, red_admiral, {
                "instance_id": "petrograd_soviet", "motion": "No confidence", "player_position": "for"},
                influence_value=44, rng=random.Random(seed))
            passes += result["passed"]
        self.assertGreater(passes, 150)

    def test_the_same_room_votes_down_the_players_opponents(self):
        result, _ = operations.call_vote(self.state, red_admiral, {
            "instance_id": "petrograd_soviet", "motion": "Confidence in the coalition", "player_position": "against"},
            rng=random.Random(3))
        self.assertFalse(result["passed"])

    def test_a_speech_is_rolled_by_the_application_and_moves_persuadable_blocs(self):
        def tally(degree):
            state = fresh_petrograd()
            with patch.object(red_admiral, "roll_check", fixed_check(degree)):
                result, _ = operations.call_vote(state, red_admiral, {
                    "instance_id": "petrograd_soviet", "motion": "m", "player_position": "for",
                    "speech_attribute": "oration"}, rng=random.Random(7))
            internationalists = next(b for b in result["blocs"] if b["name"] == "Menshevik-Internationalists")
            return internationalists["yes"], result["speech"]["degree"]
        good, degree = tally("critical_success")
        bad, _ = tally("critical_failure")
        self.assertEqual(degree, "critical_success")
        self.assertGreater(good, bad)

    def test_custom_axis_charts_follow_motion_alignment(self):
        self.state["mechanics"]["cc"] = {
            "type_key": "bolshevik_central_committee", "title": "CC", "render_primitive": "seat_chart",
            "data": copy.deepcopy(mechanics.load_catalog("petrograd_1917")["bolshevik_central_committee"]["seed_data"]),
        }
        rise, _ = operations.call_vote(self.state, red_admiral, {
            "instance_id": "cc", "motion": "Rise now", "player_position": "for", "motion_alignment": 1}, rng=random.Random(2))
        wait, _ = operations.call_vote(self.state, red_admiral, {
            "instance_id": "cc", "motion": "Wait for the Congress", "player_position": "for", "motion_alignment": -1}, rng=random.Random(2))
        self.assertTrue(rise["passed"])
        self.assertFalse(wait["passed"])
        self.assertEqual(rise["yes"] + rise["no"] + rise["abstain"], 12)  # 11 members + the player

    def test_vote_fires_its_canon_event_only_through_the_gate(self):
        state = fresh_petrograd()  # 4 September: presidium_no_confidence isn't allowed before the 5th
        with patch.object(random, "random", return_value=0.5), patch.object(random, "uniform", return_value=0.0):
            _, events, result = run(state, "call_vote", {"instance_id": "petrograd_soviet", "motion": "No confidence",
                                                          "player_position": "for", "canon_event": "presidium_no_confidence"})
        self.assertTrue(result["passed"])
        self.assertEqual(result["canon_event"][0]["kind"], "canon_event_rejected")
        self.assertEqual(state["canon_events"]["presidium_no_confidence"]["status"], "pending")

        state["date"] = "1917-09-09"
        with patch.object(random, "random", return_value=0.5), patch.object(random, "uniform", return_value=0.0):
            run(state, "call_vote", {"instance_id": "petrograd_soviet", "motion": "No confidence",
                                     "player_position": "for", "canon_event": "presidium_no_confidence"})
        self.assertEqual(state["canon_events"]["presidium_no_confidence"]["status"], "fired")
        self.assertEqual(state["resources"]["soviet_authority"], 44 + 8)


class TestSwayUnit(unittest.TestCase):
    def setUp(self):
        self.state = fresh_petrograd()

    def test_dc_follows_loyalty_and_influence(self):
        self.assertEqual(operations.sway_dc(0), 11)
        self.assertEqual(operations.sway_dc(-2), 15)
        self.assertEqual(operations.sway_dc(2), 7)
        self.assertEqual(operations.sway_dc(0, influence_value=80), 9)
        self.assertEqual(operations.sway_dc(0, influence_value=20), 13)

    def test_success_moves_loyalty_and_one_attempt_per_day(self):
        with patch.object(red_admiral, "roll_check", fixed_check("success")):
            result, _ = operations.sway_unit(self.state, red_admiral, {
                "instance_id": "petrograd_garrison", "unit_id": "preobrazhensky", "attribute": "oration", "description": "x"})
            again, _ = operations.sway_unit(self.state, red_admiral, {
                "instance_id": "petrograd_garrison", "unit_id": "preobrazhensky", "attribute": "oration", "description": "x"})
        self.assertEqual((result["loyalty_before"], result["loyalty_after"]), (0, 1))
        self.assertIn("error", again)

    def test_stand_down_only_neutralizes(self):
        with patch.object(red_admiral, "roll_check", fixed_check("critical_success")):
            result, _ = operations.sway_unit(self.state, red_admiral, {
                "instance_id": "petrograd_garrison", "unit_id": "junkers", "attribute": "oration",
                "direction": "stand_down", "description": "x"})
        self.assertEqual(result["loyalty_after"], -1)

    def test_sway_through_the_engine_updates_the_derived_resource(self):
        before = self.state["resources"]["garrison_loyalty"]
        with patch.object(red_admiral, "roll_check", fixed_check("critical_success")):
            run(self.state, "sway_unit", {"instance_id": "petrograd_garrison", "unit_id": "semyonovsky",
                                          "attribute": "oration", "description": "x"})
        self.assertGreater(self.state["resources"]["garrison_loyalty"], before)

    def test_a_critical_failure_costs_personal_safety(self):
        before = self.state["resources"]["personal_safety"]
        with patch.object(red_admiral, "roll_check", fixed_check("critical_failure")):
            run(self.state, "sway_unit", {"instance_id": "petrograd_garrison", "unit_id": "semyonovsky",
                                          "attribute": "oration", "description": "x"})
        self.assertEqual(self.state["resources"]["personal_safety"], before + core.AT_STAKE_DELTAS["critical_failure"])


class TestConfrontation(unittest.TestCase):
    def setUp(self):
        self.state = fresh_petrograd()

    def units(self):
        return {u["id"]: u for u in self.state["mechanics"]["petrograd_garrison"]["data"]["units"]}

    def test_waverers_refuse_and_an_unopposed_move_succeeds(self):
        result, _ = operations.resolve_confrontation(self.state, red_admiral, {
            "instance_id": "petrograd_garrison", "objective": "The telegraph", "player_side_attacking": True,
            "player_units": ["red_guards", "volynsky"], "opposing_units": ["preobrazhensky"], "terrain": "street"},
            rng=random.Random(1))
        self.assertEqual(result["player_outcome"], "uncontested_success")
        refused = {r["unit"] for r in result["refused_to_fight"]}
        self.assertEqual(refused, {"Volynsky Regiment", "Preobrazhensky Regiment"})

    def test_nobody_willing_means_the_move_collapses(self):
        result, _ = operations.resolve_confrontation(self.state, red_admiral, {
            "instance_id": "petrograd_garrison", "objective": "x", "player_side_attacking": True,
            "player_units": ["volynsky"], "opposing_units": ["junkers"], "terrain": "street"}, rng=random.Random(1))
        self.assertEqual(result["player_outcome"], "uncontested_failure")

    def test_a_real_fight_costs_both_sides(self):
        before = self.units()["junkers"]["strength"]
        before_rg = self.units()["red_guards"]["strength"]
        result, _ = operations.resolve_confrontation(self.state, red_admiral, {
            "instance_id": "petrograd_garrison", "objective": "Winter Palace", "player_side_attacking": True,
            "player_units": ["red_guards"], "opposing_units": ["junkers"], "terrain": "fortified"}, rng=random.Random(4))
        self.assertNotIn(result["player_outcome"], ("uncontested_success", "uncontested_failure", "no_fight"))
        self.assertLess(self.units()["junkers"]["strength"], before)
        self.assertLess(self.units()["red_guards"]["strength"], before_rg)

    def test_adhoc_opposition_is_capped(self):
        result, _ = operations.resolve_confrontation(self.state, red_admiral, {
            "instance_id": "petrograd_garrison", "objective": "x", "player_side_attacking": False,
            "player_units": ["red_guards"], "terrain": "street",
            "adhoc_opposition": [{"name": "Krasnov's Cossacks", "strength": 999999, "readiness": 9}] * 5},
            rng=random.Random(1))
        self.assertEqual(len(result["took_the_field"]["opposing"]), operations.MAX_ADHOC_UNITS)
        self.assertLessEqual(result["opposing_power"], operations.MAX_ADHOC_UNITS * operations.MAX_ADHOC_STRENGTH * 1.25)

    def test_attacking_before_the_party_decides_has_political_costs(self):
        state = fresh_petrograd()
        before = state["resources"]["party_standing"]
        _, _, result = run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "Winter Palace", "player_side_attacking": True,
            "player_units": ["red_guards", "kronstadt_sailors"], "opposing_units": ["junkers"], "terrain": "fortified",
            "place": "Winter Palace"})
        self.assertLessEqual(state["resources"]["party_standing"], before - 6)
        self.assertTrue(any("July Days" in n for n in result.get("world_events", [])))

        state = fresh_petrograd()
        state["canon_events"]["central_committee_insurrection_vote"]["status"] = "fired"
        before = state["resources"]["party_standing"]
        run(state, "resolve_confrontation", {
            "instance_id": "petrograd_garrison", "objective": "Winter Palace", "player_side_attacking": True,
            "player_units": ["red_guards", "kronstadt_sailors"], "opposing_units": ["junkers"], "terrain": "fortified",
            "place": "Winter Palace"})
        self.assertEqual(state["resources"]["party_standing"], before)

    def test_unknown_units_are_an_error_not_a_guess(self):
        result, _ = operations.resolve_confrontation(self.state, red_admiral, {
            "instance_id": "petrograd_garrison", "objective": "x", "player_side_attacking": True,
            "player_units": ["the_whole_army"], "terrain": "open"})
        self.assertIn("error", result)


class TestClockAndWorld(unittest.TestCase):
    def setUp(self):
        self.dyn = dynamics.load("petrograd_1917")
        self.schema = engine.load_json("petrograd_1917", "resource_schema.json")

    def test_starting_state_is_the_release_from_kresty(self):
        state = fresh_petrograd()
        self.assertEqual(state["date"], "1917-09-04")
        self.assertEqual(state["canon_events"]["trotsky_released_from_kresty"]["status"], "fired")
        self.assertEqual(state["canon_events"]["trotsky_elected_chairman"]["status"], "pending")
        self.assertFalse(state["character"]["flags"]["soviet_chairman"])
        self.assertIn("petrograd_soviet", state["mechanics"])
        self.assertIn("petrograd_garrison", state["mechanics"])
        # the stored starting value matches what the application derives from the order of battle
        dynamics.recompute_derived(state, self.dyn)
        self.assertEqual(state["resources"]["garrison_loyalty"], 63)

    def test_days_elapsed_moves_the_date_drifts_the_world_and_fires_locked_events(self):
        state = fresh_petrograd()
        _, events, result = run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 7})
        self.assertEqual(state["date"], "1917-09-11")
        self.assertLess(state["world"]["government_stability"], 30)
        self.assertLess(state["world"]["bread_supply"], 40)
        self.assertEqual(result["date_now"], "1917-09-11")
        _, _, result = run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 7})
        self.assertEqual(state["canon_events"]["democratic_conference"]["status"], "fired")
        self.assertIn("canon event fired: democratic_conference", result["world_events"])

    def test_days_are_capped(self):
        state = fresh_petrograd()
        run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 60})
        self.assertEqual(state["date"], "1917-09-11")

    def test_multiplayer_turns_never_move_the_clock(self):
        state = fresh_petrograd()
        provider, _, result = run(state, "propose_state_update", {"reasoning": "x", "days_elapsed": 3}, allow_time_advance=False)
        self.assertEqual(state["date"], "1917-09-04")
        self.assertEqual(result["rejected"][0]["kind"], "time_ignored")
        spec = next(s for s in provider.tool_specs_seen[0] if s["name"] == "propose_state_update")
        self.assertNotIn("days_elapsed", spec["parameters"]["properties"])

    def test_locked_events_cannot_be_fired_early_by_the_gm(self):
        state = fresh_petrograd()
        _, _, result = run(state, "propose_state_update", {"reasoning": "x", "canon_event_triggered": "lenin_returns_to_petrograd"})
        self.assertEqual(state["canon_events"]["lenin_returns_to_petrograd"]["status"], "pending")
        self.assertIn("locked", result["rejected"][0]["reason"])

    def test_contingent_events_report_what_is_missing(self):
        state = fresh_petrograd()
        _, _, result = run(state, "propose_state_update", {"reasoning": "x", "canon_event_triggered": "october_revolution"})
        reason = result["rejected"][0]["reason"]
        self.assertIn("central_committee_insurrection_vote", reason)
        self.assertIn("garrison_loyalty >= 72", reason)

    def test_derived_resource_deltas_are_ignored(self):
        state = fresh_petrograd()
        _, _, result = run(state, "propose_state_update", {"reasoning": "x", "resource_deltas": {"garrison_loyalty": 10, "party_standing": 2}})
        self.assertEqual(state["resources"]["garrison_loyalty"], 63)
        self.assertEqual(state["resources"]["party_standing"], 54)
        self.assertEqual(result["rejected"][0]["kind"], "derived_resource_ignored")

    def test_world_deltas_are_clamped(self):
        state = fresh_petrograd()
        run(state, "propose_state_update", {"reasoning": "x", "world_deltas": {"government_stability": -40}})
        self.assertEqual(state["world"]["government_stability"], 25)

    def test_arrest_threshold_rolls_and_applies_the_result(self):
        state = fresh_petrograd()
        state["resources"]["personal_safety"] = 15
        with patch.object(red_admiral, "roll_check", fixed_check("failure")):
            changes = dynamics.check_thresholds(state, self.dyn, self.schema, red_admiral)
        self.assertTrue(state["character"]["flags"]["in_prison"])
        self.assertTrue(any(c.get("kind") == "threshold" and c["id"] == "arrest_attempt" for c in changes))
        # in prison: the attempt doesn't repeat
        self.assertEqual(dynamics.check_thresholds(state, self.dyn, self.schema, red_admiral), [])

    def test_arrest_threshold_respects_its_cooldown(self):
        state = fresh_petrograd()
        state["resources"]["personal_safety"] = 10
        with patch.object(red_admiral, "roll_check", fixed_check("success")):
            dynamics.check_thresholds(state, self.dyn, self.schema, red_admiral)
            state["resources"]["personal_safety"] = 10
            self.assertEqual(dynamics.check_thresholds(state, self.dyn, self.schema, red_admiral), [])
            state["date"] = "1917-09-10"
            self.assertTrue(dynamics.check_thresholds(state, self.dyn, self.schema, red_admiral))

    def test_old_saves_are_upgraded_without_losing_anything(self):
        old = {"date": "1917-09-25", "turn_number": 12, "resources": {"garrison_loyalty": 50},
               "canon_events": {"trotsky_elected_chairman": {"status": "fired", "type": "locked"}}, "character": {"flags": {}}}
        dynamics.ensure_initialized(old, self.dyn)
        self.assertEqual(old["canon_events"]["trotsky_elected_chairman"]["status"], "fired")
        self.assertEqual(old["canon_events"]["mrc_formed"]["status"], "pending")
        self.assertEqual(old["world"]["government_stability"], 30)
        dynamics.recompute_derived(old, self.dyn)
        self.assertEqual(old["resources"]["garrison_loyalty"], 50)  # no tracker yet: left alone

    def test_prompt_carries_the_clock(self):
        state = fresh_petrograd()
        provider = FakeProvider([providers.TurnResult(text_parts=["Hi."], tool_calls=[], usage=None)])
        engine.run_turn(provider, "m", state, "petrograd_1917", "red_admiral", [], "hello")
        prompt = provider.prompts[0]
        self.assertIn("# THE CLOCK", prompt)
        self.assertIn("Second All-Russian Congress of Soviets: 1917-10-25 (51 days away)", prompt)
        self.assertIn("call_vote", {s["name"] for s in provider.tool_specs_seen[0]})
        self.assertIn("Petrograd Soviet — Plenum", prompt)

    def test_settings_without_dynamics_are_untouched(self):
        state = engine.load_premade_character_state("britannia_876")
        date = state.get("date")
        provider = FakeProvider(one_tool_turn("propose_state_update", {"reasoning": "x", "days_elapsed": 5}))
        engine.run_turn(provider, "m", state, "britannia_876", "monarch", [], "go")
        self.assertEqual(state.get("date"), date)
        self.assertNotIn("world", state)
        self.assertNotIn("# THE CLOCK", provider.prompts[0])


if __name__ == "__main__":
    unittest.main()
