"""
Style check (2026-11-18): catches the habits that made play read badly, and feeds them back.

Reported from a long Petrograd session: every scene ended with someone "waiting for your answer"
or a bell; the narrator explained what each moment meant ("That restraint costs you nothing.");
everyone spoke in epigrams; NPCs read the player's offers back to him and fired lists of
questions. The prompt already forbade most of this, and the model did it anyway - rules buried in a
long prompt fade. So after every turn this module checks the narration with cheap patterns and:
  - scrub(): deletes the few things that are safe to delete mechanically (a trailing "X waits."
    sentence with no dialogue in it, "+20 XP"/"Level up" lines written into the prose);
  - record(): keeps the notes and the last few closing lines in state["_style"];
  - prompt_section(): puts them at the very end of the next system prompt, where recency helps:
    "Your last reply ended on someone waiting; it explained the moment; ...".
"""

import re

import council

_SENT = re.compile(r"(?<=[.!?\"])\s+")
WAIT = re.compile(r"\b(waits?|waiting|does not move|doesn't move|keeps (?:his|her|their) eyes|eyes on you|"
                  r"for your (?:answer|reply|response)|expects an answer)\b", re.I)
BELL = re.compile(r"\b(?:a|the) bell (?:rings|sounds|rang|goes)\b|\bbell sounds\b", re.I)
VERDICT = re.compile(
    r"(?:^|[.!?]\s+)(?:You have (?:made|bought|preserved|given|won|turned|forced|kept|left)\b[^\"]{0,120}\."
    r"|That (?:restraint|answer|move|choice|distinction|gesture) (?:costs?|changes?|buys?|matters?)\b"
    r"|The (?:remaining|real|only) question (?:is|now)\b"
    r"|[A-Z][a-z]+ is no longer\b[^.]{0,80}\.\s+(?:He|She|It|They) (?:is|are)\b"
    r"|The \w+ has \w+; the \w+ has not\.)")
EPIGRAM = re.compile(r"\b[A-Z][a-z]+(?: \w+)?, yes\. [A-Z][a-z]+(?: \w+)?, no\.")
GESTURES = re.compile(r"\b(taps?|tapping) (?:the|his|her|a) (?:page|paper|table|sheet|clauses?|sentence)|"
                      r"\bfolds? (?:the|his|her) (?:paper|page|sheet|agenda|arms)|lowers (?:his|her) voice|"
                      r"\bstudies you\b|face closes|\bleans (?:closer|nearer|in|toward you)\b", re.I)
MECHANICS = re.compile(r"\b\d+\s*XP\b|\bXP!|\bLevel \d+!|\blevel(?:ed)? up\b|\b(?:rolled|a roll of|the dice|DC \d+)\b", re.I)
MECH_LINE = re.compile(r"^[^\w\"]*(?:Progress:.*XP.*|Level \d+!?|\+\d+ XP!?)\s*$", re.I | re.M)


def _paragraphs(text):
    return [p.strip() for p in re.split(r"\n\s*\n|\n", text or "") if p.strip()]


def scrub(text):
    """Deterministic removals only: XP/level lines in the prose, and a closing sentence (outside any
    dialogue) whose whole job is to say someone is waiting. Leaves the text untouched otherwise."""
    if not text:
        return text
    text = MECH_LINE.sub("", text).strip()
    paras = _paragraphs(text)
    if not paras:
        return text
    last = paras[-1]
    sents = _SENT.split(last)
    tail = sents[-1] if sents else ""
    if '"' in tail or not WAIT.search(tail) or len(tail) >= 110:
        return text
    if len(sents) > 1 or len(paras) > 1:
        return text[: text.rfind(tail)].rstrip()
    return text


def lint(text):
    notes = []
    paras = _paragraphs(text)
    if not paras:
        return notes
    ending = " ".join(_SENT.split(paras[-1])[-2:])
    if WAIT.search(ending):
        notes.append("it ended on someone waiting for the player")
    if BELL.search(text):
        notes.append("it used a bell as punctuation")
    m = VERDICT.search(text)
    if m:
        quote = m.group(0).strip(".!? \n")[:90]
        notes.append(f"the narrator explained the moment (\"{quote}\")")
    if EPIGRAM.search(text):
        notes.append("someone spoke in a balanced epigram (\"X, yes. Y, no.\")")
    gestures = [g.group(0).lower() for g in GESTURES.finditer(text)]
    if len(gestures) >= 2:
        notes.append("stock gestures (" + ", ".join(sorted(set(gestures)))[:90] + ")")
    last_quote = re.findall(r"\"([^\"]+)\"", paras[-1]) or re.findall(r"\"([^\"]+)\"", text)[-1:]
    if last_quote and sum(q.count("?") for q in last_quote) >= 3:
        notes.append("it ended with a string of questions at the player")
    if MECHANICS.search(text):
        notes.append("game numbers (XP, levels, rolls) appeared in the prose")
    short = [p for p in paras if len(_SENT.split(p)) == 1 and '"' not in p]
    if len(paras) >= 6 and len(short) >= len(paras) * 0.6:
        notes.append("it was a string of one-line paragraphs")
    low = text.lower()
    banned = [w for w in council.BANNED_WORDS if re.search(r"\b" + re.escape(w.lower()) + r"\b", low)]
    if banned:
        notes.append("banned words: " + ", ".join(banned[:4]))
    return notes


def closing_line(text):
    paras = _paragraphs(text)
    if not paras:
        return ""
    sents = _SENT.split(paras[-1])
    return sents[-1][:140] if sents else ""


def record(state, narration):
    s = state.setdefault("_style", {})
    s["notes"] = lint(narration)
    s["turn"] = state.get("turn_number", 0)
    endings = (s.get("endings") or []) + [closing_line(narration)]
    s["endings"] = [e for e in endings if e][-3:]
    return s["notes"]


def prompt_section(state):
    s = state.get("_style") or {}
    parts = []
    if s.get("notes"):
        parts.append("Your last reply: " + "; ".join(s["notes"]) + ". Don't do that again.")
    if len(s.get("endings") or []) >= 2:
        parts.append("Your recent replies ended: " + " / ".join(f"\"{e}\"" for e in s["endings"])
                     + ". End this one differently.")
    if not parts:
        return ""
    return "\n# STYLE CHECK ON YOUR RECENT REPLIES (automatic)\n" + "\n".join(parts)
