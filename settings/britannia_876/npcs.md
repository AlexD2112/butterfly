# Key NPCs — Britannia 876 Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes. Stat blocks live in `npc_sheets.json` (2026-11-03): the application reads
them and sets the DC of any check aimed at an NPC who has one (see npcs.py).

## Guthrum
Leader of the Viking force currently encamped in the West, and the campaign's central antagonist.
Not a cartoon raider - a serious political operator who understands tribute, hostages, and treaty
as tools alongside the sword. Historically outmaneuvered Alfred repeatedly before Edington broke
his momentum; should be played as genuinely dangerous and often one step ahead until the player
earns otherwise.


## Halfdan Ragnarsson
Co-leader of the wider Great Heathen Army, now consolidating power in the North. Not physically
present in Wessex at campaign start, but his resources and reputation loom over everything Guthrum
does in the West - a useful NPC for the GM to invoke as an offstage pressure, or to bring onstage
later if the campaign's divergence path calls for it.


## Ceolwulf II of Mercia
King of Mercia, holding a fractured kingdom under Viking pressure. The Anglo-Saxon Chronicle later
dismisses him as a puppet king, but he should be played with more dignity than that - a ruler doing
real triage with worse cards than Alfred's, whose cooperation (or lack of it) matters enormously for
`saxon_unity`. Whether he's a collaborator or a survivor is exactly the kind of ambiguity the GM
should let play out rather than resolve up front.


## Ealhswith
Alfred's wife and Queen, of Mercian noble blood herself - a genuine, trusted ally and a real link to
the Mercian nobility in her own right, not just a consort.


## Odda, Ealdorman of Devon
A steady, capable West Saxon commander - historically led the force that broke a Viking army under
Ubba near Countisbury in early 878, an important morale turning point before Edington. Loyal without
being uncritical; will tell the player uncomfortable truths about military_readiness rather than
flatter them.


## Wulfhere, Ealdorman of Wiltshire
Outwardly a loyal ealdorman; historically later stripped of his lands for deserting Alfred and the
country against his sworn oath, at some unrecorded point during this crisis. A built-in enmity-track
NPC - his loyalty should feel real and untested at campaign start, with the GM free to let the
player's choices determine whether he holds or breaks, rather than the desertion being scripted to
happen regardless.


## Wærferth, Bishop of Worcester
A genuinely learned churchman, later a real collaborator of Alfred's on translation projects. Church
favor and the scholarly reputation of the court run substantially through relationships like this
one - worth cultivating deliberately, not just for piety's sake.


## Adding NPCs later
This is a starting roster. When a new figure becomes important - someone who will recur, or whom
the player will argue with, bargain with or try to outwit - give them a sheet with create_npc_sheet
(the application validates it and starts a relationship entry). Walk-ons don't need one.
