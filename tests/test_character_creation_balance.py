import unittest

from systems import _shared
from systems._shared import core
from systems.red_admiral import mechanics as red_admiral

ATTRS = {"oration": 16, "intellect": 14, "resolve": 13, "presence": 12, "cunning": 10, "physique": 8}


def _create(skill_tiers=None, liability_ids=None, feat_id="silver_tongue", name="Test Character"):
    return red_admiral.create_character(name, "Tester", ATTRS, skill_tiers or {}, feat_id, liability_ids)


class TestLiabilitySkillPointTradeoff(unittest.TestCase):
    """Liabilities were a pure narrative downside with zero creation-time upside - the mechanically
    optimal choice was always to take none, which isn't a real tradeoff. These confirm each taken
    liability now funds real bonus skill points, matching LIABILITY_BONUS_SKILL_POINTS."""

    def test_base_budget_with_no_liabilities(self):
        # SKILL_POINT_BUDGET (10) exactly spent, no liabilities - should validate cleanly.
        tiers = {"platform_speechmaking": 1, "debate_and_public_argument": 1, "crowd_command_and_agitation": 1,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1, "inspiring_loyalty": 1,
                 "factional_maneuvering": 1}
        self.assertEqual(sum(tiers.values()), core.SKILL_POINT_BUDGET)
        character, errors = _create(skill_tiers=tiers)
        self.assertEqual(errors, [])
        self.assertIsNotNone(character)

    def test_one_point_over_base_budget_fails_with_no_liabilities(self):
        tiers = {"platform_speechmaking": 1, "debate_and_public_argument": 1, "crowd_command_and_agitation": 1,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1, "inspiring_loyalty": 1,
                 "factional_maneuvering": 1, "deception": 1}  # 11 points, budget is 10
        character, errors = _create(skill_tiers=tiers)
        self.assertIsNone(character)
        self.assertTrue(any("over budget" in e for e in errors))

    def test_one_liability_grants_bonus_points_that_would_otherwise_fail(self):
        tiers = {"platform_speechmaking": 1, "debate_and_public_argument": 1, "crowd_command_and_agitation": 1,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1, "inspiring_loyalty": 1,
                 "factional_maneuvering": 1, "deception": 1}  # 11 points - fails at budget 10, fits at 10+2
        character, errors = _create(skill_tiers=tiers, liability_ids=["arrogance"])
        self.assertEqual(errors, [])
        self.assertIsNotNone(character)

    def test_two_liabilities_grant_double_the_bonus(self):
        # 14 points spent - would need both liabilities (10 + 2*2 = 14) to afford.
        tiers = {"platform_speechmaking": 1, "debate_and_public_argument": 1, "crowd_command_and_agitation": 1,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1, "inspiring_loyalty": 1,
                 "factional_maneuvering": 1, "deception": 1, "operational_security": 1, "negotiation": 1,
                 "personal_persuasion": 1}
        self.assertEqual(sum(tiers.values()), 14)
        character, errors = _create(skill_tiers=tiers, liability_ids=["arrogance", "visible_target"])
        self.assertEqual(errors, [])
        self.assertIsNotNone(character)

        # Same skill spend with only one liability (10+2=12 budget) should fail.
        character2, errors2 = _create(skill_tiers=tiers, liability_ids=["arrogance"])
        self.assertIsNone(character2)
        self.assertTrue(any("over budget" in e for e in errors2))

    def test_unknown_liability_id_does_not_grant_bonus_points(self):
        # An invalid liability_id shouldn't be usable to inflate the skill budget - it should error
        # on the unknown id AND not count toward the bonus.
        tiers = {"platform_speechmaking": 1, "debate_and_public_argument": 1, "crowd_command_and_agitation": 1,
                 "revolutionary_theory": 2, "written_propaganda": 2, "crisis_command": 1, "inspiring_loyalty": 1,
                 "factional_maneuvering": 1, "deception": 1}  # 11 points
        character, errors = _create(skill_tiers=tiers, liability_ids=["not_a_real_liability"])
        self.assertIsNone(character)
        self.assertTrue(any("Unknown liability" in e for e in errors))
        self.assertTrue(any("over budget" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
