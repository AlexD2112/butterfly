import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import db
import engine
import multiplayer
import providers


class TestCombatEncounterStorage(unittest.TestCase):
    """DB-level: the persistence/enforcement primitives, independent of any AI call."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]
        # Real character rows - combat_participants.character_id has a real FK to characters(id).
        u2 = db.get_or_create_discord_user("2", "Fighter1", None)
        u3 = db.get_or_create_discord_user("3", "Fighter2", None)
        self.char1_id = db.create_character(u2["id"], "petrograd_1917", "red_admiral", "Fighter1", {"turn_number": 0}, game_id=self.game["id"])
        self.char2_id = db.create_character(u3["id"], "petrograd_1917", "red_admiral", "Fighter2", {"turn_number": 0}, game_id=self.game["id"])

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def test_create_encounter_persists_participants_in_given_order(self):
        participants = [
            {"character_id": self.char1_id, "initiative_roll": 18},
            {"npc_key": "lenin", "initiative_roll": 12},
            {"character_id": self.char2_id, "initiative_roll": 5},
        ]
        encounter_id = db.create_combat_encounter(self.location_id, self.subspace_id, participants)
        stored = db.get_combat_participants(encounter_id)
        self.assertEqual([p["turn_order_index"] for p in stored], [0, 1, 2])
        self.assertEqual(stored[0]["character_id"], self.char1_id)
        self.assertEqual(stored[1]["npc_key"], "lenin")

    def test_get_active_combat_encounter_finds_the_right_one(self):
        encounter_id = db.create_combat_encounter(self.location_id, self.subspace_id, [{"character_id": self.char1_id, "initiative_roll": 10}])
        active = db.get_active_combat_encounter(self.subspace_id)
        self.assertEqual(active["id"], encounter_id)

    def test_current_combat_actor_reflects_the_turn_index(self):
        encounter_id = db.create_combat_encounter(
            self.location_id, self.subspace_id,
            [{"character_id": self.char1_id, "initiative_roll": 18}, {"character_id": self.char2_id, "initiative_roll": 5}],
            start_turn_index=1,
        )
        actor = db.get_current_combat_actor(encounter_id)
        self.assertEqual(actor["character_id"], self.char2_id)

    def test_advance_combat_turn_wraps_around(self):
        encounter_id = db.create_combat_encounter(
            self.location_id, self.subspace_id,
            [{"character_id": self.char1_id, "initiative_roll": 18}, {"character_id": self.char2_id, "initiative_roll": 5}],
            start_turn_index=1,
        )
        db.advance_combat_turn(encounter_id)
        self.assertEqual(db.get_current_combat_actor(encounter_id)["character_id"], self.char1_id)  # wrapped

    def test_end_combat_encounter_stops_it_showing_as_active(self):
        encounter_id = db.create_combat_encounter(self.location_id, self.subspace_id, [{"character_id": self.char1_id, "initiative_roll": 10}])
        db.end_combat_encounter(encounter_id)
        self.assertIsNone(db.get_active_combat_encounter(self.subspace_id))


def _usage():
    return {"provider": "openai", "model": "gpt-5.6-luna", "input_tokens": 10, "output_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0}


class ScriptedActionProvider:
    """Routes call() by force_tool_name (combat classifiers) vs plain narration text (the actual
    engine.run_turn call, which never requests a check in these tests - keeps focus on the
    combat-orchestration logic, not the underlying mechanics loop already covered elsewhere)."""

    def __init__(self, narration="A tense moment passes.", combat_starts=False, combat_participants=None, combat_ends=False):
        self.narration = narration
        self.combat_starts = combat_starts
        self.combat_participants = combat_participants or []
        self.combat_ends = combat_ends
        self.calls = []
        self.system_prompts = []  # one per call() - lets a test inspect what the main narration call actually saw

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        self.system_prompts.append(system_prompt)
        if force_tool_name == "decide_combat":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c1", "name": "decide_combat", "input": {
                    "started": self.combat_starts, "participants": self.combat_participants, "reasoning": "scripted",
                }}],
                usage=_usage(),
            )
        if force_tool_name == "decide_combat_status":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c2", "name": "decide_combat_status",
                                             "input": {"ended": self.combat_ends, "reasoning": "scripted"}}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=[self.narration], tool_calls=[], usage=_usage())

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class ScriptedSpaceProvider:
    """Like ScriptedActionProvider (never starts combat, plain narration), but ALSO scripts a real
    answer to decide_space - used to actually exercise multiplayer.detect_new_space's decision-
    application branches in resolve_action, rather than just its graceful-skip-when-unconfigured path
    (which is all TestResolveAction above covers)."""

    def __init__(self, narration="You move on.", space_kind="no_change", space_name=None, target_subspace_name=None):
        self.narration = narration
        self.space_kind = space_kind
        self.space_name = space_name
        self.target_subspace_name = target_subspace_name
        self.calls = []

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
        if force_tool_name == "decide_combat":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "c1", "name": "decide_combat",
                                             "input": {"started": False, "participants": [], "reasoning": "scripted"}}],
                usage=_usage(),
            )
        if force_tool_name == "decide_space":
            return providers.TurnResult(
                text_parts=[], tool_calls=[{"id": "s1", "name": "decide_space", "input": {
                    "kind": self.space_kind, "name": self.space_name,
                    "target_subspace_name": self.target_subspace_name, "reasoning": "scripted",
                }}],
                usage=_usage(),
            )
        return providers.TurnResult(text_parts=[self.narration], tool_calls=[], usage=_usage())

    def append_assistant_turn(self, conversation, turn_result):
        conversation.append(("assistant", turn_result))

    def append_tool_results(self, conversation, turn_result, results):
        conversation.append(("tool_results", results))


class TestDynamicSpaceCreation(unittest.TestCase):
    """resolve_action's application of multiplayer.detect_new_space's decision - the actual DB
    mutations (create/move), not just the classifier prompt/parsing (that's exercised for real against
    a live model separately, see NOTES.md's launch-readiness entry). providers.get_provider is patched
    to hand back a ScriptedSpaceProvider instead of the usual ProviderError-skip, so these tests
    actually reach the decision-application branches."""

    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user(discord_id, name, None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", name, state, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.location_id, self.subspace_id)
        return char_id

    def _char_row(self, char_id):
        conn = db.get_db()
        user_id = conn.execute("SELECT user_id FROM characters WHERE id = ?", (char_id,)).fetchone()["user_id"]
        conn.close()
        return db.get_character(char_id, user_id)

    def _resolve(self, char_id, space_kind, space_name=None, target_subspace_name=None, content="I do something."):
        fake = ScriptedSpaceProvider(space_kind=space_kind, space_name=space_name, target_subspace_name=target_subspace_name)
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            return multiplayer.resolve_action(
                fake, "gpt-5.6-luna", "luna", self._char_row(char_id), self.location_id, self.subspace_id,
                content, fake.new_conversation(),
            )

    def test_no_change_leaves_the_character_exactly_where_they_were(self):
        char_id = self._make_character("2", "Solo")
        result = self._resolve(char_id, "no_change")
        self.assertIsNone(result["new_subspace_id"])
        self.assertIsNone(result["new_location_id"])
        self.assertIsNone(result["moved_to_subspace_id"])
        self.assertIsNone(result["moved_to_location_id"])
        self.assertEqual(db.get_character_presence(char_id)["subspace_id"], self.subspace_id)

    def test_new_subspace_creates_it_under_the_current_location_and_moves_the_character(self):
        char_id = self._make_character("2", "Solo")
        result = self._resolve(char_id, "new_subspace", "The Cellar")
        self.assertIsNotNone(result["new_subspace_id"])
        self.assertIsNone(result["new_location_id"])
        self.assertIsNone(result["moved_to_location_id"])  # same location, only the subspace changed
        new_subspace = db.get_subspace(result["new_subspace_id"])
        self.assertEqual(new_subspace["location_id"], self.location_id)
        self.assertEqual(new_subspace["name"], "The Cellar")
        self.assertEqual(new_subspace["origin"], "dynamic")
        self.assertEqual(new_subspace["created_by_character_id"], char_id)
        self.assertEqual(db.get_character_presence(char_id)["subspace_id"], result["new_subspace_id"])

    def test_matched_subspace_moves_into_the_existing_one_without_creating_a_duplicate(self):
        existing_id = db.create_subspace(self.location_id, "The Cellar")
        char_id = self._make_character("2", "Solo")
        result = self._resolve(char_id, "matched_subspace", "The Cellar")
        self.assertIsNone(result["new_subspace_id"])
        self.assertEqual(result["moved_to_subspace_id"], existing_id)
        self.assertEqual(len(db.list_subspaces(self.location_id)), 2)  # Main + The Cellar, no 3rd

    def test_new_location_creates_it_with_a_default_subspace_and_moves_the_character(self):
        char_id = self._make_character("2", "Solo")
        result = self._resolve(char_id, "new_location", "The Putilov Works")
        self.assertIsNotNone(result["new_location_id"])
        self.assertEqual(result["moved_to_location_id"], result["new_location_id"])
        new_location = db.get_location(result["new_location_id"])
        self.assertEqual(new_location["name"], "The Putilov Works")
        self.assertEqual(new_location["server_id"], self.server_id)
        presence = db.get_character_presence(char_id)
        self.assertEqual(presence["location_id"], result["new_location_id"])
        self.assertEqual(presence["subspace_id"], db.get_default_subspace(result["new_location_id"])["id"])

    def test_matched_location_moves_into_the_existing_locations_default_subspace(self):
        other_location_id = db.create_location(self.server_id, "The Putilov Works")
        char_id = self._make_character("2", "Solo")
        result = self._resolve(char_id, "matched_location", "The Putilov Works")
        self.assertIsNone(result["new_location_id"])
        self.assertEqual(result["moved_to_location_id"], other_location_id)
        self.assertEqual(result["moved_to_subspace_id"], db.get_default_subspace(other_location_id)["id"])
        self.assertEqual(db.get_character_presence(char_id)["location_id"], other_location_id)

    def test_matched_location_with_a_target_subspace_lands_in_that_existing_subspace_not_the_default(self):
        # Regression test for a real miscalibration found via live-model testing 2026-08-23 (see
        # NOTES.md): "I head back to the telegraph room" after having left for a different Location
        # created a DUPLICATE "Telegraph Room" under the wrong Location instead of routing back to the
        # original one, because the classifier previously had no visibility into other Locations'
        # subspaces. target_subspace_name is the fix - the classifier can name a specific existing
        # subspace under the matched Location, and the app must land there, not that Location's
        # generic default.
        other_location_id = db.create_location(self.server_id, "The Smolny Institute")
        telegraph_room_id = db.create_subspace(other_location_id, "Telegraph Room")
        char_id = self._make_character("2", "Solo")
        result = self._resolve(
            char_id, "matched_location", "The Smolny Institute", target_subspace_name="Telegraph Room",
        )
        self.assertEqual(result["moved_to_location_id"], other_location_id)
        self.assertEqual(result["moved_to_subspace_id"], telegraph_room_id)  # the EXISTING one, not a new duplicate
        self.assertEqual(len(db.list_subspaces(other_location_id)), 2)  # Main + Telegraph Room, no 3rd

    def test_new_location_with_a_target_subspace_creates_that_subspace_there_not_just_the_default(self):
        char_id = self._make_character("2", "Solo")
        result = self._resolve(
            char_id, "new_location", "The Putilov Works", target_subspace_name="Shop Floor",
        )
        landing = db.get_subspace(result["moved_to_subspace_id"])
        self.assertEqual(landing["name"], "Shop Floor")
        self.assertEqual(landing["location_id"], result["new_location_id"])
        self.assertEqual(len(db.list_subspaces(result["new_location_id"])), 2)  # Main + Shop Floor

    def test_dynamic_space_creation_is_skipped_entirely_during_active_combat(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        db.create_combat_encounter(
            self.location_id, self.subspace_id,
            [{"character_id": attacker_id, "initiative_roll": 18}, {"character_id": defender_id, "initiative_roll": 5}],
        )
        current_actor_id = db.get_current_combat_actor(db.get_active_combat_encounter(self.subspace_id)["id"])["character_id"]
        fake = ScriptedSpaceProvider(space_kind="new_subspace", space_name="Somewhere New")
        with patch.object(providers, "get_provider", return_value=(fake, "gpt-5.6-luna")):
            multiplayer.resolve_action(
                fake, "gpt-5.6-luna", "luna", self._char_row(current_actor_id), self.location_id, self.subspace_id,
                "I press the attack.", fake.new_conversation(),
            )
        self.assertNotIn("decide_space", fake.calls)  # never even asked - a fight isn't when the world grows


class TestResolveAction(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.location_id = db.create_location(self.server_id, "Location A")
        self.subspace_id = db.get_default_subspace(self.location_id)["id"]

        # resolve_action also runs multiplayer.detect_new_subspace (2026-08-22) after a free action,
        # which resolves its own judge provider via providers.get_provider(JUDGE_MODEL_KEY) rather
        # than using whatever fake provider the test passed in for the main call. Patched to raise
        # ProviderError for every test in this class - the SAME graceful "no provider configured, skip
        # the check" fallback resolve_action already has, so combat-focused tests here stay focused on
        # combat and never make a real API call or depend on real environment configuration.
        self._provider_patcher = patch.object(providers, "get_provider", side_effect=providers.ProviderError("not configured"))
        self._provider_patcher.start()

    def tearDown(self):
        self._provider_patcher.stop()
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user(discord_id, name, None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", name, state, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.location_id, self.subspace_id)
        return char_id

    def _char_row(self, char_id):
        conn = db.get_db()
        user_id = conn.execute("SELECT user_id FROM characters WHERE id = ?", (char_id,)).fetchone()["user_id"]
        conn.close()
        return db.get_character(char_id, user_id)

    def test_a_free_action_with_nobody_else_present_never_starts_combat(self):
        char_id = self._make_character("2", "Solo")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=["character #999"])  # shouldn't even be asked
        result = multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(char_id), self.location_id, self.subspace_id,
            "I look around.", fake.new_conversation(),
        )
        self.assertIsNone(result["combat_encounter_id"])
        self.assertNotIn("decide_combat", fake.calls)  # no one else present - not even worth asking

    def test_a_solo_action_with_no_prior_subspace_activity_gets_no_scene_section(self):
        """No prior activity at all (not even this character's own) - build_system_prompt should skip
        the "OTHER CHARACTERS PRESENT" section entirely rather than describing an empty scene."""
        char_id = self._make_character("2", "Solo")
        fake = ScriptedActionProvider()
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(char_id), self.location_id, self.subspace_id,
            "I look around.", fake.new_conversation(),
        )
        main_prompt = fake.system_prompts[fake.calls.index("plain")]
        self.assertNotIn("OTHER CHARACTERS PRESENT", main_prompt)

    def test_an_action_after_another_characters_recent_turn_gets_shared_scene_context_by_name(self):
        """Real bug, found live via real Discord play (2026-09-03): a character's action was being
        resolved with zero awareness that another character in the SAME subspace had just done
        something seconds earlier - engine.run_turn (reused unchanged from single-player) only ever
        knew about the ACTING character's own state, never anyone else present. Asserts the fix: the
        system prompt actually sent for the main narration call now includes the other character's
        REAL NAME and their recent action - the same _recent_subspace_context judge_responder/
        generate_reply already used for the "message" pipeline, now fed into the mechanical
        resolve_action path too."""
        first_id = self._make_character("2", "Terrance Wildebear")
        second_id = self._make_character("3", "Tatikios")
        db.record_subspace_message(
            self.game["id"], self.location_id, self.subspace_id, first_id, "action",
            "lathers himself in butter to predict the future",
        )
        fake = ScriptedActionProvider(narration="You pour olive oil over yourself.")
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(second_id), self.location_id, self.subspace_id,
            "I pour olive oil on myself too.", fake.new_conversation(),
        )
        main_prompt = fake.system_prompts[fake.calls.index("plain")]
        self.assertIn("OTHER CHARACTERS PRESENT", main_prompt)
        self.assertIn("Terrance Wildebear", main_prompt)
        self.assertIn("butter", main_prompt)

    def test_an_action_that_ignites_combat_creates_an_encounter_including_the_actor(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(
            narration="A fight breaks out!", combat_starts=True, combat_participants=[f"character #{defender_id}"],
        )
        result = multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.location_id, self.subspace_id,
            "I attack them!", fake.new_conversation(),
        )
        self.assertIsNotNone(result["combat_encounter_id"])
        participants = db.get_combat_participants(result["combat_encounter_id"])
        participant_char_ids = {p["character_id"] for p in participants}
        self.assertEqual(participant_char_ids, {attacker_id, defender_id})

    def test_acting_out_of_turn_during_combat_raises_combat_turn_error(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.location_id, self.subspace_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.subspace_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]
        out_of_turn_id = defender_id if current_actor_id == attacker_id else attacker_id

        with self.assertRaises(multiplayer.CombatTurnError):
            multiplayer.resolve_action(
                ScriptedActionProvider(), "gpt-5.6-luna", "luna", self._char_row(out_of_turn_id),
                self.location_id, self.subspace_id, "I also attack!", ScriptedActionProvider().new_conversation(),
            )

    def test_acting_on_your_turn_during_combat_advances_to_the_next_participant(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.location_id, self.subspace_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.subspace_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="They strike back.", combat_ends=False)
        result = multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(current_actor_id), self.location_id, self.subspace_id,
            "I strike back!", fake2.new_conversation(),
        )
        self.assertEqual(result["combat_encounter_id"], encounter["id"])
        new_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]
        self.assertNotEqual(new_actor_id, current_actor_id)

    def test_combat_ending_frees_the_subspace_for_normal_actions(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.location_id, self.subspace_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.subspace_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="The attacker yields.", combat_ends=True)
        multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(current_actor_id), self.location_id, self.subspace_id,
            "I yield!", fake2.new_conversation(),
        )
        self.assertIsNone(db.get_active_combat_encounter(self.subspace_id))

    def test_a_bystander_can_act_freely_alongside_an_ongoing_fight(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        bystander_id = self._make_character("4", "Bystander")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.location_id, self.subspace_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.subspace_id)
        before_actor = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="I duck for cover, uninvolved.")
        result = multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(bystander_id), self.location_id, self.subspace_id,
            "I duck behind a desk.", fake2.new_conversation(),
        )
        # Doesn't raise CombatTurnError (not a participant), and doesn't disturb the turn order.
        self.assertEqual(result["combat_encounter_id"], encounter["id"])
        after_actor = db.get_current_combat_actor(encounter["id"])["character_id"]
        self.assertEqual(before_actor, after_actor)


if __name__ == "__main__":
    unittest.main()
