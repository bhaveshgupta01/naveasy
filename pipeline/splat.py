"""Aerial Gaussian splats (drone-photo 3D models of the outside of 232 homes) from the Matterport hackathon site.

    python -m pipeline.splat <sid> [radius in meters]

The full file is about 550 MB (8 M Gaussians, standard INRIA .ply, 68 bytes each, Z up, roughly meters).
The drone orbits the home, so the scene covers the whole neighborhood but is dense only at the home.
We download the file once, keep the home and its lot, and cache data/splats/<sid>_<radius>.ply for the
web viewer. Signed links last an hour: store the file, not the link.
"""
from __future__ import annotations

import csv
import math
import sys
from functools import lru_cache

import numpy as np
import requests

from .clients import ROOT, UA

SITE = "https://beta-app.matterport.com/2026-princeton-hackathon"
OUT = ROOT / "data" / "splats"


@lru_cache(maxsize=1)
def splat_ids() -> set[str]:
    path = OUT / "models_index.csv"
    if not path.exists():
        return set()
    with open(path) as f:
        return {r["sid"] for r in csv.DictReader(f) if r.get("splat") == "yes"}


def fetch(sid: str, radius_m: int = 45, max_n: int = 2_500_000) -> "tuple[object, int]":
    """The home and its lot only: download the whole splat once, keep the Gaussians within radius_m of the
    densest spot (the drone orbits the home, so that is the home), and cache the crop."""
    path = OUT / f"{sid}_{radius_m}.ply"
    if path.exists():
        return path, 0
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / f"{sid}.full.tmp"
    with requests.get(f"{SITE}/resources/{sid}/splat/splat.ply", headers=UA, timeout=900, stream=True) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 22):
                f.write(chunk)
    with open(tmp, "rb") as f:
        head = f.read(4096)
    end = head.index(b"end_header\n") + len(b"end_header\n")
    header = head[:end].decode()
    props = [ln.split()[-1] for ln in header.splitlines() if ln.startswith("property float")]
    total = int(next(ln.split()[-1] for ln in header.splitlines() if ln.startswith("element vertex")))
    g = np.memmap(tmp, dtype="<f4", mode="r", offset=end, shape=(total, len(props)))
    xyz = np.asarray(g[:, :3])
    center = np.median(xyz, axis=0)
    for _ in range(3):      # tighten onto the dense core
        near = np.linalg.norm(xyz[:, :2] - center[:2], axis=1) < 80
        center = np.median(xyz[near], axis=0)
    keep = np.nonzero((np.linalg.norm(xyz[:, :2] - center[:2], axis=1) < radius_m) & (np.abs(xyz[:, 2] - center[2]) < 40))[0]
    frac = 1.0
    if len(keep) > max_n:
        frac = max_n / len(keep)
        keep = np.sort(np.random.default_rng(0).choice(keep, max_n, replace=False))
    out = np.array(g[keep])
    out[:, :3] -= center            # put the home at the origin
    if frac < 1:                    # thinned: grow the survivors so surfaces stay closed (scales are logs)
        for i, p_ in enumerate(props):
            if p_.startswith("scale_"):
                out[:, i] += 0.5 * math.log(1 / frac)
    with open(path, "wb") as f:
        f.write(header.replace(f"element vertex {total}", f"element vertex {len(out)}").encode())
        f.write(out.astype("<f4").tobytes())
    del g
    tmp.unlink()
    return path, len(out)


def preview(sid: str, radius_m: int = 45) -> None:
    """A quick top-down and side picture of the Gaussian centers, to check the data without a GPU."""
    from PIL import Image
    path, _ = fetch(sid, radius_m)
    raw = path.read_bytes()
    end = raw.index(b"end_header\n") + 11
    props = [ln.split()[-1] for ln in raw[:end].decode().splitlines() if ln.startswith("property float")]
    g = np.frombuffer(raw, dtype="<f4", offset=end).reshape(-1, len(props))
    col = {p: i for i, p in enumerate(props)}
    xyz = g[:, [col["x"], col["y"], col["z"]]]
    rgb = np.clip(0.5 + 0.28209479 * g[:, [col["f_dc_0"], col["f_dc_1"], col["f_dc_2"]]], 0, 1)
    alpha = 1 / (1 + np.exp(-g[:, col["opacity"]]))
    keep = alpha > 0.3
    xyz, rgb = xyz[keep], (rgb[keep] * 255).astype(np.uint8)
    lo, hi = np.percentile(xyz, 0.5, axis=0), np.percentile(xyz, 99.5, axis=0)
    print("gaussians", len(g), "bounds 2-98%:", lo.round(1), hi.round(1))
    for name, (a, b, depth) in {"top": (0, 1, 2), "side": (0, 2, 1)}.items():
        s = 900 / max(hi[a] - lo[a], hi[b] - lo[b])
        W, H = int((hi[a] - lo[a]) * s) + 1, int((hi[b] - lo[b]) * s) + 1
        u = ((xyz[:, a] - lo[a]) * s).astype(int)
        v = ((hi[b] - xyz[:, b]) * s).astype(int)
        ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
        order = np.argsort(xyz[ok, depth] if name == "top" else -xyz[ok, depth])
        img = np.full((H, W, 3), 255, np.uint8)
        img[v[ok][order], u[ok][order]] = rgb[ok][order]
        Image.fromarray(img).save(OUT / f"{sid}_{name}.png")


if __name__ == "__main__":
    sid = sys.argv[1]
    rad = int(sys.argv[2]) if len(sys.argv) > 2 else 45
    p, n = fetch(sid, rad)
    print(p, n)
    preview(sid, rad)
