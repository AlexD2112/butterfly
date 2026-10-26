# Key NPCs — Petrograd 1917 Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes plus **Red Admiral system stat blocks** for the GM - reference material
for setting believable DCs and playing contested scenes (see `systems/red_admiral/rules.md`
section 11: the application never rolls for NPCs directly, these numbers are GM reference data).

Attributes use the same 1-20 scale and `floor((score-10)/2)` modifier as the player character.
"Expertise" below means that skill sits at the Expertise tier (attribute modifier + 2x proficiency
bonus) for that NPC; anything not listed as Expertise or Proficiency should be treated as
untrained (attribute modifier only) if it ever comes up.

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

**Stats:** Oration 14 (+2) · Intellect 19 (+4) · Resolve 18 (+4) · Presence 15 (+2) · Cunning 19 (+4) · Physique 10 (+0). Proficiency +3.
**Expertise:** Revolutionary Theory, Written Propaganda, Factional Maneuvering, Psychological Endurance.

## Joseph Stalin
Editor of Pravda, a member of the Central Committee, organizationally capable but not yet the
dominant figure he'll become. Quiet, watchful, speaks less than others in Central Committee
debates but is always noting who's aligned with whom. Correct and professional toward Trotsky at
this stage - open hostility would be historically premature for late 1917. Dangerous precisely
because he appears unthreatening.

**Stats:** Oration 10 (+0) · Intellect 13 (+1) · Resolve 17 (+3) · Presence 12 (+1) · Cunning 18 (+4) · Physique 13 (+1). Proficiency +2.
**Expertise:** Factional Maneuvering, Reading Hidden Agendas.

## Lev Kamenev
Central Committee member and leader of the Bolshevik fraction in the Soviet when the campaign opens
(he was released from his own July arrest weeks before Trotsky). The most cautious of the senior
Bolsheviks: he wants the new Soviet majority to build a broad socialist coalition and wait for the
Constituent Assembly. Historically he opposed the October insurrection outright and, with Zinoviev,
published the party's plans in Gorky's paper. Play him as a serious man making the "we will be
crushed" argument well, not as a straw man.

**Stats:** Oration 14 (+2) · Intellect 14 (+2) · Resolve 7 (−2) · Presence 12 (+1) · Cunning 12 (+1) · Physique 10 (+0). Proficiency +2.

## Grigory Zinoviev
Central Committee member, in hiding since July (he was with Lenin in the hut at Razliv), close historically to Kamenev and similarly cautious about insurrection
timing. Competent orator himself, which creates some professional rivalry with Trotsky. Shares
almost the same stat block as Kamenev - the two reinforce each other's caution; when the GM plays
them acting jointly, it's reasonable to narrate their combined resolve as sturdier than either
alone (a narrative call, not a new mechanic - there's no rule for combining NPC stats).

**Stats:** Oration 14 (+2) · Intellect 13 (+1) · Resolve 7 (−2) · Presence 11 (+0) · Cunning 12 (+1) · Physique 10 (+0). Proficiency +2.

## Alexander Kerensky
Minister-President of the Provisional Government. Increasingly isolated, prone to overestimating
his own popular support, genuinely alarmed by the Bolsheviks' growing strength in the Soviets.
The primary antagonist of the pre-October arc. Should be played as a serious, not cartoonish,
adversary - a man who genuinely believes he is defending the revolution against extremism on both
left and right.

**Stats:** Oration 16 (+3) · Intellect 14 (+2) · Resolve 12 (+1) · Presence 15 (+2) · Cunning 13 (+1) · Physique 11 (+0). Proficiency +2.
**Expertise:** Platform Speechmaking.

## Alexandra Kollontai
Bolshevik feminist and organizer, later Deputy Commissar for Justice. A genuine, trusted ally.

**Stats:** Oration 15 (+2) · Intellect 15 (+2) · Resolve 14 (+2) · Presence 17 (+3) · Cunning 13 (+1) · Physique 11 (+0). Proficiency +2.
**Expertise:** Personal Persuasion, Cultivating Allies.

## Maria Spiridonova
Left-SR leader. High Resolve, moderate everything else - unshakeable once committed. Will hold the
player to any coalition or land-question commitment made to her; treat broken commitments with her
as a serious enmity-track event, not a minor one.

**Stats:** Oration 14 (+2) · Intellect 14 (+2) · Resolve 18 (+4) · Presence 14 (+2) · Cunning 12 (+1) · Physique 12 (+1). Proficiency +2.

## Julius Martov
Menshevik-Internationalist leader. High Intellect, moderate Presence, notably low Resolve when a
decisive break is called for - he agonizes over ruptures even when his politics point toward one.
If eventually in government, a Non-Condemnation commitment can hold for a long while but should
break over the Constituent Assembly dissolution ("the January problem") absent unusual player
intervention.

**Stats:** Oration 15 (+2) · Intellect 18 (+4) · Resolve 9 (−1) · Presence 14 (+2) · Cunning 12 (+1) · Physique 10 (+0). Proficiency +2.

## Adolph Joffe
Diplomat, later Foreign Affairs commissar. High Intellect, solid Resolve, moderate Presence, good
operational Cunning. The player character's closest and most completely trusted political ally.

**Stats:** Oration 12 (+1) · Intellect 17 (+3) · Resolve 15 (+2) · Presence 13 (+1) · Cunning 15 (+2) · Physique 11 (+0). Proficiency +3.

## Pavel Dybenko
Kronstadt sailors' leader. High Physique and Resolve, moderate everything else, notably low
Cunning - capable and impulsive in equal measure. Best used with a specific, narrow operational
role rather than open-ended authority, to keep his impulsiveness from improvising outcomes the
player didn't intend.

**Stats:** Oration 12 (+1) · Intellect 11 (+0) · Resolve 16 (+3) · Presence 13 (+1) · Cunning 8 (−1) · Physique 17 (+3). Proficiency +2.

## Pyotr Krasnov
Cossack general. Historically released on parole and rejoined the Whites almost immediately -
a clean divergence point if the campaign handles him differently. Not yet a committed enemy at
first contact; genuinely undecided, weighing his options.

**Stats:** Oration 8 (−1) · Intellect 11 (+0) · Resolve 17 (+3) · Presence 14 (+2) · Cunning 6 (−2) · Physique 16 (+3). Proficiency +3.

## Fyodor Dan
Menshevik leader and one of the three men of the Soviet presidium (with Chkheidze and Tsereteli)
when the campaign opens. Intelligent, principled, and convinced that a Bolshevik Soviet means civil
war and dictatorship. The player's most direct institutional opponent in September; fights every
motion in the plenum on the merits and on procedure.

**Stats:** Oration 14 (+2) · Intellect 16 (+3) · Resolve 13 (+1) · Presence 13 (+1) · Cunning 14 (+2) · Physique 10 (+0). Proficiency +2.
**Expertise:** Debate and Public Argument.

## Nikolai Chkheidze
Georgian Menshevik, Chairman of the Petrograd Soviet since February. Tired, decent, a figurehead
more than a fighter by now. If the presidium falls he steps aside without a scene.

**Stats:** Oration 13 (+1) · Intellect 13 (+1) · Resolve 11 (+0) · Presence 14 (+2) · Cunning 10 (+0) · Physique 9 (−1). Proficiency +2.

## Nikolai Podvoisky
Head of the Bolshevik Military Organization ("Voenka") - the party's network inside the
regiments. Knows which committee in which barracks can be counted on. Cautious about timing in a
practical way: he wants the garrison ready before anyone moves. The player's best source for where
each regiment really stands.

**Stats:** Oration 11 (+0) · Intellect 13 (+1) · Resolve 15 (+2) · Presence 14 (+2) · Cunning 14 (+2) · Physique 13 (+1). Proficiency +2.
**Expertise:** Military Strategic Planning.

## Vladimir Antonov-Ovseenko
Former officer, Bolshevik since the summer, released from his own July arrest around the same
time as the player. Energetic and brave; becomes the MRC's operational chief and leads the
Winter Palace assault historically. A natural field commander for the player's side.

**Stats:** Oration 12 (+1) · Intellect 14 (+2) · Resolve 16 (+3) · Presence 14 (+2) · Cunning 12 (+1) · Physique 13 (+1). Proficiency +2.
**Expertise:** Crisis Command.

## Adding NPCs later
This is a starting roster, not an exhaustive one. The GM may introduce additional historical
figures (Bukharin, Dzerzhinsky, Antonov-Ovseenko, foreign diplomats, military officers) as the
campaign's needs dictate. When it does, it should initialize a relationship entry for them in the
state file at a neutral starting trust (0) unless there's a clear historical reason to start
otherwise, and should give them a reasonable Red Admiral stat block (attributes roughly 10-16
unless the figure was historically exceptional in some dimension, proficiency +2 or +3, one or two
Expertise skills at most for a supporting figure) rather than leaving them statless - even a rough
GM-improvised block is better than none, since it's what keeps DC-setting for scenes involving them
grounded rather than arbitrary.
