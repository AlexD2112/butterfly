# Canon Timeline — Petrograd 1917 Setting Pack

Per `core_rules.md` section 7: **locked** events happen regardless of player action (background,
out of the player's reach). **Contingent** events happen unless player actions plausibly alter
them - each has explicit prerequisites the code checks before accepting a
`canon_event_triggered` state update. This is a starting list for the opening arc (autumn 1917
through the founding of the Red Army); extend it as the campaign moves further into the Civil War.

## Locked

- **1917-09-25** — Trotsky elected Chairman of the Petrograd Soviet. (Campaign start - already
  true at turn one.)
- **1917-10 (early)** — Lenin returns from hiding in Finland to Petrograd in disguise. Happens
  regardless of player action; the player may react to it but not prevent or delay it.
- **Ongoing** — World War I continues on the Eastern Front throughout the campaign's opening arc;
  Russia's military position keeps deteriorating in the background regardless of player choices
  before Brest-Litovsk becomes directly actionable.

## Contingent

- **The Central Committee vote for insurrection** (historically ~1917-10-10). Prerequisite to
  accept as fired: `party_standing >= 60` and `lenin.trust >= 2` (the player has enough
  standing and Lenin's confidence to carry the vote). If prerequisites aren't met by the time the
  GM judges the beat is narratively due, the historical vote can be narrated as **delayed or
  contested** rather than force-fired - Kamenev/Zinoviev's historical public opposition is a
  ready-made complication to lean on.
- **The October Revolution / storming of key institutions** (historically 1917-10-24/25).
  Prerequisite: the insurrection vote beat has already fired. Outcome (Bolshevik-Soviet seizure of
  power) should be treated as *likely but not automatic* - a critical-failure result on a
  load-bearing check during the insurrection itself (e.g. securing the garrison's loyalty) is a
  legitimate way for this to go worse than history, consistent with `divergence_score` tracking.
- **Formation of Sovnarkom; Trotsky becomes People's Commissar for Foreign Affairs**.
  Prerequisite: the October beat fired with a Bolshevik-favorable outcome.
- **Brest-Litovsk negotiations begin** (historically starting December 1917). Prerequisite:
  Sovnarkom formed. This is a strong player-agency arc - Trotsky's real "neither war nor peace"
  stance was his own controversial call, not Lenin's, and is a good candidate for meaningful
  divergence if the player argues or negotiates differently.
- **Treaty of Brest-Litovsk signed** (historically March 1918). Prerequisite: negotiations beat
  fired. Terms (how punishing the treaty is) should scale with how the negotiation checks went,
  not be fixed regardless of play.
- **Trotsky becomes People's Commissar for War; founds the Red Army** (historically spring 1918).
  Prerequisite: Sovnarkom formed. This is where `command` attribute growth becomes central - expect
  this attribute to rise over subsequent checks if the player leans into this role.
- **Russian Civil War begins in earnest** (historically through 1918). Prerequisite: Red Army
  founding beat fired. Open-ended from here - this setting pack currently only scaffolds through
  the Red Army's founding; extend this file with further Civil War beats as the campaign reaches
  that point.

## Divergence notes

Nothing above should be treated as inevitable in narration even when marked "contingent" - the
prerequisites gate whether the *state update* is accepted, but the GM should be narrating genuine
uncertainty in the run-up to each beat, not a foregone conclusion the mechanics happen to confirm
later. If the player pushes hard enough in a different direction that a contingent beat's
prerequisites are never met, don't force it - let the campaign genuinely diverge and note it in
the running `divergence_score`.
