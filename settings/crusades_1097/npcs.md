# Key NPCs — Crusades 1097 Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes plus **Monarch system stat blocks** for the GM - reference material for
setting believable DCs and playing contested scenes (see `systems/monarch/rules.md` section 11:
the application never rolls for NPCs directly, these numbers are GM reference data).

Attributes use the same 1-20 scale and `floor((score-10)/2)` modifier as the player character.
"Expertise" below means that skill sits at the Expertise tier for that NPC; anything not listed
should be treated as untrained if it ever comes up.

## Godfrey of Bouillon
Fellow crusade leader, Duke of Lower Lorraine - genuinely pious and comparatively idealistic among
the crusade's princes, generally a straight dealer even when it costs him. Historically becomes the
first ruler of the Kingdom of Jerusalem (under the title Defender of the Holy Sepulchre, not king).
A useful moral counterweight to Bohemond's opportunism.

**Stats:** Oration 13 (+1) · Intellect 14 (+2) · Resolve 17 (+3) · Presence 15 (+2) · Cunning 11 (+0) · Physique 16 (+3). Proficiency +2.
**Expertise:** Unyielding Oath, Battle Nerve.

## Raymond IV of Toulouse
The wealthiest and most senior of the crusade's princes, and Bohemond's most direct rival for
leadership - and, later, for Antioch itself. Proud, experienced, genuinely devout in his own way,
and unwilling to simply defer to a younger, poorer Norman adventurer.

**Stats:** Oration 13 (+1) · Intellect 14 (+2) · Resolve 15 (+2) · Presence 14 (+2) · Cunning 13 (+1) · Physique 12 (+1). Proficiency +3.
**Expertise:** Statecraft and Law, Terrain and Logistics.

## Emperor Alexios I Komnenos
Byzantine Emperor, not physically present with the marching army but a constant offstage presence
through his representatives - the crusaders swore him oaths to return reclaimed Byzantine territory,
a commitment (`oath_to_alexios`, tracked in `commitments`) that is already straining and that
Bohemond in particular has every incentive to break. A supremely capable political operator; should
never be played as naive.

**Stats:** Oration 15 (+2) · Intellect 18 (+4) · Resolve 15 (+2) · Presence 16 (+3) · Cunning 18 (+4) · Physique 12 (+1). Proficiency +4.
**Expertise:** Intrigue and Plot, Statecraft and Law, Reading Intentions.

## Tancred
Bohemond's nephew and most loyal lieutenant - a formidable and ambitious young warrior in his own
right, genuinely devoted to his uncle but not without appetites of his own for glory and territory.

**Stats:** Oration 12 (+1) · Intellect 12 (+1) · Resolve 14 (+2) · Presence 13 (+1) · Cunning 13 (+1) · Physique 17 (+3). Proficiency +2.
**Expertise:** Arms and Armor.

## Adhemar of Le Puy
Papal legate and the expedition's real unifying moral and spiritual authority - historically the
figure most able to hold the fractious crusader princes together when personal rivalries threatened
to tear the expedition apart. Losing his mediating presence (he dies at Antioch in 1098,
historically) would be a major structural shift if the campaign reaches that point.

**Stats:** Oration 15 (+2) · Intellect 15 (+2) · Resolve 16 (+3) · Presence 16 (+3) · Cunning 11 (+0) · Physique 12 (+1). Proficiency +2.
**Expertise:** Sermon and Preaching, Unyielding Oath.

## Yaghi-Siyan
Seljuk governor of Antioch, the city the crusaders are marching toward. A capable, experienced
defender who knows exactly what a prolonged siege will cost both sides - should be played as a
serious military mind, not a pushover, right up until (and if) the historical betrayal that lets
the crusaders into the city actually occurs.

**Stats:** Oration 12 (+1) · Intellect 14 (+2) · Resolve 14 (+2) · Presence 12 (+1) · Cunning 15 (+2) · Physique 13 (+1). Proficiency +3.
**Expertise:** Siegecraft and Engineering.

## Kerbogha of Mosul
A powerful Seljuk atabeg who historically arrives with a large relief army just as the crusaders
finally take Antioch, nearly reversing their victory. Not yet on the board at campaign start, but
worth having stats ready for - the GM should introduce him only once the Antioch arc is actually
underway.

**Stats:** Oration 13 (+1) · Intellect 13 (+1) · Resolve 15 (+2) · Presence 14 (+2) · Cunning 12 (+1) · Physique 16 (+3). Proficiency +3.
**Expertise:** Rallying the Host.

## Adding NPCs later
This is a starting roster, not an exhaustive one. The GM may introduce additional historical
figures (Robert of Normandy, Robert of Flanders, Stephen of Blois, further Seljuk or Fatimid
figures) as the campaign's needs dictate, initializing a relationship entry at neutral trust (0)
and giving them a reasonable Monarch stat block (attributes roughly 10-16 unless historically
exceptional, proficiency +2 or +3, one or two Expertise skills at most for a supporting figure)
rather than leaving them statless.
