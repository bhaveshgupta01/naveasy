"""Thin, tested clients for the four external services NavEasy uses.

Keys come from the repo-root .env (see .env.example). Every client retries with
backoff and never runs requests in parallel, so we stay inside rate limits.
"""
from __future__ import annotations

import os
import random
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
UA = {"User-Agent": "naveasy-hackathon-prototype"}


def env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"{name} is not set; copy .env.example to .env and fill it in")
    return val


def with_backoff(fn, retries: int = 5):
    """Call fn() and retry on HTTP 429 / 5xx with 1, 2, 4, 8, 16 s (+ jitter) waits."""
    for attempt in range(retries + 1):
        r = fn()
        if r.status_code not in (429, 500, 502, 503, 504) or attempt == retries:
            return r
        time.sleep(min(2 ** attempt, 30) + random.random())
    return r
