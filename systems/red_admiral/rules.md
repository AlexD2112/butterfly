# Red Admiral System — v2

Part of **Butterfly**, an AI-narrated historical RPG engine. Red Admiral is a d20
attribute/skill/progression ruleset originally drafted for a Trotsky/Petrograd 1917 campaign, then
generalized so any character built for a revolutionary-politics setting can run on it. A **setting**
(see `settings/`) supplies the actual character sheet, NPCs, and world content; this system supplies
the mechanics those characters run on. Two sibling systems exist on the same shared engine
(`systems/_shared/core.py`) for other eras: **Monarch** (feudal/medieval) and **Apollo** (Roman
Republic) - see `engine.py`'s `SYSTEM_REGISTRY` for the full list. Adding a new system means adding
new skills/feats/liabilities/resource categories as configuration, not rewriting the dice engine.

Same load-bearing design principle as before: the model narrates, roleplays, and makes judgment
calls; the application rolls dice, computes modifiers, and validates every mechanical change.
Nothing that compounds over a long campaign (proficiency, XP thresholds, leveling) is left to the
model's discretion - those are all fixed formulas in `mechanics.py`, not GM narration.

## 1. Attributes

Six attributes, each scored 1-20. Modifier = `floor((score - 10) / 2)`.

- **Oration** - verbal power, platform performance, crowd command
- **Intellect** - reasoning, theory, written argument, strategic planning
- **Resolve** - willpower, courage, crisis endurance, decisiveness
- **Presence** - close-quarters charisma, personal authority, loyalty
- **Cunning** - deception, maneuvering, reading hidden agendas
- **Physique** - stamina, physical toughness, endurance

These six are deliberately abstract enough to fit most historical-political-figure characters, not
just one. A setting pack can't add new attributes without a system change (that would require a
new system), but it fully controls which *skills* sit under each one - see below.

## 2. Proficiency bonus

Fixed formula, tied to level, never GM discretion:

```
proficiency_bonus(level) = 2 + floor((level - 1) / 4)
```

Level 1-4: +2. Level 5-8: +3. Level 9-12: +4. Level 13-16: +5. Level 17-20: +6.

## 3. Skills

Skills are setting-defined (a character sheet lists its own skills, grouped under one of the six
attributes each) - this document only fixes the *mechanic*, not the list. Each skill sits at one
of three tiers:

- **Untrained** (tier 0) - attribute modifier only.
- **Proficiency** (tier 1) - attribute modifier + proficiency bonus.
- **Expertise** (tier 2) - attribute modifier + (proficiency bonus x 2).

A skill can only move up one tier per level-up (untrained -> proficiency is one step; proficiency
-> expertise is a second step; you cannot skip untrained straight to expertise in a single
level-up). A character sheet may start with some skills already at Proficiency or even Expertise
if the setting's backstory justifies it (a character with a decade of relevant real experience
shouldn't have to "earn" what they already plausibly know) - see the setting pack's
`character_sheet.json` for how that's expressed.

## 4. Checks (the resolution mechanic)

Same core principle as `core_rules.md`: only genuinely uncertain, consequential attempts call for
a check - not pure conversation or description. **The application rolls the die, not the model.**

Procedure:
1. The GM identifies the relevant attribute (required) and, if one plausibly applies, a specific
   skill under it (optional - if none fits, the check just uses the raw attribute modifier). If the
   check meaningfully involves a specific NPC (persuading them, out-maneuvering them, deceiving
   them), the GM names that NPC as the check's target.
2. The GM sets a Difficulty Class:

   | DC | Meaning |
   |----|---------|
   | 8  | Routine |
   | 12 | Meaningful challenge |
   | 15 | Hard |
   | 18 | Very hard |
   | 22 | Extraordinary |

   When an NPC is directly opposing the action, use their sheet (see `npcs.md` in the setting
   pack) as a reference point for a sensible DC rather than inventing one from nothing - a common
   rule of thumb is `10 + that NPC's relevant modifier` as a starting anchor, adjusted for context.
3. The application rolls `1d20`, adds the attribute modifier plus the skill's tier bonus (if a
   skill applies), and compares to the DC:
   - **Natural 20** (before any modifiers): automatic success, regardless of total - and an
     exceptional one, meaning the GM should narrate an extra benefit beyond the basic goal.
   - **Natural 1**: automatic failure, regardless of total - and a complication beyond just "it
     didn't work."
   - Otherwise: total >= DC is a success, total < DC is a failure. There's no separate "critical"
     tier for a very high or very low total short of a natural 20/1 - keep it binary past that,
     matching standard d20 conventions rather than stacking on extra margin-based tiers.
4. The model narrates the outcome in-fiction and proposes a state update (section 9) consistent
   with the result.

## 5. Experience and leveling

XP is awarded by the GM after meaningful events (a consequential check, a significant negotiation
or speech, a major historical beat) via the same `propose_state_update` tool used for everything
else, capped at 60 XP per single award (enforced in code) so no one event can vault a character
through multiple levels at once.

Threshold to advance from level N to N+1:
```
xp_to_next_level(level) = 100 + (level - 1) * 50
```
The application tracks cumulative XP and applies this formula automatically - crossing the
threshold immediately increments the character's level (and recomputes proficiency bonus) and sets
a `level_up_pending` flag. The GM should prompt the player to make their level-up choice at the
next natural pause once this flag is set, using the `propose_level_up` tool. Until that tool is
used, the pending choice just sits there - there's no time pressure to spend it.

**Level-up choice - pick exactly one:**

- **Option A - Skill upgrade.** Two skill steps total, maximum one step per individual skill
  (i.e. you can take two different skills up one tier each, or a single skill up one tier and
  another skill up one tier - never one skill up two tiers in the same level-up).
- **Option B - New feat.** Choose one feat, **by `feat_id`, from the system's curated feat
  catalog** (`FEAT_CATALOG` in `mechanics.py`) - never an invented one. Feats are gated by
  `min_level` (some are only available from level 5, 9, 13, or 17 onward) and occasionally by a
  prerequisite skill tier; the application rejects an ineligible or unknown `feat_id` with a clear
  reason rather than silently granting something not in the catalog. When narrating the choice,
  ground it in what actually happened during the level just completed - pick the catalog feat that
  best fits the earned moment, don't invent a new one.
- **Option C - Attribute improvement.** Distribute +3 points across attributes however you like
  (all three into one, or split up), subject to the 20 maximum per attribute.

**Reinforcement bonus:** if the choice made (the skill, the feat, or an attribute) was something
the character actually used in a check during the level just completed, the *next* level's XP
threshold gets a one-time 20% discount. The application tracks which attributes were exercised in
checks since the last level-up to determine this automatically - the GM doesn't need to self-report
"was this prominent," the record already reflects it.

## 6. Feats and liabilities

**Feats** are permanent passive abilities, gained one at a time via Option B above, or picked once
at character creation (a level-1-eligible feat only). Every feat in play comes from the fixed
`FEAT_CATALOG` - there is no freeform "invent a perk" path anymore; this exists specifically so the
system stays balanced as more characters and settings are built on it, rather than accumulating an
ever-growing, unaudited pile of one-off abilities.

**Liabilities** are the negative mirror, drawn from an equally fixed `LIABILITY_CATALOG`. Some are
typically taken at character creation (an inherent character trait, not something "earned");
others can be granted mid-campaign via `propose_state_update`'s `liabilities_gained` - but only by
a known `liability_id`, validated the same way. Liabilities can also be *removed* by later
narrative events (a specific accusation being definitively answered, a political vulnerability
being resolved) via `liabilities_removed`. A liability may optionally carry an **automatic
trigger** (e.g. Arrogance: on any failed Cunning/Presence check against a named rival, +1 enmity
pip) - those are enforced directly by the application whenever the condition is met, never left to
the model to remember to apply.

## 7. Enmity

Named rivals get an enmity track, 0-3 pips:

| Pips | Meaning |
|------|---------|
| 0 | Neutral or functional |
| 1 | Latent enmity, damaged trust |
| 2 | Active hostility, working against the character |
| 3 | Dangerous enemy - will act decisively |

Enmity accumulates through failed checks that involve a named rival as the target, broken
commitments, and narrative events - normally at most +1 pip per proposed state update, clamped to
the 0-3 range. Some liabilities specify an **automatic** enmity trigger (e.g. an "Arrogance"-style
liability that adds a pip on any failed Cunning or Presence check against a rival, regardless of
what the GM proposes) - those are enforced directly by the application whenever the trigger
condition is met, not left to the model to remember to apply.

## 8. Resources

Setting-defined categories (typically 6-12 - see `resource_schema.json` in the setting pack, e.g.
mass influence, party standing, personal safety) tracked as a 0-100 number under the hood,
displayed as one of six labels:

| Label | Range |
|-------|-------|
| Dominant | 85-100 |
| High | 70-84 |
| Rising/New | 55-69 |
| Moderate | 35-54 |
| Low | 18-34 |
| Suspect/Negative | 0-17 |

Keeping a real number underneath the label is what lets the application clamp changes to a
sensible per-check maximum the same way it does for anything else - the categorical label is a
display convenience, not a separate data type. Certain thresholds can carry automatic narrative
weight (e.g. a "Personal Safety" category hitting the bottom band making arrest or assassination
an active, GM-flagged threat) - that's setting-defined, described in the setting pack's
`resource_schema.json`.

## 9. Commitments

Formal promises or agreements the character has made, each tracked as: a short description, which
parties are bound, and a status (active / fulfilled / broken). These carry narrative weight (an
NPC who co-signed a commitment should face a higher bar to act against the character over it
publicly) without being a hard mechanical rule the application enforces - the GM is expected to
actually honor commitments in how it plays NPCs, the same way it's expected to stay in character
generally.

## 10. Canon and divergence

Same locked/contingent structure as `core_rules.md` section 7, with one addition: rather than only
tracking a bare `divergence_score` number, the application also keeps a **divergence log** - a
running list of short, specific entries describing exactly how and where the campaign has departed
from the historical record (e.g. "a document formalizing an alliance was signed that, historically,
never was"). This is what makes divergence legible to both the GM and the player over a long
campaign, rather than just an opaque counter.

## 11. Opposed checks and NPCs

NPCs built on this system carry the same six attributes and skill-tier structure as the player
character (see the setting pack's `npcs.md` for stat blocks). The application does not
automatically roll for NPCs - the GM uses an NPC's sheet as reference material for setting a
believable DC or narrating how well they perform in a contested scene, keeping the actual dice
resolution on the player character's side of the table, consistent with how the rest of this
engine works (the player's actions get checked; the world reacts).

## 12. State update protocol (tool reference)

Three tools, defined in `mechanics.py`:

- `request_check(attribute, skill, dc, description, target_npc)` - `skill` and `target_npc` are
  optional. Returns the roll result; the application computes it, the model never generates it.
- `propose_state_update(...)` - the general-purpose state-change tool: resource deltas,
  relationship deltas, enmity deltas, flags, commitments, XP awards, liabilities gained/removed
  (by `liability_id` only), canon events, divergence log entries. Validated and clamped before
  being applied. Feats are never granted here - only through `propose_level_up`.
- `propose_level_up(choice, ...)` - only valid while `level_up_pending` is true. Applies exactly
  one of the three level-up options from section 5, with its own bounds checking (max 2 skill
  steps / max 1 step per skill, +3 attribute points total, one feat by `feat_id` validated against
  the catalog and its prerequisites).

## 13. Character creation

A custom character (as opposed to a setting's premade signature character) is built with:
`systems/_shared/core.validate_and_build_character` - the same validated path the web character
creator uses. In short: attributes are the standard array `[16, 14, 13, 12, 10, 8]` assigned
one-to-one across the six attributes (no point-buy math to get wrong); skills draw from a 10-point
base budget (1 point per tier-step, so Proficiency costs 1 and Expertise costs 2 total), capped at 3
skills starting at Expertise; one level-1-eligible starting feat; up to 2 starting liabilities. Each
starting liability taken grants +2 bonus skill points (up to +4 for both), so a character built
"clean" starts leaner while one with real narrative baggage can afford more starting competence -
liabilities are a tradeoff for something, not a free downside. A setting's premade signature
character is hand-authored outside this budget and may exceed it (a famous historical figure should
feel a cut above a freshly-created one).
