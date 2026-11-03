# Key NPCs — Petrograd 1917 Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes. Stat blocks moved to `npc_sheets.json` (2026-11-03): the application
reads them and sets the DC of any check aimed at an NPC who has one (see the NPC SHEETS section of
the prompt and npcs.py).

## Vladimir Lenin
Chairman of the Bolshevik Central Committee, in hiding in Finland since the July Days (he returns
to Petrograd in disguise around 7 October). Sharp, impatient, tactically ruthless, allergic to
half-measures. Until he returns he exists for the player only as letters - furious ones, arriving
by courier, demanding the party take power now and treating every delay as betrayal. Respects
Trotsky's oratory and theoretical weight but clashes with him over timing: Trotsky wants the
rising tied to the Congress of Soviets for legitimacy, Lenin wants it before. Historically the two
became close allies through October; that's a plausible trajectory, not a guaranteed one - the
`lenins_trust` resource (which slides while the Central Committee dithers) should move with the
player's actual choices.


## Joseph Stalin
Editor of Pravda, a member of the Central Committee, organizationally capable but not yet the
dominant figure he'll become. Quiet, watchful, speaks less than others in Central Committee
debates but is always noting who's aligned with whom. Correct and professional toward Trotsky at
this stage - open hostility would be historically premature for late 1917. Dangerous precisely
because he appears unthreatening.


## Lev Kamenev
Central Committee member and leader of the Bolshevik fraction in the Soviet when the campaign opens
(he was released from his own July arrest weeks before Trotsky). The most cautious of the senior
Bolsheviks: he wants the new Soviet majority to build a broad socialist coalition and wait for the
Constituent Assembly. Historically he opposed the October insurrection outright and, with Zinoviev,
published the party's plans in Gorky's paper. Play him as a serious man making the "we will be
crushed" argument well, not as a straw man.


## Grigory Zinoviev
Central Committee member, in hiding since July (he was with Lenin in the hut at Razliv), close historically to Kamenev and similarly cautious about insurrection
timing. Competent orator himself, which creates some professional rivalry with Trotsky. Shares
almost the same stat block as Kamenev - the two reinforce each other's caution; when the GM plays
them acting jointly, it's reasonable to narrate their combined resolve as sturdier than either
alone (a narrative call, not a new mechanic - there's no rule for combining NPC stats).


## Alexander Kerensky
Minister-President of the Provisional Government. Increasingly isolated, prone to overestimating
his own popular support, genuinely alarmed by the Bolsheviks' growing strength in the Soviets.
The primary antagonist of the pre-October arc. Should be played as a serious, not cartoonish,
adversary - a man who genuinely believes he is defending the revolution against extremism on both
left and right.


## Alexandra Kollontai
Bolshevik feminist and organizer, later Deputy Commissar for Justice. A genuine, trusted ally.


## Maria Spiridonova
Left-SR leader. High Resolve, moderate everything else - unshakeable once committed. Will hold the
player to any coalition or land-question commitment made to her; treat broken commitments with her
as a serious enmity-track event, not a minor one.


## Julius Martov
Menshevik-Internationalist leader. High Intellect, moderate Presence, notably low Resolve when a
decisive break is called for - he agonizes over ruptures even when his politics point toward one.
If eventually in government, a Non-Condemnation commitment can hold for a long while but should
break over the Constituent Assembly dissolution ("the January problem") absent unusual player
intervention.


## Adolph Joffe
Diplomat, later Foreign Affairs commissar. High Intellect, solid Resolve, moderate Presence, good
operational Cunning. The player character's closest and most completely trusted political ally.


## Pavel Dybenko
Kronstadt sailors' leader. High Physique and Resolve, moderate everything else, notably low
Cunning - capable and impulsive in equal measure. Best used with a specific, narrow operational
role rather than open-ended authority, to keep his impulsiveness from improvising outcomes the
player didn't intend.


## Pyotr Krasnov
Cossack general. Historically released on parole and rejoined the Whites almost immediately -
a clean divergence point if the campaign handles him differently. Not yet a committed enemy at
first contact; genuinely undecided, weighing his options.


## Fyodor Dan
Menshevik leader and one of the three men of the Soviet presidium (with Chkheidze and Tsereteli)
when the campaign opens. Intelligent, principled, and convinced that a Bolshevik Soviet means civil
war and dictatorship. The player's most direct institutional opponent in September; fights every
motion in the plenum on the merits and on procedure.


## Nikolai Chkheidze
Georgian Menshevik, Chairman of the Petrograd Soviet since February. Tired, decent, a figurehead
more than a fighter by now. If the presidium falls he steps aside without a scene.


## Nikolai Podvoisky
Head of the Bolshevik Military Organization ("Voenka") - the party's network inside the
regiments. Knows which committee in which barracks can be counted on. Cautious about timing in a
practical way: he wants the garrison ready before anyone moves. The player's best source for where
each regiment really stands.


## Vladimir Antonov-Ovseenko
Former officer, Bolshevik since the summer, released from his own July arrest around the same
time as the player. Energetic and brave; becomes the MRC's operational chief and leads the
Winter Palace assault historically. A natural field commander for the player's side.


## Adding NPCs later
This is a starting roster, not an exhaustive one. When a new figure becomes important - someone
who will recur, or whom the player will argue with, bargain with or try to outwit - give them a
sheet with create_npc_sheet (the application validates it and starts a relationship entry).
Walk-ons don't need one.
