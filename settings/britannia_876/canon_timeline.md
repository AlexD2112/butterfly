# Canon Timeline — Britannia 876 Setting Pack

Locked events have already happened as the campaign opens (November 876) and should be treated as
settled history in narration. Contingent events are the real historical outcomes the campaign can
either follow toward or diverge from - firing one should feel earned through play, via
`canon_event_triggered` in a `propose_state_update` call, not assigned on a schedule.

## Locked (already true at campaign start)
- **Wareham and Exeter taken** - Guthrum's force seized Wareham, then slipped past a besieging West
  Saxon fleet (which was badly damaged by a storm) to take Exeter. Alfred has already been forced
  into at least one prior tribute/hostage arrangement that didn't hold.
- **Guthrum slips the fleet** - see above; this is why the threat still active in the West feels
  urgent rather than newly arrived.

## Contingent (the historical path, not guaranteed)
- **Athelney retreat** (historically early January 878) - a sudden, devastating Viking strike
  (from Guthrum's remaining force at Chippenham) drives Alfred into the Somerset marshes with a
  small band, the lowest point of his reign. Should only fire if the campaign's choices plausibly
  lead there - a strong enough `military_readiness`/`saxon_unity` position might avert it or change
  its shape entirely, which is a legitimate divergence, not a failure state.
- **Battle of Edington** (historically May 878) - Alfred rallies the Wessex fyrd from Athelney and
  wins a decisive victory over Guthrum. The campaign's central "can this be earned" moment.
- **Treaty of Wedmore** - the peace settlement following Edington.
- **Guthrum baptized as Æthelstan** - Guthrum's conversion to Christianity, sponsored by Alfred
  himself, as part of the Wedmore settlement - a genuinely strange and consequential piece of real
  history if it fires.
- **Burh system established** - Alfred's network of fortified towns, the beginning of a real
  defensive infrastructure for Wessex (and, later, a broader English kingdom).
- **Alfred and Guthrum treaty lines drawn** - the formal boundary agreement that effectively defines
  the Danelaw.

## Divergence guidance
This setting has an unusually hard historical floor (Wessex's actual survival) and an unusually
open historical ceiling (whether "England" as an idea coheres faster, slower, or differently than
it did). The GM should feel free to let `saxon_unity` and the player's relationship with figures
like Ceolwulf II produce a meaningfully different shape of English unification than the real one,
logged in the divergence log, without needing Wessex itself to fall for the campaign to feel like
it matters.
