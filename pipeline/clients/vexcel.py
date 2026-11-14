"""Vexcel REST API v2. Tested Nov 13-14 in Houston.

Conventions that cost us time:
- WKT is LONGITUDE first: POINT(-95.3795 29.7437).
- Images (ortho/extract, oriented/extract) come back as bytes, not JSON.
- Obliques exist only in the "urban" layer; check oriented_collections() first.
- Back off on 429; never call in parallel.
"""
from __future__ import annotations

import math

import requests

from . import UA, env, with_backoff

API = "https://api.vexcelgroup.com/v2"
LAYERS = "urban,urban-r,wide-area,urban-g"


def _headers() -> dict:
    return {"X-API-KEY": env("VEXCEL_API_KEY"), **UA}


def point(lat: float, lon: float) -> str:
    return f"POINT({lon} {lat})"


def square(lat: float, lon: float, half_m: float) -> str:
    dlat = half_m / 111_320
    dlon = half_m / (111_320 * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def _get(path: str, **params):
    r = with_backoff(lambda: requests.get(f"{API}/{path}", params=params, headers=_headers(), timeout=120))
    return r


def _post(path: str, body: dict):
    return with_backoff(lambda: requests.post(f"{API}/{path}", json=body, headers=_headers(), timeout=300))


def property_extract(lat: float, lon: float) -> dict | None:
    """Parcel polygon + ~75 parcel attributes + per-structure attributes. None if no parcel here."""
    r = _get("property/extract", wkt=point(lat, lon), srid=4326, layer=LAYERS, latest="true",
             include="precomputed", **{"unit-system": "imperial"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def oriented_collections(wkt: str) -> list[dict]:
    r = _get("oriented/collections", wkt=wkt, srid=4326, layer="urban", **{"product-type": "oblique",
             "include": "product-type,collection,last-capture-date"})
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return r.json()["features"]


def ortho_image(wkt: str, buffer_m: float = 10, size: str = "800,800", drawing: list | None = None) -> bytes:
    body = {"wkt": wkt, "srid": 4326, "layer": LAYERS, "buffer-meter": buffer_m, "crop": "bounding_box",
            "image-format": "jpeg", "destination-dimensions": size}
    if drawing:
        body["drawing"] = drawing
    r = _post("ortho/extract", body)
    r.raise_for_status()
    return r.content


def oblique_image(wkt: str, direction: str = "oblique-north", buffer_m: float = 10) -> bytes:
    r = _post("oriented/extract", {"wkt": wkt, "srid": 4326, "layer": "urban", "product-type": direction,
                                   "rotation": "natural", "buffer-meter": buffer_m, "crop": "bbox",
                                   "image-format": "jpeg"})
    r.raise_for_status()
    return r.content


def detect(wkt: str, labels: list[str], threshold: float = 0.3, oblique: str | None = None) -> dict:
    """Open-vocabulary detection (SAM3). Found crosswalks reliably; sidewalks and curb ramps under trees, not."""
    images = [{"endpoint": "ortho/extract",
               "request": {"layer": LAYERS, "srid": 4326, "wkt": wkt, "crop": "bounding_box"}}]
    if oblique:
        images.append({"endpoint": "oriented/extract",
                       "request": {"layer": "urban", "srid": 4326, "wkt": wkt, "product-type": oblique,
                                   "rotation": "natural", "crop": "bbox"}})
    r = _post("elements/detect", {"wkt": wkt, "srid": 4326, "buffer-meter": 0, "images": images,
                                  "model-input": {"elements": [{"label": l, "model": "sam3",
                                                                "detection-threshold": threshold} for l in labels],
                                                  "include": ["bbox", "confidence"]}})
    r.raise_for_status()
    return r.json()


def embedding_search(area_wkt: str, text: str | None = None, location_wkt: str | None = None, k: int = 10) -> list[dict]:
    """Semantic search over tiles. Exactly one of text / location. Cross-city look-alikes work (LA -> Houston 0.95)."""
    body = {"wkt": area_wkt, "srid": 4326, "include": "score,geometry,collection", "total-records": k, "page-size": k}
    if text:
        body["search-text"] = text
    else:
        body["search-location"] = location_wkt
    r = _post("embedding/extract", body)
    r.raise_for_status()
    return r.json()["features"]
