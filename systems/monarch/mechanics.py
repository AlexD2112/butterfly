"""
Monarch - d20 skills/progression system for feudal/medieval settings (court politics, faith,
warfare, oaths). Used by Britannia 876 and Crusades 1097. Configuration over
systems/_shared/core.py - see that module for the shared dice/leveling/validation logic.
"""

from systems._shared import core

ATTRIBUTES = core.ATTRIBUTES
TIER_NAMES = core.TIER_NAMES
ENMITY_LABELS = core.ENMITY_LABELS
RESOURCE_LABELS = core.RESOURCE_LABELS
XP_AWARD_CAP = core.XP_AWARD_CAP
MAX_ATTRIBUTE = core.MAX_ATTRIBUTE
MAX_LEVEL = core.MAX_LEVEL

SKILLS = {
    "oration": ["court_rhetoric", "sermon_and_preaching", "rallying_the_host", "blustering_threats"],
    "intellect": ["statecraft_and_law", "chronicle_and_scripture", "siegecraft_and_engineering", "terrain_and_logistics", "tongues_of_the_realm"],
    "resolve": ["battle_nerve", "penance_and_fasting", "unyielding_oath"],
    "presence": ["courtly_grace", "chivalrous_bearing", "kinship_ties", "wooing_and_alliance"],
    "cunning": ["intrigue_and_plot", "disguise_and_subterfuge", "reading_intentions", "spycraft"],
    "physique": ["horsemanship", "arms_and_armor", "wilderness_endurance"],
}

FEAT_CATALOG = {
    "anointed_authority": {
        "name": "Anointed Authority", "min_level": 1,
        "description": "Rule sits naturally on this character's shoulders. Vassals and common folk alike should hesitate longer than usual before openly defying a direct command.",
    },
    "beloved_by_the_smallfolk": {
        "name": "Beloved by the Smallfolk", "min_level": 1,
        "description": "Peasants, town-dwellers, and the rank and file warm to this character quickly and genuinely, not out of fear of rank.",
    },
    "silver_tongued_courtier": {
        "name": "Silver-Tongued Courtier", "min_level": 1,
        "description": "A gift for saying the right thing at court. The GM should let a well-turned compliment or veiled threat land harder than the words alone would justify.",
    },
    "unyielding_faith": {
        "name": "Unyielding Faith", "min_level": 1,
        "description": "A deep, unshakeable faith (or ideology) that steadies this character under pressure others would break under.",
    },
    "master_strategist": {
        "name": "Master Strategist", "min_level": 5,
        "prerequisite_skill": {"skill": "terrain_and_logistics", "min_tier": 1},
        "description": "Sees a battlefield or a siege the way others see a chessboard. The GM should let this character's battle plans account for at least one complication a less foresighted commander would have missed.",
    },
    "battle_hardened": {
        "name": "Battle-Hardened", "min_level": 5,
        "description": "Has led men into real slaughter and kept their nerve. Troops under this character's direct command should rout less readily than the raw situation would suggest.",
    },
    "kinmaker": {
        "name": "Kingmaker's Instinct", "min_level": 5,
        "description": "An unusually sharp read on which claimant, alliance, or faction is about to win. Once per major arc, the GM should let this character correctly sense which way a contested succession or council vote is about to break, before it's public.",
    },
    "shield_of_the_faith": {
        "name": "Shield of the Faith", "min_level": 5,
        "description": "A visible, credible defender of the Church (or the character's faith). Clergy and the devout should extend real trust and cover to this character that they wouldn't extend to a merely nominal believer.",
    },
    "feared_blade": {
        "name": "Feared Blade", "min_level": 9,
        "description": "A personal reputation for violence earned in single combat or at the front of a charge. Would-be challengers should think twice; so should anyone hoping to intimidate this character in turn - it won't easily work.",
    },
    "diplomat_of_nations": {
        "name": "Diplomat of Nations", "min_level": 9,
        "description": "Fluent in the customs and expectations of foreign courts, not just their language. Negotiations with foreign powers should never be blocked purely by cultural unfamiliarity for this character.",
    },
    "grand_patron": {
        "name": "Grand Patron", "min_level": 9,
        "description": "A generous, visible patron of the church, the arts, or the poor. This buys real, lasting goodwill - the GM should let past patronage soften at least one otherwise-hostile reaction per major arc.",
    },
    "legendary_lineage": {
        "name": "Legendary Lineage", "min_level": 13,
        "description": "Descended from (or now the founder of) a line people already speak of with awe. Claims to legitimacy involving this character's bloodline should carry real weight even among skeptics.",
    },
    "crusaders_zeal": {
        "name": "Crusader's Zeal", "min_level": 13,
        "description": "A holy-war fervor that spreads to those nearby. Troops and pilgrims rallied personally by this character for a sanctified cause should show unusual resolve, at least until that cause is visibly betrayed.",
    },
    "undying_legend": {
        "name": "Undying Legend", "min_level": 17,
        "description": "This character's deeds are already passing into song and chronicle in their own lifetime. Divergence-log entries involving them should land with outsized narrative weight.",
    },
}

LIABILITY_CATALOG = {
    "pride_of_lineage": {
        "name": "Pride of Lineage", "min_level": 1,
        "description": "On any failed Cunning or Presence check involving a named rival, automatically add 1 enmity pip (enforced by the application, not GM discretion).",
        "auto_trigger": {"on_attributes": ["cunning", "presence"], "effect": "enmity_pip"},
    },
    "excommunicable_temper": {
        "name": "Excommunicable Temper", "min_level": 1,
        "description": "A temper that has gotten this character in trouble with the Church before and will again. The GM should periodically have clergy or the pious react with extra wariness.",
    },
    "blood_debt": {
        "name": "Blood Debt", "min_level": 1,
        "description": "A feud, owed or owing, that predates this campaign. At least one enmity relationship should start (or quickly develop) at an elevated level because of it.",
    },
    "reckless_in_battle": {
        "name": "Reckless in Battle", "min_level": 1,
        "description": "Leads from the front to a fault. The GM should occasionally put this character personally at risk in engagements a more cautious commander would have avoided.",
    },
    "superstitious_dread": {
        "name": "Superstitious Dread", "min_level": 1,
        "description": "Genuinely rattled by omens, curses, and portents, real or rumored. Should cost this character composure in scenes a more skeptical character would shrug off.",
    },
    "overmortgaged_demesne": {
        "name": "Overmortgaged Demesne", "min_level": 1,
        "description": "Lands and levies are pledged further than is wise. The GM should periodically narrate treasury or vassal-loyalty quietly slipping without a check ever being called for it.",
    },
    "chronic_affliction": {
        "name": "Chronic Affliction", "min_level": 1,
        "description": "A recurring illness or old wound that flares at the worst moments. The GM should periodically impose a real cost - a missed campaign season, a compromised presence at a crucial court moment - grounded in this specific ailment, not a generic 'you feel unwell.'",
    },
}

TOOL_SPECS = core.build_tool_specs(FEAT_CATALOG)


def roll_check(character, attribute, skill, dc, target_npc=None):
    return core.roll_check(character, attribute, skill, dc, target_npc)


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
