"""Matterport Model API (GraphQL). Tested Nov 13-14 on homes, apartment units and an office suite.

- Units are meters. Floors carry the wall graph: vertices (2D points), edges (walls)
  and openings on each edge (doorway, opening, window, divider) with width, height,
  lowerElevation (threshold) and relativeCenter (0..1 along the edge).
- objectAnnotations is empty in every model we tried; detect furniture from panoramas.
- The furniture-removed panoramas live in a separate view of the model:
  model { view(type: "matterport.model.defurnished") { id } } -> query that id as a model.
  Present on homes and apartment units, absent on office suites.
- Image URLs are signed and expire: download and store images, not links.
"""
from __future__ import annotations

import requests

from . import UA, env, with_backoff

URL = "https://api.matterport.com/api/models/graph"
DEFURNISHED = "matterport.model.defurnished"

GEOMETRY_QUERY = """
query($id: ID!) {
  model(id: $id) {
    id name created
    geocoordinates { latitude longitude }
    defurnished: view(type: "matterport.model.defurnished") { id }
    panoLocations { id position { x y z } }
    floors {
      id label sequence
      vertices { id position { x y } }
      edges { id type thickness vertices { id }
              openings { type label width height lowerElevation relativeCenter } }
      rooms { id label classifications { label confidence }
              boundary { edges { id } }
              panoLocations { id }
              dimensions: dimensionEstimates(units: metric) { areaFloor: area width depth height lowerElevation } }
    }
  }
}"""

PANOS_QUERY = """
query($id: ID!, $res: String!) {
  model(id: $id) {
    panoLocations { id label position { x y z } rotation { x y z w }
                    skybox(resolution: $res) { status children } }
  }
}"""


def gql(query: str, variables: dict | None = None) -> dict:
    auth = (env("MATTERPORT_API_KEY"), env("MATTERPORT_API_TOKEN"))
    r = with_backoff(lambda: requests.post(URL, json={"query": query, "variables": variables or {}},
                                           auth=auth, headers=UA, timeout=90))
    r.raise_for_status()
    body = r.json()
    if body.get("errors") and not body.get("data"):
        raise RuntimeError(body["errors"][0].get("message"))
    return body


def geometry(model_id: str, cache: bool = True) -> dict | None:
    """Floors with wall graph, openings and rooms (meters). Cached in data/matterport/<id>.json."""
    import json
    from . import ROOT
    path = ROOT / "data" / "matterport" / f"{model_id}.json"
    if cache and path.exists():
        return json.loads(path.read_text())
    model = gql(GEOMETRY_QUERY, {"id": model_id})["data"]["model"]
    if cache:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model))
    return model


def defurnished_view_id(model_id: str) -> str | None:
    body = gql('query($id: ID!){ model(id:$id){ view(type: "%s"){ id } } }' % DEFURNISHED, {"id": model_id})
    view = ((body.get("data") or {}).get("model") or {}).get("view")
    return view["id"] if view else None


def panoramas(model_or_view_id: str, resolution: str = "2k") -> list[dict]:
    """Scan positions with cube-face image URLs (6 faces: 0 up, 1-4 around, 5 down)."""
    return gql(PANOS_QUERY, {"id": model_or_view_id, "res": resolution})["data"]["model"]["panoLocations"]
