#!/usr/bin/env python3
"""
CLI entry point for the Butterfly AI-narrated historical RPG engine. The webapp (server.py) is the
primary way to play now - this is kept working for terminal use / scripting, sharing all its actual
logic with the webapp via engine.py and providers.py rather than duplicating it. This CLI always
plays the chosen setting's premade signature character to a single legacy save file - use the
webapp for multiple characters, custom character creation, or accounts.

Setup:
  pip install anthropic openai python-dotenv flask requests
  export ANTHROPIC_API_KEY=...     # needed for sonnet/haiku/opus - or put it in ai_rpg/.env, see .env.example
  export OPENAI_API_KEY=...        # needed for luna (the default) - or put it in ai_rpg/.env
  python loop.py                              # starts/resumes Petrograd 1917 with the default model
  python loop.py haiku                        # force a specific model - see providers.MODEL_REGISTRY for keys
  AI_RPG_MODEL=sonnet python loop.py           # same override via env var instead of argv
  AI_RPG_SETTING=rome_59_bce python loop.py    # play a different setting's premade character
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

import engine
import providers


def resolve_model_key():
    candidate = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AI_RPG_MODEL")
    return candidate.lower() if candidate else providers.DEFAULT_MODEL_KEY


def resolve_setting_key():
    candidate = os.environ.get("AI_RPG_SETTING")
    return candidate.lower() if candidate else engine.DEFAULT_SETTING_KEY


def print_debug_events(debug_events):
    for ev in debug_events:
        if ev["type"] == "check":
            skill_part = f" / {ev['skill']} ({ev['tier']})" if ev.get("skill") else ""
            target = f" (target: {ev['target_npc']})" if ev.get("target_npc") else ""
            mod = f"+{ev['modifier']}" if ev["modifier"] >= 0 else str(ev["modifier"])
            print(
                f"  [CHECK] {ev['attribute']}{skill_part} vs DC {ev['dc']}: rolled {ev['roll']} {mod} "
                f"-> total {ev['total']} = {ev['degree'].upper()}{target}"
            )
        elif ev["type"] in ("state_update", "level_up"):
            label = "LEVEL-UP CHOICE" if ev["type"] == "level_up" else "STATE UPDATE"
            print(f"  [{label}] {ev.get('reasoning', '')}")
            for c in ev.get("changes", []):
                kind = c["kind"]
                if kind == "resource":
                    note = " (clamped)" if c["clamped"] else ""
                    print(f"    {c['key']}: {c['before']} ({c['before_label']}) -> {c['after']} ({c['after_label']}){note}")
                elif kind == "relationship":
                    note = " (clamped)" if c["clamped"] else ""
                    print(f"    {c['key']} trust: {c['before']} -> {c['after']}{note}")
                elif kind in ("enmity", "enmity_auto"):
                    cause = f" ({c['cause']})" if c.get("cause") else ""
                    print(f"    {c['key']} enmity: {c['before']} -> {c['after']} pips{cause}")
                elif kind == "flag":
                    print(f"    flag {c['key']} = {c['value']}")
                elif kind == "commitment":
                    print(f"    commitment '{c['key']}' set: {c['value'].get('status')}")
                elif kind == "liability_gained":
                    print(f"    liability gained: {c['name']}")
                elif kind == "liability_removed":
                    print(f"    liability removed: {c['id']}")
                elif kind == "liability_rejected":
                    print(f"    liability rejected ({c['id']}): {c['reason']}")
                elif kind == "xp":
                    extra = f" (requested {c['requested']}, clamped)" if c["applied"] != c["requested"] else ""
                    print(f"    +{c['applied']} XP{extra}")
                elif kind == "level_up":
                    print(f"    LEVEL UP -> {c['new_level']}")
                elif kind == "skill_upgrade":
                    print(f"    {c['skill']}: {c['before']} -> {c['after']}")
                elif kind == "skill_upgrade_rejected":
                    print(f"    skill upgrade rejected ({c['skill']}): {c['reason']}")
                elif kind == "feat_gained":
                    print(f"    feat gained: {c['name']}")
                elif kind == "feat_rejected":
                    print(f"    feat rejected ({c['id']}): {c['reason']}")
                elif kind == "attribute_improvement":
                    print(f"    {c['attribute']}: {c['before']} -> {c['after']}")
                elif kind == "reinforcement_bonus":
                    print(f"    reinforcement bonus: next level {round(c['discount'] * 100)}% cheaper")
                elif kind == "level_up_rejected":
                    print(f"    level-up rejected: {c['reason']}")
                elif kind == "canon_event":
                    print(f"    canon event fired: {c['key']}")
                elif kind == "divergence":
                    print(f"    divergence logged: {c['note']}")
                elif kind == "ignored_unknown_category":
                    print(f"    ignored unknown category: {c['key']}")
                else:
                    print(f"    {c}")


def main():
    model_key = resolve_model_key()
    setting_key = resolve_setting_key()
    if model_key not in providers.MODEL_REGISTRY:
        print(f"Unknown model key: {model_key}. Options: {', '.join(providers.MODEL_REGISTRY)}", file=sys.stderr)
        sys.exit(1)
    if setting_key not in engine.SETTING_REGISTRY:
        print(f"Unknown setting key: {setting_key}. Options: {', '.join(engine.SETTING_REGISTRY)}", file=sys.stderr)
        sys.exit(1)
    system_key = engine.SETTING_REGISTRY[setting_key]["system"]

    try:
        provider, model = providers.get_provider(model_key)
    except providers.ProviderError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if engine.SAVE_FILE.exists():
        state = engine.load_state()
        is_new = False
    else:
        state = engine.load_premade_character_state(setting_key)
        is_new = state.pop("_is_new", False)
    conversation = provider.new_conversation()

    setting_label = engine.SETTING_REGISTRY[setting_key]["label"]
    print(f"=== Butterfly: {setting_label} === (model: {providers.MODEL_REGISTRY[model_key]['label']})\n")
    if is_new:
        opening = engine.load_text(setting_key, "opening_scene.md")
        narration, debug_events = engine.run_turn(
            provider, model, state, setting_key, system_key, conversation,
            f"[SYSTEM: begin the campaign]\n\n{opening}",
        )
        print_debug_events(debug_events)
        print(narration)
    else:
        print(f"Resuming at turn {state['turn_number']}, {state['date']}. Type your action.")

    engine.save_state(state)

    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit"}:
            break

        try:
            narration, debug_events = engine.run_turn(
                provider, model, state, setting_key, system_key, conversation, user_input
            )
        except Exception as e:
            print(f"\n[Error: {type(e).__name__}: {e}]")
            continue

        print_debug_events(debug_events)
        print(f"\n{narration}")
        engine.save_state(state)


if __name__ == "__main__":
    main()
