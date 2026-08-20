import unittest

from systems.apollo import mechanics as apollo
from systems.monarch import mechanics as monarch
from systems.red_admiral import mechanics as red_admiral


def _character(feats):
    return {
        "level": 1,
        "attributes": {"oration": 16, "intellect": 14, "resolve": 13, "presence": 12, "cunning": 10, "physique": 8},
        "skills": {},
        "feats": feats,
    }


class TestFeatCheckBonus(unittest.TestCase):
    """Feats had zero code-enforced effect anywhere before this - every single one was pure GM
    narrative with no numeric backing, which is at odds with the engine's whole premise that
    mechanical outcomes belong to the app, not model discretion. These cover the small set of
    feats (one per system, cleanly check-shaped) that now carry a real, code-enforced bonus."""

    def test_red_admiral_silver_tongue_boosts_oration_only(self):
        character = _character([{"id": "silver_tongue", "name": "Silver Tongue"}])
        oration_result = red_admiral.roll_check(character, "oration", None, 12)
        self.assertEqual(oration_result["feat_bonus"], 2)
        self.assertEqual(oration_result["feat_bonus_source"], "Silver Tongue")
        self.assertEqual(oration_result["modifier"], 3 + 2)  # +3 attribute mod, +2 feat, no skill/prof

        intellect_result = red_admiral.roll_check(character, "intellect", None, 12)
        self.assertEqual(intellect_result["feat_bonus"], 0)
        self.assertIsNone(intellect_result["feat_bonus_source"])

    def test_red_admiral_iron_constitution_boosts_physique_not_oration(self):
        character = _character([{"id": "iron_constitution", "name": "Iron Constitution"}])
        physique_result = red_admiral.roll_check(character, "physique", None, 12)
        self.assertEqual(physique_result["feat_bonus"], 2)
        oration_result = red_admiral.roll_check(character, "oration", None, 12)
        self.assertEqual(oration_result["feat_bonus"], 0)

    def test_character_without_the_feat_gets_no_bonus(self):
        character = _character([])
        result = red_admiral.roll_check(character, "oration", None, 12)
        self.assertEqual(result["feat_bonus"], 0)
        self.assertEqual(result["modifier"], 3)  # attribute mod only

    def test_monarch_silver_tongued_courtier_and_unyielding_faith(self):
        character = _character([
            {"id": "silver_tongued_courtier", "name": "Silver-Tongued Courtier"},
            {"id": "unyielding_faith", "name": "Unyielding Faith"},
        ])
        oration_result = monarch.roll_check(character, "oration", None, 12)
        self.assertEqual(oration_result["feat_bonus"], 2)
        resolve_result = monarch.roll_check(character, "resolve", None, 12)
        self.assertEqual(resolve_result["feat_bonus"], 2)
        cunning_result = monarch.roll_check(character, "cunning", None, 12)
        self.assertEqual(cunning_result["feat_bonus"], 0)

    def test_apollo_master_of_the_forum_and_stoic_composure(self):
        character = _character([
            {"id": "master_of_the_forum", "name": "Master of the Forum"},
            {"id": "stoic_composure", "name": "Stoic Composure"},
        ])
        oration_result = apollo.roll_check(character, "oration", None, 12)
        self.assertEqual(oration_result["feat_bonus"], 2)
        resolve_result = apollo.roll_check(character, "resolve", None, 12)
        self.assertEqual(resolve_result["feat_bonus"], 2)

    def test_unknown_feat_id_on_sheet_does_not_crash_or_grant_bonus(self):
        # Defensive: a feat id that somehow isn't in the catalog (shouldn't happen given
        # apply_level_up's validation, but roll_check shouldn't assume it can't) must not error.
        character = _character([{"id": "not_a_real_feat", "name": "Ghost Feat"}])
        result = red_admiral.roll_check(character, "oration", None, 12)
        self.assertEqual(result["feat_bonus"], 0)


if __name__ == "__main__":
    unittest.main()
