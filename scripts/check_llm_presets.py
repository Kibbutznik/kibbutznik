#!/usr/bin/env python3
"""Validate every OpenRouter preset in LLM_PRESETS against the live catalog.

Why this exists: OpenRouter retires model ids without warning. A dead id in
LLM_PRESETS fails only at *switch* time (a 404 on every agent turn), which is
a miserable way to find out — it's bitten this project twice already
(`mistral-small-creative`, then three presets at once in the 2026-06 audit).

Run it whenever you touch the presets, and before a launch:

    python scripts/check_llm_presets.py

Exits non-zero if any preset is dead, so it can gate CI if wanted. Network
call to the public catalog only — no API key needed.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.request

CATALOG = "https://openrouter.ai/api/v1/models"
SRC = pathlib.Path(__file__).resolve().parent.parent / "agents" / "simulation_api.py"


def main() -> int:
    src = SRC.read_text()
    try:
        block = src.split("LLM_PRESETS = {")[1].split("\n}")[0]
    except IndexError:
        print("!! could not find LLM_PRESETS in", SRC)
        return 2

    presets = re.findall(
        r'"(?P<key>[^"]+)":\s*\{"backend":\s*"openrouter",\s*"model":\s*"(?P<model>[^"]+)"',
        block,
    )
    if not presets:
        print("!! no OpenRouter presets found — nothing to check")
        return 0

    try:
        data = json.load(urllib.request.urlopen(CATALOG, timeout=30))["data"]
    except Exception as e:  # network/catalog problem is not a preset failure
        print(f"!! could not reach the OpenRouter catalog ({e}) — skipping check")
        return 0

    live = {m["id"]: m for m in data}
    dead = []
    print(f"Checking {len(presets)} OpenRouter preset(s) against the live catalog\n")
    for key, model in presets:
        m = live.get(model)
        if m is None:
            dead.append((key, model))
            print(f"  DEAD  {key:<22} {model}")
            continue
        p = m.get("pricing", {}) or {}
        pin = float(p.get("prompt") or 0) * 1e6
        pout = float(p.get("completion") or 0) * 1e6
        print(f"  ok    {key:<22} {model:<45} ${pin:.3f}/${pout:.3f} per M")

    if dead:
        print(f"\n{len(dead)} preset(s) point at retired model ids — they will 404 on use:")
        for key, model in dead:
            print(f"  - {key}: {model}")
        print("Find a live replacement at https://openrouter.ai/models")
        return 1

    print("\nAll OpenRouter presets are live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
