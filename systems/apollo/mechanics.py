"""
Apollo - d20 skills/progression system for Roman Republic-era settings (Senate politics, dignitas,
clientela, the legions). Used by Rome 59 BCE. Configuration over systems/_shared/core.py - see that
module for the shared dice/leveling/validation logic.
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
    "oration": ["forensic_oratory", "contio_speechmaking", "military_exhortation", "threats_and_coercion"],
    "intellect": ["roman_law_and_precedent", "philosophy_and_rhetoric_theory", "logistics_and_supply", "provincial_administration", "languages_of_the_mediterranean"],
    "resolve": ["gravitas_under_pressure", "stoic_endurance", "unbending_will"],
    "presence": ["patrician_bearing", "patron_client_charm", "marital_and_familial_alliance", "popularis_appeal"],
    "cunning": ["political_intrigue", "bribery_and_graft", "reading_hidden_agendas", "covert_correspondence"],
    "physique": ["horsemanship_and_campaigning", "swordsmanship", "physical_endurance"],
}

FEAT_CATALOG = {
    "dignitas_personified": {
        "name": "Dignitas Personified", "min_level": 1,
        "description": "Personal standing that precedes this character into any room. Senators and clients alike should extend a baseline deference they wouldn't extend to a man of lesser reputation.",
    },
    "friend_of_the_plebs": {
        "name": "Friend of the Plebs", "min_level": 1,
        "description": "Genuinely trusted by the urban and rural poor, not merely performing populism. Plebeian NPCs should warm to this character faster than to a typical patrician.",
    },
    "master_of_the_forum": {
        "name": "Master of the Forum", "min_level": 1,
        "description": "A gift for the set-piece speech. The GM should let a well-built forensic or contio argument sway a crowd or jury that a flatter delivery wouldn't have moved.",
    },
    "stoic_composure": {
        "name": "Stoic Composure", "min_level": 1,
        "description": "Trained (or naturally gifted) at appearing unshaken. Should steady this character in crisis scenes that would visibly rattle most others.",
    },
    "patron_of_many": {
        "name": "Patron of Many", "min_level": 5,
        "prerequisite_skill": {"skill": "patron_client_charm", "min_tier": 1},
        "description": "A wide, genuinely loyal clientela built over years. Once or twice per major arc, the GM should let this character call in a favor - votes, muscle, information - from clients with no on-screen setup needed.",
    },
    "veteran_of_the_legions": {
        "name": "Veteran of the Legions", "min_level": 5,
        "description": "Has actually commanded men in the field, not just read about it. Troops under this character's direct command should hold formation and nerve better than the raw situation suggests.",
    },
    "augurs_favor": {
        "name": "Augur's Favor", "min_level": 5,
        "description": "Either holds real religious office or is widely believed to have the gods' ear. The devout and the superstitious should give unusual weight to this character's blessing or curse.",
    },
    "master_advocate": {
        "name": "Master Advocate", "min_level": 5,
        "description": "A formidable presence in the courts. Legal proceedings this character argues in person should carry a real thumb on the scale in their favor.",
    },
    "master_of_intrigue": {
        "name": "Master of Intrigue", "min_level": 9,
        "description": "Reads a room of rivals the way others read a book. Once per major arc, the GM should let this character correctly sense a plot against them slightly before it breaks cover.",
    },
    "triumphator": {
        "name": "Triumphator", "min_level": 9,
        "description": "Has been (or is clearly on track to be) honored with a triumph - the single highest public acclaim Rome grants a commander. Military glory checks and recruitment involving this character's name should land unusually well.",
    },
    "foreign_client_kings_ally": {
        "name": "Foreign Client-Kings' Ally", "min_level": 9,
        "description": "Cultivated personal relationships with client kings and foreign envoys. Diplomacy scenes with foreign powers should never be blocked purely by unfamiliarity for this character.",
    },
    "unshakeable_auctoritas": {
        "name": "Unshakeable Auctoritas", "min_level": 13,
        "description": "A personal authority that survives even open political attack. The GM should have at least one attempted public humiliation of this character land more weakly than the attacker intended.",
    },
    "founders_mantle": {
        "name": "Founder's Mantle", "min_level": 13,
        "description": "Is beginning to be spoken of as a man who will refound something - an office, an alliance, an era. Claims to a larger historical role should carry real weight even among skeptics.",
    },
    "living_legend_of_rome": {
        "name": "Living Legend of Rome", "min_level": 17,
        "description": "Already passing into the histories in real time. Divergence-log entries involving this character should land with outsized narrative weight.",
    },
}

LIABILITY_CATALOG = {
    "overweening_ambition": {
        "name": "Overweening Ambition", "min_level": 1,
        "description": "On any failed Cunning or Presence check involving a named rival, automatically add 1 enmity pip (enforced by the application, not GM discretion).",
        "auto_trigger": {"on_attributes": ["cunning", "presence"], "effect": "enmity_pip"},
    },
    "scandalous_reputation": {
        "name": "Scandalous Reputation", "min_level": 1,
        "description": "A past (or ongoing) scandal that political enemies love to invoke. The GM should have this surface as a rhetorical weapon against the character periodically, whether or not it's currently relevant.",
    },
    "crushing_debt": {
        "name": "Crushing Debt", "min_level": 1,
        "description": "Owes ruinous sums, as many ambitious Romans did. The GM should periodically narrate treasury quietly slipping to creditors without a check ever being called for it.",
    },
    "enemies_in_the_senate": {
        "name": "Enemies in the Senate", "min_level": 1,
        "description": "At least one enmity relationship with a senator should start (or quickly develop) at an elevated level because of a prior political collision.",
    },
    "superstition_bound": {
        "name": "Superstition-Bound", "min_level": 1,
        "description": "Genuinely rattled by unfavorable omens, real or rumored, in a culture that takes them seriously. Should cost this character composure in scenes a more skeptical character would shrug off.",
    },
    "overextended_clientela": {
        "name": "Overextended Clientela", "min_level": 1,
        "description": "Promised more to more clients than can actually be delivered. The GM should periodically narrate popular_support or patrician_network quietly slipping without a check ever being called for it.",
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
