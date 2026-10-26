# Monarch System — v1

Part of **Butterfly**, an AI-narrated historical RPG engine. Monarch is a d20
attribute/skill/progression ruleset for feudal/medieval settings - court politics, faith, warfare,
oaths and lineage. Used by the **Britannia 876** and **Crusades 1097** setting packs. Built on the
same shared engine (`systems/_shared/core.py`) as its sibling systems **Red Admiral**
(revolutionary-era) and **Apollo** (Roman Republic) - only the skills, feats, liabilities, and
resource categories below are specific to Monarch; the dice math, leveling, and validation rules
are identical across all three and documented once in the shared engine's docstrings.

Same load-bearing design principle throughout: the model narrates, roleplays, and makes judgment
calls; the application rolls dice, computes modifiers, and validates every mechanical change.
Nothing that compounds over a long campaign (proficiency, XP thresholds, leveling, feat/liability
eligibility) is left to the model's discretion.

## 1. Attributes

Six attributes, each scored 1-20. Modifier = `floor((score - 10) / 2)`.

- **Oration** - command of a hall, a host, or a pulpit
- **Intellect** - law, scripture, siegecraft, logistics, strategy
- **Resolve** - nerve in battle, penance, oath-keeping
- **Presence** - courtly charm, chivalry, kinship bonds
- **Cunning** - intrigue, disguise, reading intentions
- **Physique** - horsemanship, arms, endurance in the field

## 2. Proficiency bonus

`proficiency_bonus(level) = 2 + floor((level - 1) / 4)`. Level 1-4: +2. Level 5-8: +3. Level 9-12:
+4. Level 13-16: +5. Level 17-20: +6.

## 3. Skills

Skills sit under one of the six attributes, at one of three tiers - **Untrained** (attribute
modifier only), **Proficiency** (+ proficiency bonus), **Expertise** (+ proficiency bonus x2). See
`mechanics.py`'s `SKILLS` for Monarch's canonical list (court rhetoric, siegecraft, horsemanship,
intrigue and plot, and so on). A skill moves up at most one tier per level-up.

## 4. Checks

Only genuinely uncertain, consequential attempts call for a check - never pure conversation or
description. **The application rolls the die, not the model.**

| DC | Meaning |
|----|---------|
| 8  | Routine |
| 12 | Meaningful challenge |
| 15 | Hard |
| 18 | Very hard |
| 22 | Extraordinary |

`1d20 + attribute modifier + skill tier bonus` vs. DC. The margin (total minus DC) sets one of six
degrees, applied by the application: **critical success** (natural 20, or beat the DC by 10+),
**strong success** (beat it by 5-9), **success** (met it, or beat it by up to 4), **success at a
cost** (missed by 1-3: the goal is reached, but the GM narrates a real price), **failure** (missed
by 4-9), **critical failure** (natural 1, or missed by 10+). A check may name one resource
`at_stake`; the application moves it by the degree (+4 / +2 / 0 / -3 / -4 / -8).

**Commitments** (section 9): when a commitment goes from active to broken, the application itself
takes 2 trust and adds an enmity pip for each bound party who has a relationship or enmity entry.

## 5. Experience and leveling

XP awarded via `propose_state_update`, capped at 60 per event. Threshold to advance:
`xp_to_next_level(level) = 100 + (level - 1) * 50`, tracked and applied automatically by the
application, which sets `level_up_pending` on a threshold crossing.

**Level-up choice - pick exactly one, via `propose_level_up`:**

- **Skill upgrade** - up to 2 skill steps, max 1 step per skill.
- **New feat** - by `feat_id` from `FEAT_CATALOG` only, gated by `min_level` and sometimes a
  prerequisite skill tier. Never an invented feat.
- **Attribute improvement** - +3 points distributed across attributes, 20 maximum each.

**Reinforcement bonus:** if the choice used an attribute/skill actually exercised in a check since
the last level-up, the next level's XP threshold gets a one-time 20% discount, tracked
automatically.

## 6. Feats and liabilities

Feats are permanent passive traits, one at a time, from the fixed `FEAT_CATALOG` only - no
freeform invention. Most feats are GM-narrated only, but a select few (the ones whose effect maps
cleanly onto a single check type) carry a **check bonus** enforced by the application in
`roll_check` - e.g. Silver-Tongued Courtier: +2 on Oration checks, applied automatically whenever
the feat is on the character's sheet, not left to the GM to remember. Liabilities are the negative
mirror, from `LIABILITY_CATALOG`, typically taken at character creation (each one taken also grants
+2 bonus starting skill points - see section 13 - so a liability is a real tradeoff, not a free
downside) or granted mid-campaign via `propose_state_update`'s `liabilities_gained` (by
`liability_id`, validated) and removable via `liabilities_removed`. A liability may carry an
**automatic trigger** enforced by the application - e.g. Pride of Lineage: any failed
Cunning/Presence check against a named rival adds +1 enmity pip, without the GM needing to remember
to apply it.

## 7. Enmity

Named rivals get a 0-3 pip enmity track (0 neutral, 1 latent, 2 active hostility, 3 dangerous
enemy). Normally at most +1 pip per proposed state update; automatic-trigger liabilities bypass
that cap via their own dedicated code path.

## 8. Resources

Setting-defined categories (see the setting pack's `resource_schema.json`) tracked as a 0-100
number, displayed as: Dominant (85-100), High (70-84), Rising/New (55-69), Moderate (35-54), Low
(18-34), Suspect/Negative (0-17). Typical Monarch categories: royal favor, baronial standing,
church favor, popular support, military command, treasury, foreign reputation, vassal loyalty,
personal safety.

## 9. Commitments

Formal oaths, betrothals, alliances, and vows - tracked as description / bound parties / status
(active, fulfilled, broken). Medieval settings should lean hard on this: an oath publicly broken is
one of the most narratively consequential things a character can do, and NPCs bound to it should
react accordingly, even though the application doesn't force that reaction mechanically.

## 10. Canon and divergence

Locked/contingent canon events plus a running divergence log of specific entries describing how the
campaign has departed from the historical record.

## 11. Opposed checks and NPCs

NPCs carry the same six attributes and skill-tier structure - see the setting pack's `npcs.md`.
The application never rolls for NPCs directly; their sheet is GM reference material for setting a
believable DC or narrating a contested scene.

## 12. State update protocol (tool reference)

- `request_check(attribute, skill, dc, description, target_npc)`
- `propose_state_update(...)` - resource/relationship/enmity deltas, flags, commitments, XP,
  liabilities gained/removed (by id), canon events, divergence notes. Never grants feats.
- `propose_level_up(choice, ...)` - skill / feat (by `feat_id`) / attribute, once per pending flag.

## 13. Character creation

Custom characters use `systems/_shared/core.validate_and_build_character`: standard array
`[16, 14, 13, 12, 10, 8]` across attributes, a 10-point base skill budget (max 3 starting Expertise
skills), one level-1 starting feat, up to 2 starting liabilities - each taken grants +2 bonus skill
points (up to +4 for both), so liabilities buy real starting competence rather than being a free
downside. A setting's premade signature character may exceed this budget by hand.
