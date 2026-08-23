"""
Red Admiral - d20 skills/progression system for revolutionary-era political settings (the first
setting built on it is Petrograd 1917 / Trotsky, but the system itself is character-agnostic).

This module is configuration over systems/_shared/core.py: SKILLS/FEAT_CATALOG/LIABILITY_CATALOG
define what's specific to Red Admiral; all dice math, clamping, and validation logic is shared.
"""

from systems._shared import core

ATTRIBUTES = core.ATTRIBUTES
TIER_NAMES = core.TIER_NAMES
ENMITY_LABELS = core.ENMITY_LABELS
RESOURCE_LABELS = core.RESOURCE_LABELS
XP_AWARD_CAP = core.XP_AWARD_CAP
MAX_ATTRIBUTE = core.MAX_ATTRIBUTE
MAX_LEVEL = core.MAX_LEVEL

# Canonical skill list, grouped by governing attribute - used both for premade character sheets and
# for the generic character creator (systems/_shared/core.validate_and_build_character).
SKILLS = {
    "oration": ["platform_speechmaking", "debate_and_public_argument", "crowd_command_and_agitation", "intimidation"],
    "intellect": ["revolutionary_theory", "written_propaganda", "military_strategic_planning", "situation_analysis", "languages_and_translation"],
    "resolve": ["crisis_command", "psychological_endurance", "willpower_vs_coercion"],
    "presence": ["inspiring_loyalty", "negotiation", "personal_persuasion", "cultivating_allies"],
    "cunning": ["factional_maneuvering", "deception", "reading_hidden_agendas", "operational_security"],
    "physique": ["endurance_and_travel", "physical_confrontation", "stealth_and_evasion"],
}

# Curated feat catalog - replaces the old "propose any perk you like" freeform path. Every feat a
# character ever gains must come from here, chosen by id via propose_level_up (or at character
# creation for a level-1 feat). Staggered min_level requirements give leveling an actual arc.
FEAT_CATALOG = {
    "silver_tongue": {
        "name": "Silver Tongue", "min_level": 1,
        "description": "Words come easily and land hard. In debate or public address, the GM should let a clever turn of phrase win over an audience that a flatter delivery wouldn't have. Mechanically: +2 on Oration checks.",
        "check_bonus": {"on_attributes": ["oration"], "bonus": 2},
    },
    "iron_constitution": {
        "name": "Iron Constitution", "min_level": 1,
        "description": "Exhaustion, illness, and privation take unusually long to catch up. Extended crisis or travel should cost this character less than it costs others. Mechanically: +2 on Physique checks.",
        "check_bonus": {"on_attributes": ["physique"], "bonus": 2},
    },
    "man_of_the_people": {
        "name": "Man/Woman of the People", "min_level": 1,
        "description": "Genuinely at ease with workers, soldiers, and the crowd, not just performing at them. Rank-and-file NPCs should warm to this character faster than to other leaders.",
    },
    "quick_study": {
        "name": "Quick Study", "min_level": 1,
        "description": "Picks up unfamiliar terrain - a new city, a new bureaucracy, a new front - faster than expected. The GM should reduce the friction of 'first time doing this' scenes.",
    },
    "network_of_agitators": {
        "name": "Network of Agitators", "min_level": 5,
        "prerequisite_skill": {"skill": "crowd_command_and_agitation", "min_tier": 1},
        "description": "A standing web of committed organizers who owe this character personally, not just the party. Can plausibly call in a favor - a safehouse, a rumor, a crowd - on short notice, once or twice per major arc.",
    },
    "battle_tested": {
        "name": "Battle-Tested", "min_level": 5,
        "description": "Has been under real fire and didn't break. Panic and rout among troops or crowds this character personally commands should be rarer than the base situation would suggest.",
    },
    "ruthless_pragmatist": {
        "name": "Ruthless Pragmatist", "min_level": 5,
        "description": "Will make the hard call others flinch from. Opens dialogue options and state-update possibilities (e.g. authorizing something brutal for strategic gain) that a more sentimental character couldn't credibly take - a double-edged feat, since it should also cost trust with softer-hearted allies over time.",
    },
    "old_bolshevik": {
        "name": "Movement Veteran", "min_level": 5,
        "description": "Years in the underground before it was fashionable earns a specific kind of respect newcomers can't buy. Old hands and true believers should give this character more benefit of the doubt in factional disputes.",
    },
    "polyglot": {
        "name": "Polyglot", "min_level": 9,
        "description": "Fluent in several languages relevant to the setting's diplomacy and exile networks. Negotiation and intelligence-gathering scenes involving foreigners should never be blocked purely by a language barrier for this character.",
    },
    "feared_reputation": {
        "name": "Feared Reputation", "min_level": 9,
        "description": "The name alone does work before a word is spoken. Intimidation-flavored checks against anyone who's heard of this character get a meaningful in-fiction advantage - but so does anyone trying to rally opposition by pointing at that same fear.",
    },
    "tactical_mind": {
        "name": "Tactical Mind", "min_level": 9,
        "description": "Sees the shape of a crisis a beat before others do. When commanding a chaotic situation, this character should get to ask the GM one clarifying tactical question the fiction wouldn't normally offer.",
    },
    "political_survivor": {
        "name": "Political Survivor", "min_level": 13,
        "description": "Has weathered purges, plots, and reversals before. The GM should telegraph at least a flicker of warning before this specific character gets blindsided by a factional maneuver - reflecting hard-won instinct, not plot armor.",
    },
    "trusted_confidant": {
        "name": "Trusted Confidant", "min_level": 13,
        "description": "One specific named ally (GM and player agree who) will believe this character's account over almost anyone else's, even against fairly strong contrary evidence, at least once per major arc.",
    },
    "historic_stature": {
        "name": "Historic Stature", "min_level": 17,
        "description": "This character's actions are already being written into the history people tell themselves in real time. Divergence-log entries involving this character should land with outsized narrative weight.",
    },
}

# Curated liability catalog. Most are narrative/GM-adjudicated; a liability may optionally carry an
# "auto_trigger" - a code-enforced effect the application applies automatically rather than trusting
# the GM to remember (see systems/_shared/core.apply_auto_liability_effects).
LIABILITY_CATALOG = {
    "arrogance": {
        "name": "Arrogance", "min_level": 1,
        "description": "On any failed Cunning or Presence check involving a named rival, automatically add 1 enmity pip (enforced by the application, not GM discretion).",
        "auto_trigger": {"on_attributes": ["cunning", "presence"], "effect": "enmity_pip"},
    },
    "visible_target": {
        "name": "Visible Target", "min_level": 1,
        "description": "A conspicuous public figure whose enemies have an easy time making them a symbol - opponents gain an edge on attempts to discredit this character in certain constituencies. The GM should reflect this in narrative framing and DC-setting when relevant, not as an automatic numeric trigger.",
    },
    "workaholic": {
        "name": "Workaholic", "min_level": 1,
        "description": "Cannot stop working even when it costs them. Extended neglect of health, family, or allies should periodically surface as complications the GM introduces, not just flavor.",
    },
    "blunt_tongue": {
        "name": "Blunt Tongue", "min_level": 1,
        "description": "Says the true thing, not the tactful one. Moderates and fence-sitters should be measurably harder to keep on side than they would be for a more diplomatic character.",
    },
    "old_grudges": {
        "name": "Old Grudges", "min_level": 1,
        "description": "Does not let go of a betrayal. Reconciliation with a rival once enmity reaches 'active hostility' (2 pips) or higher should be unusually hard to achieve, even when it would be strategically wise.",
    },
    "overextended": {
        "name": "Overextended", "min_level": 1,
        "description": "Juggling too many fronts at once. When this character is the acting force behind a resource gain in one area, the GM should periodically narrate a quieter resource somewhere else slipping without a check ever being called for it.",
    },
}

TOOL_SPECS = core.build_tool_specs(FEAT_CATALOG)


def roll_check(character, attribute, skill, dc, target_npc=None):
    return core.roll_check(character, attribute, skill, dc, target_npc, FEAT_CATALOG)


def apply_auto_liability_effects(state, check_result):
    return core.apply_auto_liability_effects(state, check_result, LIABILITY_CATALOG)


def apply_state_update(state, update, schema):
    return core.apply_state_update(state, update, schema, LIABILITY_CATALOG)


def apply_level_up(state, update):
    return core.apply_level_up(state, update, FEAT_CATALOG)


def create_character(name, role, attribute_assignment, skill_tiers, feat_id, liability_ids=None):
    return core.validate_and_build_character(
        name, role, attribute_assignment, skill_tiers, feat_id, liability_ids,
        SKILLS, FEAT_CATALOG, LIABILITY_CATALOG,
    )


def create_leveled_character(name, role, level, attribute_assignment, skill_tiers, feat_ids, liability_ids=None):
    return core.validate_and_build_leveled_character(
        name, role, level, attribute_assignment, skill_tiers, feat_ids, liability_ids,
        SKILLS, FEAT_CATALOG, LIABILITY_CATALOG,
    )
