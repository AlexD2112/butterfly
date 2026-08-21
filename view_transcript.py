"""
Local admin tool for browsing full game transcripts saved by server.py's turn route to the `turns`
table (see db.py) - deliberately a script you run on this machine, NOT a web route. The app has no
concept of "admin" anywhere else in it (every existing route only ever shows a user their own data),
and adding one just for this would mean new auth code and a new piece of public attack surface for
a feature that's only ever meant for whoever already has filesystem access to butterfly.db - the
person running the server. A script needs neither.

Usage:
    python view_transcript.py --list              list every character across every account
    python view_transcript.py <character_id>       print that character's full turn-by-turn transcript

Only turns played after this feature shipped (2026-08-21) are recorded - anything played earlier
has no `turns` rows, since the data simply wasn't being saved before.
"""

import argparse
import json

import db

db.init_db()


def _list_characters():
    rows = db.list_characters_with_turn_counts()
    if not rows:
        print("No characters exist yet.")
        return
    for r in rows:
        who = r["username"] + (" [local/shared]" if r["is_local"] else "")
        print(f"#{r['id']:<5} {r['name']:<28} {r['setting_key']:<18} {who:<25} {r['turn_count']} turn(s) recorded")


def _format_debug_event(ev):
    kind = ev.get("type")
    if kind == "check":
        skill = f" ({ev['skill']})" if ev.get("skill") else ""
        return f"Check: {ev.get('attribute')}{skill} vs DC {ev.get('dc')} -> rolled {ev.get('roll')} = {ev.get('degree')}"
    if kind == "state_update":
        return f"State update ({len(ev.get('changes', []))} change(s)): {ev.get('reasoning', '')}"
    if kind == "level_up":
        return f"LEVEL UP: {ev.get('reasoning', '')}"
    if kind == "canon_event":
        return f"Canon event fired: {ev.get('key')}"
    if kind == "council":
        note = ev.get("aborted_reason") or ev.get("disagreement_notes") or "reviewed cleanly"
        return f"Council Mode: {len(ev.get('judges_selected', []))} judge(s) - {note}"
    if kind == "budget_exhausted":
        return "Budget ran out mid-turn"
    return None


def _print_transcript(character_id):
    conn = db.get_db()
    char = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    conn.close()
    if char is None:
        print(f"No character with id {character_id}. Run --list to see valid ids.")
        return

    turns = db.get_turns(character_id)
    print(f"=== {char['name']} ({char['setting_key']}) - character #{character_id} ===")
    print(f"{len(turns)} turn(s) recorded\n")
    if not turns:
        print("Nothing recorded yet - either no turns have been played since this feature shipped, "
              "or all of this character's turns predate it.")
        return

    for t in turns:
        print(f"--- Turn {t['turn_number']} (model: {t['model_key'] or 'unknown'}) ---")
        print(f"Player: {t['player_input']}\n")
        print(t["narration"])
        events = [e for e in json.loads(t["debug_events_json"])]
        lines = [l for l in (_format_debug_event(e) for e in events) if l]
        if lines:
            print("\nMechanics:")
            for l in lines:
                print(f"  - {l}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("character_id", nargs="?", type=int, help="Character id to print the transcript for")
    parser.add_argument("--list", action="store_true", help="List every character across every account")
    args = parser.parse_args()

    if args.list or args.character_id is None:
        _list_characters()
    else:
        _print_transcript(args.character_id)
