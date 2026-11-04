# Key NPCs — Rome 59 BCE Setting Pack

Player-facing relationship/trust values live in the state file (`relationships`/`enmity`); this
file is behavioral notes. Stat blocks live in `npc_sheets.json` (2026-11-03): the application reads
them and sets the DC of any check aimed at an NPC who has one (see npcs.py).

## Gnaeus Pompeius Magnus (Pompey)
Fellow Triumvir, Rome's most celebrated living general, about to become Caesar's son-in-law through
marriage to Julia. Immensely popular, genuinely capable, but proud and increasingly sensitive to
being seen as the junior partner in an alliance he originally expected to dominate. The relationship
should feel real and warm at the start, with the seeds of the later rupture (which this campaign
may or may not reach) visible if the player looks for them.


## Marcus Licinius Crassus
Fellow Triumvir, the wealthiest man in Rome, and the alliance's financial engine. Transactional
almost to a fault - his loyalty runs through what the arrangement gets him, not personal warmth,
though that doesn't make it unreliable, just conditional.


## Marcus Porcius Cato (the Younger)
The Senate's most implacable defender of the old constitutional order, and Caesar's most
principled opponent - not corrupt, not personally vindictive in the ordinary sense, which is
exactly what makes him dangerous: he cannot be bought and will not bend. A defining enmity NPC.


## Marcus Calpurnius Bibulus
Caesar's fellow consul this year, and his single biggest procedural obstacle - having largely given
up on stopping Caesar through ordinary politics, he has taken to "watching the skies" for
unfavorable omens as a technical basis to declare Caesar's legislation invalid. Petty in method,
genuinely committed in conviction.


## Marcus Tullius Cicero
Rome's finest orator and a former consul, watching the Triumvirate's rise with real alarm but not
yet openly at war with it. Increasingly isolated from a Senate that no longer quite knows what to do
with him. Could become a genuine ally if courted, or - following the historical path - a target once
Clodius turns on him.


## Publius Clodius Pulcher
A chaotic, brilliant populist operator, recently helped across from patrician to plebeian status by
Caesar specifically so he could stand for tribune - a favor that comes with real risk, since
Clodius's agenda is his own, not Caesar's. Historically the engineer of Cicero's exile the following
year; whether that happens here, and how, is very much in the player's hands.


## Julia
Caesar's daughter, about to marry Pompey to seal the Triumvirate's alliance. Genuinely, warmly
loved by her father and - unusually for a purely political marriage - by her new husband as well.
The single most stabilizing relationship in the Triumvirate's structure, which also makes her
wellbeing a real strategic vulnerability worth the GM's attention.


## Adding NPCs later
This is a starting roster. When a new figure becomes important - someone who will recur, or whom
the player will argue with, bargain with or try to outwit - give them a sheet with create_npc_sheet
(the application validates it and starts a relationship entry). Walk-ons don't need one.
