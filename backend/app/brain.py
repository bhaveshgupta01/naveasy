"""The assistant's understanding, on a local open-source language model (through Ollama).

The model does two narrow jobs, and never supplies facts:
  1. parse(): turn what the person said into a command (intent, needs, budget, place, the qualities they want).
  2. answer(): word a reply to a question from sentences we retrieved from the measured data.
Everything the assistant states still comes from the pipeline's numbers. If no local model is running,
dialogue.py falls back to its keyword rules.

Pick the model with NAVEASY_LOCAL_LLM; otherwise the first of PREFERRED that is installed is used.
"""
from __future__ import annotations

import json
import os
import time

import requests

OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434")
PREFERRED = ("qwen2.5:7b", "qwen2.5", "llama3.1", "llama3.2", "mistral")
PRIORITIES = ["safe_streets", "close_to_transit", "close_to_pharmacy", "close_to_hospital", "close_to_clinic", "close_to_grocery", "close_to_park", "close_to_school",
              "close_to_restaurants", "cheap", "new_building", "elevator", "single_story", "bright", "quiet", "spacious"]
SCHEMA = {"type": "object", "properties": {
    "intent": {"type": "string", "enum": ["find_homes", "about_home", "inside_home", "route", "neighborhood", "moving", "question", "next_home", "repeat", "help", "chitchat"]},
    "needs": {"type": "array", "items": {"type": "string", "enum": ["wheelchair", "blind", "deaf", "older"]}},
    "city": {"type": ["string", "null"], "enum": ["houston", "la", None]},
    "max_rent": {"type": ["integer", "null"]},
    "near_place": {"type": ["string", "null"]},
    "priorities": {"type": "array", "items": {"type": "string", "enum": PRIORITIES}},
    "bedrooms": {"type": ["integer", "null"]},
    "which_home": {"type": ["string", "null"]},
    "destination": {"type": ["string", "null"], "enum": ["pharmacy", "clinic", "grocery", "transit", "hospital", "school", "park", "restaurant", None]},
    "topic": {"type": ["string", "null"], "enum": ["layout", "light", "doors", "problems", "size", "paths", None]},
    "from_address": {"type": ["string", "null"]}},
    "required": ["intent", "needs", "city", "max_rent", "near_place", "priorities", "bedrooms", "which_home", "destination", "topic", "from_address"]}
SYSTEM = """You turn what a person says to a housing assistant into a JSON command. The assistant helps people with disabilities find a home in Houston or Los Angeles and understand it by voice.

intent, pick one:
- find_homes: they want homes or apartments found, or change what they are looking for (budget, area, qualities).
- about_home: they want to hear about one of the homes already listed ("the second one", "tell me about Calais").
- inside_home: what a home is like inside: rooms, layout, light, windows, doors, getting around inside.
- route: how to get from the home to a place (pharmacy, bus, grocery...).
- neighborhood: what the area around the current home is like.
- moving: they are relocating from an address they give.
- question: a question about facts already shown (score, rent, age, elevator, a door width, what colors mean, how something is measured).
- next_home, repeat, help.
- chitchat: greetings, thanks, or anything else.

Other fields (use null or [] when not said):
- needs: needs the person states about themselves or who the home is for: wheelchair, blind, deaf, older. Do not guess.
- city: houston or la, only if said.
- max_rent: monthly budget in dollars as a number ("under 1800", "two thousand a month" -> 2000).
- near_place: a NAMED place, landmark, neighborhood or street address to search near (Midtown, the Medical Center, Rice University). Never a quality ("safe", "quiet", "nice") and never a kind of place ("a bus stop", "a hospital").
- priorities: qualities they want, only those they actually express:
  safe_streets (safe, safe area, safe to walk, good crossings), close_to_transit (bus, train, rail, public transport), close_to_pharmacy, close_to_hospital, close_to_clinic (doctor), close_to_grocery (supermarket, shops), close_to_park, close_to_school, close_to_restaurants,
  cheap (cheap, affordable, low rent), new_building (new, modern), elevator, single_story (no stairs, one level, ground floor), bright (sunny, lots of light, big windows), quiet, spacious (big, roomy).
- bedrooms: number of bedrooms asked for.
- which_home: "1", "2", "3"... for first, second, third, or the home's name, when they point at a listed home. "it", "that one", "there" with a current home -> null (the current home is used).
- destination: for route: pharmacy, clinic, grocery, transit, hospital, school, park, restaurant.
- topic: for inside_home: layout, light, doors, problems, size, paths.
- from_address: for moving: the address they are leaving.
Return only the JSON."""

_cache = {"at": 0.0, "model": None}


def model() -> str | None:
    """The local model to use, or None if Ollama isn't running or has no suitable model."""
    if os.getenv("NAVEASY_LOCAL_LLM", "").lower() in ("off", "none", "0"):
        return None
    if time.time() - _cache["at"] < 30:
        return _cache["model"]
    found = None
    try:
        names = [m["name"] for m in requests.get(f"{OLLAMA}/api/tags", timeout=2).json().get("models", [])]
        want = os.getenv("NAVEASY_LOCAL_LLM")
        if want:
            found = next((n for n in names if n == want or n.split(":")[0] == want), None)
        else:
            found = next((n for p in PREFERRED for n in names if n == p or n.split(":")[0] == p), None)
    except Exception:  # noqa: BLE001
        found = None
    _cache.update(at=time.time(), model=found)
    return found


def _chat(messages: list[dict], fmt=None, max_tokens: int = 220, timeout: int = 40) -> str | None:
    m = model()
    if not m:
        return None
    try:
        body = {"model": m, "stream": False, "keep_alive": "60m", "messages": messages, "options": {"temperature": 0, "num_predict": max_tokens}}
        if fmt:
            body["format"] = fmt
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=timeout)
        return (r.json().get("message") or {}).get("content")
    except Exception:  # noqa: BLE001
        return None


def parse(text: str, situation: str = "") -> dict | None:
    """What the person means, as a command. None if no model is available or it returned nonsense."""
    out = _chat([{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": (f"Situation: {situation}\n" if situation else "") + f"Person says: {text}"}], fmt=SCHEMA)
    if not out:
        return None
    try:
        cmd = json.loads(out)
    except ValueError:
        return None
    if not isinstance(cmd, dict) or cmd.get("intent") not in SCHEMA["properties"]["intent"]["enum"]:
        return None
    cmd["needs"] = [n for n in cmd.get("needs") or [] if n in ("wheelchair", "blind", "deaf", "older")]
    cmd["priorities"] = list(dict.fromkeys(p for p in cmd.get("priorities") or [] if p in PRIORITIES))
    return cmd


def answer(question: str, facts: list[str], situation: str = "") -> str | None:
    """A short spoken answer worded from the given facts only. None if the facts don't answer it."""
    if not facts:
        return None
    out = _chat([{"role": "system", "content":
                  "You are NavEasy, a voice assistant that helps people with disabilities choose a home. Answer the question using ONLY the facts given. "
                  "If the facts do not answer it, reply with exactly: UNKNOWN. Speak plainly in one to three short sentences. No lists, no markdown, "
                  "no advice beyond the facts, and never call a home accessible or compliant. Say numbers with their units. "
                  "Do not draw conclusions the facts don't state: if asked whether something will work, give the measurement that bears on it "
                  "and say what it means against the stated limit, rather than a bare yes or no."},
                 {"role": "user", "content": (f"Situation: {situation}\n" if situation else "") + "Facts:\n- " + "\n- ".join(facts) + f"\n\nQuestion: {question}"}], max_tokens=160)
    if not out or "UNKNOWN" in out.upper()[:24]:
        return None
    return out.strip().strip('"')


def warm() -> None:
    import threading
    threading.Thread(target=lambda: parse("hello"), daemon=True).start()
