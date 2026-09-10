import unittest

import engine
import mechanics
import providers


class TestLoadCatalog(unittest.TestCase):
    def test_petrograd_has_the_four_pilot_types(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        self.assertIsNotNone(catalog)
        self.assertEqual(
            set(catalog.keys()),
            {"petrograd_soviet_composition", "petrograd_garrison", "red_army_command", "diplomatic_position"},
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
        self.assertIsNone(mechanics.load_catalog("britannia_876"))

    def test_catalog_comment_key_is_stripped(self):
        catalog = mechanics.load_catalog("petrograd_1917")
        self.assertNotIn("_comment", catalog)


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
        provider = FakeProvider([providers.TurnResult(text_parts=["Hi."], tool_calls=[], usage=make_usage())])
        engine.run_turn(
            provider, "gpt-5.6-luna", {"campaign_log_summary": [], "turn_number": 1}, "britannia_876",
            "monarch", [], "do something",
        )
        tool_names = {t["name"] for t in provider.tool_specs_seen[0]}
        self.assertNotIn("propose_mechanic_update", tool_names)

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
