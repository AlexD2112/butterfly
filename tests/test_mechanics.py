import unittest
from unittest.mock import patch

import engine
import mechanics
import providers


class TestLoadCatalog(unittest.TestCase):
    def test_petrograd_has_the_expected_types(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        self.assertIsNotNone(catalog)
        self.assertEqual(
            set(catalog.keys()),
            {
                "petrograd_soviet_composition", "petrograd_garrison", "red_army_command", "diplomatic_position",
                "bolshevik_central_committee", "military_revolutionary_committee",
            },
        )
        for type_key, entry in catalog.items():
            self.assertIn(entry["render_primitive"], {"seat_chart", "org_tree", "text"})
            self.assertIn("seed_data", entry)
            self.assertIn("data_shape", entry)
            self.assertIn("when_relevant", entry)
            self.assertTrue(entry.get("canon_triggers"), f"{type_key} should list at least one canon trigger")

    def test_diplomatic_position_uses_the_text_primitive(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        entry = catalog["diplomatic_position"]
        self.assertEqual(entry["render_primitive"], "text")
        self.assertIn("entries", entry["seed_data"])
        self.assertGreater(len(entry["seed_data"]["entries"]), 0)
        for item in entry["seed_data"]["entries"]:
            self.assertIn("heading", item)
            self.assertIn("body", item)

    def test_setting_without_a_catalog_returns_none(self):
        # All four real settings now have a catalog (see the per-setting tests below) - this exercises
        # the "no catalog file at all" path with a setting key that plain doesn't exist, since
        # load_catalog only ever checks for the file's presence, not setting-registry membership.
        self.assertIsNone(mechanics.load_catalog("nonexistent_setting_xyz"))

    def test_catalog_comment_key_is_stripped(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        self.assertNotIn("_comment", catalog)

    ALL_SETTINGS_WITH_CATALOGS = ["petrograd_1917", "britannia_876", "rome_59_bce", "crusades_1097"]

    def test_every_setting_catalog_is_well_formed(self):
        """One general check across all four settings, rather than duplicating the same assertions
        per setting - each entry must be internally consistent regardless of which setting it's in."""
        for setting_key in self.ALL_SETTINGS_WITH_CATALOGS:
            catalog = mechanics.load_catalog(setting_key)
            self.assertIsNotNone(catalog, f"{setting_key} should have a catalog")
            self.assertGreaterEqual(len(catalog), 3, f"{setting_key} should have at least 3 pilot types")
            for type_key, entry in catalog.items():
                with self.subTest(setting=setting_key, type_key=type_key):
                    self.assertIn(entry["render_primitive"], {"seat_chart", "org_tree", "text"})
                    self.assertIn("seed_data", entry)
                    self.assertIn("data_shape", entry)
                    self.assertIn("when_relevant", entry)
                    self.assertIn("sourcing_note", entry)
                    self.assertTrue(entry.get("canon_triggers"), "should list at least one canon trigger")
                    if entry["render_primitive"] == "seat_chart":
                        seed = entry["seed_data"]
                        self.assertEqual(
                            sum(f["seats"] for f in seed["factions"]), seed["body_size"],
                            "seat_chart seed factions should sum exactly to body_size",
                        )
                    elif entry["render_primitive"] == "org_tree":
                        self.assertIn("root", entry["seed_data"])
                        self.assertIn("name", entry["seed_data"]["root"])
                    elif entry["render_primitive"] == "text":
                        entries = entry["seed_data"]["entries"]
                        self.assertGreater(len(entries), 0)
                        for item in entries:
                            self.assertIn("heading", item)
                            self.assertIn("body", item)

    def test_britannia_rome_and_crusades_have_the_expected_types(self):
        self.assertEqual(
            set(mechanics.load_catalog("britannia_876").keys()),
            {
                "west_saxon_muster", "guthrums_host", "alfred_guthrum_negotiations",
                "alfreds_witan", "mercian_and_frankish_relations",
            },
        )
        self.assertEqual(
            set(mechanics.load_catalog("rome_59_bce").keys()),
            {
                "roman_senate_composition", "first_triumvirate_ledger", "gallic_legions_command",
                "extra_senatorial_network", "client_kings_ledger",
            },
        )
        self.assertEqual(
            set(mechanics.load_catalog("crusades_1097").keys()),
            {
                "crusader_princes_council", "byzantine_relations_ledger", "crusader_host_at_antioch",
                "antioch_siege_conditions", "regional_muslim_powers",
            },
        )


class TestApplyMechanicUpdate(unittest.TestCase):
    def setUp(self):
        self.catalog = mechanics.load_catalog("petrograd_1917")
        self.state = {"turn_number": 3, "mechanics": {}}

    def test_create_populates_state(self):
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "create", "type_key": "petrograd_soviet_composition",
                "instance_id": "soviet_ispolkom", "title": "Petrograd Soviet — Ispolkom",
                "data": {"body_size": 90, "factions": []}, "review_after_turns": 5,
                "reasoning": "Trotsky was just elected chairman.",
            },
        )
        self.assertEqual(changes[0]["kind"], "mechanic_created")
        entry = self.state["mechanics"]["soviet_ispolkom"]
        self.assertEqual(entry["type_key"], "petrograd_soviet_composition")
        self.assertEqual(entry["render_primitive"], "seat_chart")
        self.assertEqual(entry["created_turn"], 3)
        self.assertEqual(entry["next_review_turn"], 8)

    def test_create_rejects_unknown_type_key(self):
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {"action": "create", "type_key": "made_up_type", "instance_id": "x", "reasoning": "no"},
        )
        self.assertEqual(changes[0]["kind"], "mechanic_rejected")
        self.assertEqual(self.state["mechanics"], {})

    def test_create_rejects_duplicate_instance_id(self):
        self.state["mechanics"]["soviet_ispolkom"] = {"type_key": "petrograd_soviet_composition", "title": "x"}
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "create", "type_key": "petrograd_soviet_composition", "instance_id": "soviet_ispolkom",
                "title": "dup", "data": {}, "review_after_turns": 5, "reasoning": "no",
            },
        )
        self.assertEqual(changes[0]["kind"], "mechanic_rejected")
        self.assertIn("already exists", changes[0]["reason"])

    def test_create_requires_title_data_and_cadence(self):
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {"action": "create", "type_key": "petrograd_soviet_composition", "instance_id": "x", "reasoning": "no"},
        )
        self.assertEqual(changes[0]["kind"], "mechanic_rejected")
        self.assertEqual(self.state["mechanics"], {})

    def test_update_mutates_existing_entry_and_resets_review_point(self):
        mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "create", "type_key": "petrograd_garrison", "instance_id": "garrison",
                "title": "Petrograd Garrison", "data": {"root": {"name": "old"}}, "review_after_turns": 4,
                "reasoning": "setup",
            },
        )
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "update", "type_key": "petrograd_garrison", "instance_id": "garrison",
                "data": {"root": {"name": "new"}}, "review_after_turns": 2, "reasoning": "a regiment defected",
            },
        )
        self.assertEqual(changes[0]["kind"], "mechanic_updated")
        entry = self.state["mechanics"]["garrison"]
        self.assertEqual(entry["data"]["root"]["name"], "new")
        self.assertEqual(entry["updated_turn"], 3)
        self.assertEqual(entry["next_review_turn"], 5)

    def test_update_unknown_instance_is_rejected(self):
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {"action": "update", "type_key": "petrograd_garrison", "instance_id": "nope", "reasoning": "no"},
        )
        self.assertEqual(changes[0]["kind"], "mechanic_rejected")

    def test_create_with_the_text_primitive(self):
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "create", "type_key": "diplomatic_position", "instance_id": "brest_litovsk",
                "title": "Peace Negotiations — Diplomatic Ledger",
                "data": {"entries": [{"heading": "Talks open", "tag": "22 Dec 1917", "body": "Formal negotiations begin."}]},
                "review_after_turns": 3, "reasoning": "Negotiations beat just fired.",
            },
        )
        self.assertEqual(changes[0]["kind"], "mechanic_created")
        entry = self.state["mechanics"]["brest_litovsk"]
        self.assertEqual(entry["render_primitive"], "text")
        self.assertEqual(len(entry["data"]["entries"]), 1)

    def test_expire_deletes_the_entry(self):
        mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "create", "type_key": "petrograd_garrison", "instance_id": "garrison",
                "title": "Petrograd Garrison", "data": {"root": {"name": "old"}}, "review_after_turns": 4,
                "reasoning": "setup",
            },
        )
        changes = mechanics.apply_mechanic_update(
            self.state, self.catalog,
            {
                "action": "expire", "type_key": "petrograd_garrison", "instance_id": "garrison",
                "reasoning": "superseded by the Red Army",
            },
        )
        self.assertEqual(changes[0]["kind"], "mechanic_expired")
        self.assertNotIn("garrison", self.state["mechanics"])


class TestCatalogPromptSection(unittest.TestCase):
    def test_includes_canon_triggers(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        section = mechanics.catalog_prompt_section(catalog)
        self.assertIn("Canon triggers to watch for", section)
        self.assertIn("Brest-Litovsk negotiations begin", section)


class TestActiveMechanicsPromptSection(unittest.TestCase):
    def test_empty_when_no_mechanics(self):
        self.assertEqual(mechanics.active_mechanics_prompt_section({"turn_number": 1}), "")

    def test_flags_due_mechanics_and_not_others(self):
        state = {
            "turn_number": 10,
            "mechanics": {
                "due_one": {"type_key": "petrograd_garrison", "title": "Due", "next_review_turn": 9},
                "not_due": {"type_key": "petrograd_garrison", "title": "Not due", "next_review_turn": 99},
            },
        }
        section = mechanics.active_mechanics_prompt_section(state)
        self.assertIn("due_one", section)
        self.assertIn("DUE FOR REVIEW", section.split("not_due")[0]) if "not_due" in section else None
        due_line = [l for l in section.splitlines() if "due_one" in l][0]
        not_due_line = [l for l in section.splitlines() if "not_due" in l][0]
        self.assertIn("DUE FOR REVIEW", due_line)
        self.assertNotIn("DUE FOR REVIEW", not_due_line)


class FakeProvider:
    """Same minimal stand-in as test_engine_budget.py - see that file for the rationale."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.tool_specs_seen = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation):
        self.tool_specs_seen.append(tool_specs)
        result = self.script[self.calls]
        self.calls += 1
        return result

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


def make_usage():
    return {
        "provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 1000, "output_tokens": 500,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
    }


class TestEngineIntegration(unittest.TestCase):
    def test_mechanics_tool_offered_for_petrograd(self):
        provider = FakeProvider([providers.TurnResult(text_parts=["Hi."], tool_calls=[], usage=make_usage())])
        engine.run_turn(
            provider, "gpt-5.6-luna", {"campaign_log_summary": [], "turn_number": 1}, "petrograd_1917",
            "red_admiral", [], "do something",
        )
        tool_names = {t["name"] for t in provider.tool_specs_seen[0]}
        self.assertIn("propose_mechanic_update", tool_names)

    def test_mechanics_tool_absent_for_a_setting_without_a_catalog(self):
        # All four real settings now have a catalog (that's the point of this whole test file), so
        # there's no real setting left to exercise the "missing catalog" path with - a genuinely
        # nonexistent setting_key fails earlier in run_turn (resource_schema.json etc. don't exist
        # either), which would test setting-existence, not catalog-absence specifically. Mocking
        # load_catalog on a real setting isolates the one thing this test actually cares about.
        provider = FakeProvider([providers.TurnResult(text_parts=["Hi."], tool_calls=[], usage=make_usage())])
        with patch("mechanics.load_catalog", return_value=None):
            engine.run_turn(
                provider, "gpt-5.6-luna", {"campaign_log_summary": [], "turn_number": 1}, "petrograd_1917",
                "red_admiral", [], "do something",
            )
        tool_names = {t["name"] for t in provider.tool_specs_seen[0]}
        self.assertNotIn("propose_mechanic_update", tool_names)

    def test_mechanics_tool_offered_for_britannia_rome_and_crusades(self):
        for setting_key, system_key in [("britannia_876", "monarch"), ("rome_59_bce", "apollo"), ("crusades_1097", "monarch")]:
            with self.subTest(setting=setting_key):
                provider = FakeProvider([providers.TurnResult(text_parts=["Hi."], tool_calls=[], usage=make_usage())])
                engine.run_turn(
                    provider, "gpt-5.6-luna", {"campaign_log_summary": [], "turn_number": 1}, setting_key,
                    system_key, [], "do something",
                )
                tool_names = {t["name"] for t in provider.tool_specs_seen[0]}
                self.assertIn("propose_mechanic_update", tool_names)

    def test_ai_creating_a_mechanic_end_to_end_for_rome(self):
        create_input = {
            "action": "create", "type_key": "roman_senate_composition", "instance_id": "senate",
            "title": "The Roman Senate — Factional Balance", "data": {"body_size": 600, "factions": []},
            "review_after_turns": 5, "reasoning": "Bibulus's obstruction just became a live concern.",
        }
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["The Senate murmurs."],
                    tool_calls=[{"id": "1", "name": "propose_mechanic_update", "input": create_input}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=make_usage()),
            ]
        )
        state = {"campaign_log_summary": [], "turn_number": 1}
        engine.run_turn(
            provider, "gpt-5.6-luna", state, "rome_59_bce", "apollo", [], "I address the Senate",
        )
        self.assertIn("senate", state["mechanics"])
        self.assertEqual(state["mechanics"]["senate"]["render_primitive"], "seat_chart")

    def test_ai_creating_the_central_committee_mechanic_end_to_end(self):
        """Exercises one of the newly-added (2026-09-10) mechanics specifically, since the general
        catalog-structure test only checks the seed_data's shape, not the full create pipeline for
        every single new type - this is the org_tree-of-named-individuals pattern, distinct from
        petrograd_garrison's org_tree-of-military-units pattern already covered above."""
        catalog = mechanics.load_catalog("petrograd_1917")
        seed = catalog["bolshevik_central_committee"]["seed_data"]
        create_input = {
            "action": "create", "type_key": "bolshevik_central_committee", "instance_id": "cc_oct_10",
            "title": "Bolshevik Central Committee — Insurrection Vote", "data": seed,
            "review_after_turns": 2, "reasoning": "The insurrection question just came to a head.",
        }
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["The room falls quiet."],
                    tool_calls=[{"id": "1", "name": "propose_mechanic_update", "input": create_input}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=make_usage()),
            ]
        )
        state = {"campaign_log_summary": [], "turn_number": 1}
        engine.run_turn(
            provider, "gpt-5.6-luna", state, "petrograd_1917", "red_admiral", [], "I call the vote",
        )
        entry = state["mechanics"]["cc_oct_10"]
        self.assertEqual(entry["render_primitive"], "org_tree")
        stance_names = {c["name"] for c in entry["data"]["root"]["children"]}
        self.assertIn("Opposed", stance_names)

    def test_ai_creating_a_mechanic_end_to_end(self):
        create_input = {
            "action": "create", "type_key": "petrograd_soviet_composition", "instance_id": "soviet_ispolkom",
            "title": "Petrograd Soviet — Ispolkom", "data": {"body_size": 90, "factions": []},
            "review_after_turns": 5, "reasoning": "Just elected chairman - clearly relevant now.",
        }
        provider = FakeProvider(
            [
                providers.TurnResult(
                    text_parts=["You take the gavel."],
                    tool_calls=[{"id": "1", "name": "propose_mechanic_update", "input": create_input}],
                    usage=make_usage(),
                ),
                providers.TurnResult(text_parts=["Done."], tool_calls=[], usage=make_usage()),
            ]
        )
        state = {"campaign_log_summary": [], "turn_number": 1}
        narration, debug_events = engine.run_turn(
            provider, "gpt-5.6-luna", state, "petrograd_1917", "red_admiral", [], "I address the chamber",
        )
        self.assertIn("soviet_ispolkom", state["mechanics"])
        self.assertEqual(state["mechanics"]["soviet_ispolkom"]["render_primitive"], "seat_chart")
        self.assertTrue(any(ev["type"] == "mechanic_update" for ev in debug_events))


if __name__ == "__main__":
    unittest.main()
