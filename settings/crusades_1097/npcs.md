# Key NPCs — Crusades 1097 Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes. Stat blocks live in `npc_sheets.json` (2026-11-03): the application reads
them and sets the DC of any check aimed at an NPC who has one (see npcs.py).

## Godfrey of Bouillon
Fellow crusade leader, Duke of Lower Lorraine - genuinely pious and comparatively idealistic among
the crusade's princes, generally a straight dealer even when it costs him. Historically becomes the
first ruler of the Kingdom of Jerusalem (under the title Defender of the Holy Sepulchre, not king).
A useful moral counterweight to Bohemond's opportunism.


## Raymond IV of Toulouse
The wealthiest and most senior of the crusade's princes, and Bohemond's most direct rival for
leadership - and, later, for Antioch itself. Proud, experienced, genuinely devout in his own way,
and unwilling to simply defer to a younger, poorer Norman adventurer.


## Emperor Alexios I Komnenos
Byzantine Emperor, not physically present with the marching army but a constant offstage presence
through his representatives - the crusaders swore him oaths to return reclaimed Byzantine territory,
a commitment (`oath_to_alexios`, tracked in `commitments`) that is already straining and that
Bohemond in particular has every incentive to break. A supremely capable political operator; should
never be played as naive.


## Tancred
Bohemond's nephew and most loyal lieutenant - a formidable and ambitious young warrior in his own
right, genuinely devoted to his uncle but not without appetites of his own for glory and territory.


## Adhemar of Le Puy
Papal legate and the expedition's real unifying moral and spiritual authority - historically the
figure most able to hold the fractious crusader princes together when personal rivalries threatened
to tear the expedition apart. Losing his mediating presence (he dies at Antioch in 1098,
historically) would be a major structural shift if the campaign reaches that point.


## Yaghi-Siyan
Seljuk governor of Antioch, the city the crusaders are marching toward. A capable, experienced
defender who knows exactly what a prolonged siege will cost both sides - should be played as a
serious military mind, not a pushover, right up until (and if) the historical betrayal that lets
the crusaders into the city actually occurs.


## Kerbogha of Mosul
A powerful Seljuk atabeg who historically arrives with a large relief army just as the crusaders
finally take Antioch, nearly reversing their victory. Not yet on the board at campaign start, but
worth having stats ready for - the GM should introduce him only once the Antioch arc is actually
underway.


## Adding NPCs later
This is a starting roster. When a new figure becomes important - someone who will recur, or whom
the player will argue with, bargain with or try to outwit - give them a sheet with create_npc_sheet
(the application validates it and starts a relationship entry). Walk-ons don't need one.
