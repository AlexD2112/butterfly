# Core Rules — AI-Narrated Historical RPG Engine (v1)

This document is the engine-agnostic rulebook. It defines how the game works mechanically -
attributes, checks, turn structure, and the state-update protocol - independent of any particular
historical period or protagonist. A **setting pack** (see the interface at the end of this
document) supplies the actual content: who you're playing, who the NPCs are, what the world looks
like, and which historical beats are canon. `settings/petrograd_1917/` is the first setting pack.

Design philosophy: the LLM handles narration, dialogue, NPC behavior, and judgment calls. Code
handles the two things LLMs are bad at - fair randomness and keeping state consistent over a long
campaign. The rulebook is injected into every turn's context so the model has a fixed reference
for how mechanics work, rather than improvising them fresh each time.

## 1. Character sheet

A player character (and, at GM discretion, major NPCs) has:

- **Attributes** - rated 1 to 5 stars. 1 = untrained/poor, 3 = competent, 5 = legendary/historic
  ability. The generic attribute list (a setting pack may add period-specific ones, but should
  keep these seven as the baseline so mechanics stay portable):
  - `oratory` - public speaking, agitation, crowd persuasion
  - `theory` - ideological/intellectual argument, doctrine, strategic analysis
  - `organization` - administration, logistics, running an institution
  - `diplomacy` - one-on-one persuasion, negotiation, alliance-building
  - `subterfuge` - conspiracy, secrecy, evasion, reading hidden motives
  - `command` - leading people under pressure, military or otherwise
  - `endurance` - physical/mental resilience, resistance to stress, illness, imprisonment
- **Resources** - setting-defined pools (e.g. political capital, mass support, danger level).
  Each has a declared range (usually 0-100) and a short description of what high/low means.
- **Relationships** - a table of named NPCs, each with a trust/alignment score (-5 to +5) and a
  one-line note on the current state of the relationship. Updated by checks and by narrative
  events, never by simple narration alone (see section 4).
- **Flags** - booleans/short strings for anything binary that needs to persist (e.g.
  `in_exile: false`, `party_position: "Central Committee"`).

## 2. Checks (the resolution mechanic)

Any time the player attempts something with a real chance of failure or meaningful stakes, it's a
**check**, not free narration. Pure conversation, description, and roleplay never require a check -
only attempts to *change something* (persuade a skeptical crowd, talk your way past a guard,
out-maneuver a rival at a Central Committee meeting) do.

Procedure:
1. The GM (LLM) identifies the relevant attribute and sets a Difficulty Class (DC), using this
   scale: 5 trivial, 10 easy, 15 moderate, 20 hard, 25 very hard, 30 historic/near-impossible.
2. **The application rolls the die, not the model.** A real `1d20` is generated in code and passed
   into context as a fact ("Roll result: 14"). This is the single most important rule in this
   document - LLMs cannot generate fair randomness on their own, and letting the model "decide" a
   roll defeats the entire point of having a resolution mechanic. See section 6.
3. Total = roll + (attribute stars - 1) × 2. Compare to the DC:
   - Miss by 10+: **critical failure** - a complication beyond just "it didn't work."
   - Miss: **failure**.
   - Hit: **success**.
   - Beat by 10+: **critical success** - an extra benefit beyond the basic goal.
4. The model narrates the outcome in-fiction and proposes a state update (section 4) consistent
   with the result. Code validates the update before it's applied.

## 3. Turn structure

Three nested layers of "turn":

- **Scene** - a stretch of free-form roleplay: dialogue, description, wandering conversation. No
  state changes happen from scene narration alone.
- **Check** - as above, triggered mid-scene whenever the player attempts something uncertain.
  Produces a validated state update.
- **Strategic turn** - a periodic (setting-defined cadence, e.g. weekly) beat where accumulated
  consequences resolve: world-state drift, NPC reactions to the trend of recent player behavior,
  and canon-timeline advancement. This is where "the war grinds on," "unrest rises," etc. get
  applied, similar in spirit to a weekly tick in a simulation game, but narrated rather than
  formula-driven - though see section 7 for how canon events stay on track.

## 4. State update protocol

The model never edits state directly by writing prose that happens to imply a change. Every state
change is proposed via a structured tool call, e.g.:

```json
{
  "tool": "propose_state_update",
  "resource_deltas": {"political_capital": +3},
  "relationship_deltas": {"lenin": +1},
  "flags_set": {},
  "canon_event_triggered": null,
  "reasoning": "Persuaded the committee with a strong critical-success speech."
}
```

The application validates before applying:
- Deltas are clamped to a per-check maximum (setting-defined; prevents a single check from
  swinging a resource wildly).
- Resources are clamped to their declared range after the delta.
- `canon_event_triggered` is only accepted if that event's prerequisites (section 7) are actually
  met - the model can *propose* a historical beat firing, but code has the final say on whether
  conditions justify it.
- Anything that fails validation is rejected and the model is asked to revise, not silently
  dropped - keeps the narration and the state from diverging from each other.

## 5. Memory and context

Full history isn't replayed every turn. Instead:
- A **state digest** (current character sheet + resources + relationships + open plot threads) is
  reconstructed fresh each turn from the persisted state file - this is authoritative, not
  reconstructed from memory of past narration.
- A **recent history window** (last N scene exchanges, verbatim) gives short-term continuity.
- Older history is periodically **summarized** into a running campaign log (a few sentences per
  strategic turn), which stays in context indefinitely so long-past events aren't forgotten even
  once the verbatim window has rolled past them.
- A **canon/pinned facts** block (setting-defined, section 7) is always in context and never
  summarized away - this is what keeps the model from contradicting fixed history or prior
  irreversible player choices.

## 6. Why the app owns randomness

Worth stating plainly since it's the load-bearing guardrail of this whole design: language models
are not reliable random number generators and are not reliable arbiters of fairness under
pressure to produce a satisfying story. If the model "rolls the dice" itself, outcomes will drift
toward whatever's narratively convenient in the moment, which erodes stakes over a long campaign.
Generating the roll in code and handing it to the model as a given fact ("here is what happened,
narrate it") keeps the GM's storytelling role separate from the game's fairness guarantee.

## 7. Canon and divergence

Historical settings need a way to stay grounded without being a straitjacket. Each setting pack
defines a **canon timeline**: a list of historical beats, each tagged as:
- **Locked** - happens regardless of player action (e.g. background events far from the player's
  reach). Referenced for flavor, never contradicted.
- **Contingent** - happens unless player actions plausibly prevent/alter it, tracked via specific
  prerequisite state conditions defined by the setting pack (e.g. "only skippable if
  `political_capital > X` and a specific relationship flag is set").

A running `divergence_score` increases as contingent beats get altered from their historical
outcome. This isn't a "good/bad" score - it's a tracking mechanism so the GM (and the player) has
an honest sense of how far the campaign has drifted from real history, and so later contingent
events can reference "the world as it now stands" instead of assuming an unaltered timeline.

## 8. Setting pack interface

A setting pack is a folder providing:
- `character_sheet.json` - starting attributes/resources/relationships/flags for the player
  character.
- `npcs.md` - key NPCs: brief bio, starting relationship value, behavioral notes for the GM.
- `factions.md` - major factions/institutions and their starting state.
- `canon_timeline.md` - the locked/contingent event list from section 7, in chronological order,
  each with prerequisites and a brief description of both the historical outcome and what
  "diverged" might look like.
- `world_state_schema.json` - the resource pools this setting uses, their ranges, and short
  descriptions.
- `opening_scene.md` - the scenario hook the campaign starts from.

The core engine (rules above, plus the turn-loop implementation) should never need to change to
support a new setting pack - only these files do.
