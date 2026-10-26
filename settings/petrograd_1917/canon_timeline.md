# Canon Timeline — Petrograd 1917 Setting Pack

All dates are Julian (Old Style), as they were in Russia in 1917. The campaign opens on
**4 September 1917**, the day Trotsky walks out of Kresty on bail.

The application enforces this timeline (see `dynamics.json` and `dynamics.py`):
- **Locked** events fire on their own when the calendar reaches their date. The GM narrates them;
  nobody can stop them.
- **Contingent** events fire only when their requirements are met. A `canon_event_triggered` that
  fails them is rejected and the GM is told what's missing. Several fire through `call_vote`.
- The GM moves the calendar with `days_elapsed`. Every day, the Provisional Government's stability
  and Petrograd's bread supply fall on their own, so waiting has a cost.

## Already true at the start

- **27–31 August** — The Kornilov affair. The general's march on the capital collapses; workers,
  railwaymen and Soviet agitators stop it; Red Guards are armed. Kerensky is gravely weakened.
- **31 August** — The Petrograd Soviet passes a Bolshevik resolution for the first time.
- **4 September** — Trotsky released from Kresty on bail. The July charges remain open.

## The road to October

- **~9 September (contingent: `presidium_no_confidence`)** — Historically, a Bolshevik motion of no
  confidence carried and the Menshevik-SR presidium of Chkheidze, Tsereteli and Dan resigned. In
  play: a vote on the plenum chart. It can fail.
- **14–22 September (locked: `democratic_conference`)** — The Democratic Conference meets to widen
  the government's base. The Bolsheviks attend; Kamenev favors working through it, Lenin's letters
  from Finland call it a farce.
- **Mid-September (background)** — Lenin's letters "The Bolsheviks Must Take Power" and "Marxism
  and Insurrection" reach the Central Committee, which, alarmed, keeps them from circulating.
- **25 September (contingent: `trotsky_elected_chairman`)** — Historically, Trotsky was elected
  Chairman of the Petrograd Soviet. In play: requires the old presidium to have fallen and real
  standing in the Soviet.
- **25 September (locked: `third_coalition_formed`)** — Kerensky's third coalition cabinet, with the
  Kadets.
- **Late September (threshold)** — If the Central Committee still hasn't moved and Lenin's trust is
  slipping, Lenin threatens to resign from it to agitate in the ranks.
- **7 October (locked: `pre_parliament_opens`; contingent: `pre_parliament_walkout`)** — The
  Pre-Parliament opens; historically Trotsky read the Bolshevik declaration and led the walkout.
- **~7 October (locked: `lenin_returns_to_petrograd`)** — Lenin comes back from Finland in disguise
  and hides in the Vyborg district.
- **~8 October (locked: `garrison_transfer_order`)** — The government orders garrison regiments to
  the front. It backfires: even neutral regiments start looking to the Soviet.
- **10 October (contingent: `central_committee_insurrection_vote`)** — Historically, the Central
  Committee voted 10 to 2 to put armed insurrection on the agenda, Kamenev and Zinoviev against. In
  play: a `call_vote` on the Central Committee chart. Kamenev and Zinoviev become rivals if it passes.
- **~16 October (contingent: `mrc_formed`)** — The Soviet creates the Military-Revolutionary
  Committee - nominally to defend the capital, in fact the staff of the rising.
- **18 October (background)** — Historically, Kamenev published the party's plans in Gorky's
  *Novaya Zhizn*. A GM may use this if the vote fired and Kamenev's enmity is high.
- **21–23 October (background)** — Historically, the garrison regiments recognized the MRC's
  authority, and Trotsky won over the Peter and Paul Fortress in person. In play this is `sway_unit`.
- **From 22 October (threshold: `kerensky_strikes_first`)** — If the government is still standing,
  Kerensky moves first: presses seized, bridges raised, arrests ordered.
- **24–25 October (contingent: `october_revolution`)** — The seizure of the city. Requires the vote,
  the MRC, a garrison that will follow (derived `garrison_loyalty` ≥ 72) and a government at
  stability ≤ 30. Play the objectives out with `resolve_confrontation`.
- **25 October (milestone)** — The Second All-Russian Congress of Soviets opens at the Smolny.

## After October (scaffolding only)

- **`sovnarkom_formed`** (26 October) — Council of People's Commissars; Trotsky historically
  People's Commissar for Foreign Affairs.
- **`brest_litovsk_negotiations_begin`** (November–December) and **`brest_litovsk_signed`**
  (3 March 1918) — Trotsky's real "neither war nor peace" gamble is strong divergence territory.
  The treaty's terms should follow how the negotiation checks went.
- **`red_army_founded`** (February–March 1918) and **`civil_war_begins`** — extend this file and
  `dynamics.json` when a campaign gets there.

## Divergence

Requirements gate whether the application accepts a beat; they don't make it inevitable. Narrate
real uncertainty in the run-up. If the player never meets a contingent beat's requirements, the
campaign diverges - log it with `divergence_note`. Locked dates still arrive regardless.
