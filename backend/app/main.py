"""NavEasy API.

Today every endpoint serves the SYNTHETIC files in frontend/public/mock, so the
interface can be built in parallel. Each endpoint has the exact shape described
in docs/API_CONTRACT.md; as the pipeline lands, swap the mock read for the real
query without changing the response shape.

Run from the repo root:
    . .venv/bin/activate
    uvicorn backend.app.main:app --reload --port 8000
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "frontend" / "public" / "mock"
load_dotenv(ROOT / ".env")

PROFILES = ("wheelchair", "blind", "deaf", "older")
Profile = Literal["wheelchair", "blind", "deaf", "older"]

app = FastAPI(title="NavEasy API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _mock(name: str):
    path = MOCK / name
    if not path.exists():
        raise HTTPException(404, f"mock file {name} not found; run python scripts/make_mock.py")
    return json.loads(path.read_text())


def _profiles(raw: str) -> list[str]:
    ps = [p.strip() for p in raw.split(",") if p.strip()]
    bad = [p for p in ps if p not in PROFILES]
    if bad or not ps:
        raise HTTPException(422, f"profiles must be a comma list of {PROFILES}")
    return ps


def _combine(scores: dict, profiles: list[str], how: str) -> dict:
    vals = [scores[p]["score"] for p in profiles]
    order = {"low": 0, "medium": 1, "high": 2}
    confs = sorted((scores[p]["confidence"] for p in profiles), key=order.get)
    score = min(vals) if how == "min" else round(sum(vals) / len(vals))
    return {"score": score, "confidence": confs[0]}


@app.get("/health")
def health():
    return {"ok": True, "mode": "mock"}


@app.get("/map/buildings")
def map_buildings(profiles: str = "wheelchair", combine: Literal["min", "avg"] = "min",
                  passing_only: bool = False, min_score: int = 60):
    ps = _profiles(profiles)
    data = _mock("buildings.json")
    out = []
    for b in data["buildings"]:
        b = {**b, "combined": _combine(b["scores"], ps, combine)}
        if passing_only and b["combined"]["score"] < min_score:
            continue
        out.append(b)
    return {"city": data["city"], "profiles": ps, "combine": combine, "buildings": out}


@app.get("/map/hexes")
def map_hexes(profiles: str = "wheelchair", combine: Literal["min", "avg"] = "min"):
    ps = _profiles(profiles)
    data = _mock("hexes.json")
    cells = [{**c, "combined": _combine(c["scores"], ps, combine)} for c in data["cells"]]
    return {"city": data["city"], "res": data["res"], "profiles": ps, "cells": cells}


@app.get("/buildings/{building_id}")
def building(building_id: str):
    for b in _mock("buildings.json")["buildings"]:
        if b["id"] == building_id:
            return {
                **b,
                "components": {"vertical_access": 0.8, "approach": 0.6, "listing_evidence": 0.3, "age": 0.7},
                "units": ["u_sample_001"],
                "nearest": [
                    {"category": "pharmacy", "name": "Sample Pharmacy", "minutes": {"wheelchair": 19.8}},
                    {"category": "clinic", "name": "Sample Clinic", "minutes": {"wheelchair": 12.0}},
                ],
            }
    raise HTTPException(404, "building not found")


@app.get("/units/{model_id}/heatmap")
def unit_heatmap(model_id: str, profile: Profile = "wheelchair", furnished: bool = True):
    unit = _mock(f"unit_{model_id}.json")
    heat = unit["heat"].get(profile) or unit["heat"]["wheelchair"]
    unit = {**unit, "heat": heat["furnished" if furnished else "empty"], "profile": profile, "furnished": furnished}
    return unit


@app.get("/routes")
def routes(building_id: str = "b000", category: str = "pharmacy", profile: Profile = "wheelchair"):
    return _mock("route_b000_pharmacy_wheelchair.json")


@app.get("/furnish/score")
def furnish_score(model_id: str = "u_sample_001", profiles: str = "wheelchair"):
    return _mock(f"furnish_score_{model_id}.json")


class FurnishRequest(BaseModel):
    target_model_id: str
    profiles: list[Profile] = ["wheelchair"]
    inventory: list[dict] = []
    preferences: dict = {}


@app.post("/furnish/plan")
def furnish_plan(req: FurnishRequest):
    return _mock(f"furnish_plan_{req.target_model_id}.json")


class RelocateRequest(BaseModel):
    origin: dict
    profiles: list[Profile] = ["wheelchair"]
    must_haves: list[dict] = []
    budget: int | None = None


@app.post("/relocate")
def relocate(req: RelocateRequest):
    return _mock("relocate_sample.json")


class ChatRequest(BaseModel):
    messages: list[dict]
    session_id: str | None = None


@app.post("/agent/chat")
def agent_chat(req: ChatRequest):
    return _mock("agent_reply_sample.json")


@app.post("/voice/token")
def voice_token():
    """Mint a short-lived Gemini Live token for the browser.

    TODO: call the ephemeral-token endpoint (see docs/VOICE.md). The API key never
    leaves the server; the browser only ever sees the short-lived token.
    """
    if not os.getenv("GEMINI_API_KEY"):
        raise HTTPException(503, "GEMINI_API_KEY is not set in .env")
    raise HTTPException(501, "ephemeral token minting not implemented yet; see docs/VOICE.md")
