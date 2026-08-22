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
        self.room_id = db.create_room(self.server_id, "Room A")
        self.location_id = db.get_default_location(self.room_id)["id"]
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
        encounter_id = db.create_combat_encounter(self.room_id, self.location_id, participants)
        stored = db.get_combat_participants(encounter_id)
        self.assertEqual([p["turn_order_index"] for p in stored], [0, 1, 2])
        self.assertEqual(stored[0]["character_id"], self.char1_id)
        self.assertEqual(stored[1]["npc_key"], "lenin")

    def test_get_active_combat_encounter_finds_the_right_one(self):
        encounter_id = db.create_combat_encounter(self.room_id, self.location_id, [{"character_id": self.char1_id, "initiative_roll": 10}])
        active = db.get_active_combat_encounter(self.location_id)
        self.assertEqual(active["id"], encounter_id)

    def test_current_combat_actor_reflects_the_turn_index(self):
        encounter_id = db.create_combat_encounter(
            self.room_id, self.location_id,
            [{"character_id": self.char1_id, "initiative_roll": 18}, {"character_id": self.char2_id, "initiative_roll": 5}],
            start_turn_index=1,
        )
        actor = db.get_current_combat_actor(encounter_id)
        self.assertEqual(actor["character_id"], self.char2_id)

    def test_advance_combat_turn_wraps_around(self):
        encounter_id = db.create_combat_encounter(
            self.room_id, self.location_id,
            [{"character_id": self.char1_id, "initiative_roll": 18}, {"character_id": self.char2_id, "initiative_roll": 5}],
            start_turn_index=1,
        )
        db.advance_combat_turn(encounter_id)
        self.assertEqual(db.get_current_combat_actor(encounter_id)["character_id"], self.char1_id)  # wrapped

    def test_end_combat_encounter_stops_it_showing_as_active(self):
        encounter_id = db.create_combat_encounter(self.room_id, self.location_id, [{"character_id": self.char1_id, "initiative_roll": 10}])
        db.end_combat_encounter(encounter_id)
        self.assertIsNone(db.get_active_combat_encounter(self.location_id))


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

    def new_conversation(self):
        return []

    def append_user_message(self, conversation, text):
        conversation.append(("user", text))

    def call(self, model, system_prompt, tool_specs, conversation, force_tool_name=None):
        self.calls.append(force_tool_name or "plain")
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


class TestResolveAction(unittest.TestCase):
    def setUp(self):
        self._tmpdir = TemporaryDirectory()
        self._orig_db_file = db.DB_FILE
        db.DB_FILE = Path(self._tmpdir.name) / "test_butterfly.db"
        db.init_db()
        admin = db.get_or_create_discord_user("1", "Admin", None)
        self.server_id = db.create_multiplayer_server("Test Server", "petrograd_1917", "red_admiral", "day", 24.0, admin["id"])
        self.game = db.get_game_for_server(self.server_id)
        self.room_id = db.create_room(self.server_id, "Room A")
        self.location_id = db.get_default_location(self.room_id)["id"]

    def tearDown(self):
        db.DB_FILE = self._orig_db_file
        self._tmpdir.cleanup()

    def _make_character(self, discord_id, name):
        state = engine.load_premade_character_state("petrograd_1917")
        state.pop("_is_new", None)
        user = db.get_or_create_discord_user(discord_id, name, None)
        char_id = db.create_character(user["id"], "petrograd_1917", "red_admiral", name, state, game_id=self.game["id"])
        db.move_character(char_id, self.server_id, self.room_id, self.location_id)
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
            fake, "gpt-5.6-luna", "luna", self._char_row(char_id), self.room_id, self.location_id,
            "I look around.", fake.new_conversation(),
        )
        self.assertIsNone(result["combat_encounter_id"])
        self.assertNotIn("decide_combat", fake.calls)  # no one else present - not even worth asking

    def test_an_action_that_ignites_combat_creates_an_encounter_including_the_actor(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(
            narration="A fight breaks out!", combat_starts=True, combat_participants=[f"character #{defender_id}"],
        )
        result = multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.room_id, self.location_id,
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
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.room_id, self.location_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.location_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]
        out_of_turn_id = defender_id if current_actor_id == attacker_id else attacker_id

        with self.assertRaises(multiplayer.CombatTurnError):
            multiplayer.resolve_action(
                ScriptedActionProvider(), "gpt-5.6-luna", "luna", self._char_row(out_of_turn_id),
                self.room_id, self.location_id, "I also attack!", ScriptedActionProvider().new_conversation(),
            )

    def test_acting_on_your_turn_during_combat_advances_to_the_next_participant(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.room_id, self.location_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.location_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="They strike back.", combat_ends=False)
        result = multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(current_actor_id), self.room_id, self.location_id,
            "I strike back!", fake2.new_conversation(),
        )
        self.assertEqual(result["combat_encounter_id"], encounter["id"])
        new_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]
        self.assertNotEqual(new_actor_id, current_actor_id)

    def test_combat_ending_frees_the_location_for_normal_actions(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.room_id, self.location_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.location_id)
        current_actor_id = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="The attacker yields.", combat_ends=True)
        multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(current_actor_id), self.room_id, self.location_id,
            "I yield!", fake2.new_conversation(),
        )
        self.assertIsNone(db.get_active_combat_encounter(self.location_id))

    def test_a_bystander_can_act_freely_alongside_an_ongoing_fight(self):
        attacker_id = self._make_character("2", "Attacker")
        defender_id = self._make_character("3", "Defender")
        bystander_id = self._make_character("4", "Bystander")
        fake = ScriptedActionProvider(combat_starts=True, combat_participants=[f"character #{defender_id}"])
        multiplayer.resolve_action(
            fake, "gpt-5.6-luna", "luna", self._char_row(attacker_id), self.room_id, self.location_id,
            "I attack them!", fake.new_conversation(),
        )
        encounter = db.get_active_combat_encounter(self.location_id)
        before_actor = db.get_current_combat_actor(encounter["id"])["character_id"]

        fake2 = ScriptedActionProvider(narration="I duck for cover, uninvolved.")
        result = multiplayer.resolve_action(
            fake2, "gpt-5.6-luna", "luna", self._char_row(bystander_id), self.room_id, self.location_id,
            "I duck behind a desk.", fake2.new_conversation(),
        )
        # Doesn't raise CombatTurnError (not a participant), and doesn't disturb the turn order.
        self.assertEqual(result["combat_encounter_id"], encounter["id"])
        after_actor = db.get_current_combat_actor(encounter["id"])["character_id"]
        self.assertEqual(before_actor, after_actor)


if __name__ == "__main__":
    unittest.main()
