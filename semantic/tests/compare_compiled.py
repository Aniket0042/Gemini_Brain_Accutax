"""Prove that a Cube model change leaves what a Cube serves unchanged.

Compares two running Cube APIs that share CUBEJS_API_SECRET (for example the live
VM Cube and a throwaway container with the changed model):

  * /meta: every view, member, type, title and description the agent sees.
  * /sql:  the SQL compiled for every view member on its own, and for each view's
           measures and dimensions together.

SQL is compared with whitespace collapsed, since Jinja leaves blank lines that
Postgres ignores. Exit code 0 means identical, 1 means a difference (printed).

Run on the VM with the AI venv (PyJWT is installed there):
  CUBE_SECRET=... python compare_compiled.py http://127.0.0.1:4000 http://127.0.0.1:4096
Optional: --org 24 (organization in the token), --iss / --aud (default to the VM values).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

import jwt


def token(secret: str, org: int, iss: str, aud: str) -> str:
    now = int(time.time())
    claims = {"organization_ids": [org], "sub": "compare-compiled", "iss": iss, "aud": aud, "iat": now, "exp": now + 110}
    return jwt.encode(claims, secret, algorithm="HS256")


def get(base: str, path: str, auth: str, query: dict | None = None) -> dict:
    url = base.rstrip("/") + path
    if query is not None:
        url += "?" + urllib.parse.urlencode({"query": json.dumps(query)})
    req = urllib.request.Request(url, headers={"Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}


def normalize_sql(payload: dict) -> str:
    if "error" in payload:
        return "ERROR " + str(payload["error"])
    text, params = payload["sql"]["sql"]
    return re.sub(r"\s+", " ", text).strip() + " | params=" + json.dumps(params)


def queries(meta: dict) -> list[tuple[str, dict]]:
    out = []
    for view in sorted(meta.get("cubes", []), key=lambda c: c["name"]):
        if view.get("type") != "view":
            continue
        measures = sorted(m["name"] for m in view.get("measures", []))
        dimensions = sorted(d["name"] for d in view.get("dimensions", []) if d.get("type") != "time")
        times = sorted(d["name"] for d in view.get("dimensions", []) if d.get("type") == "time")
        for m in measures:
            out.append((m, {"measures": [m]}))
        for d in dimensions:
            out.append((d, {"dimensions": [d]}))
        for t in times:
            out.append((t, {"measures": measures[:1], "timeDimensions": [{"dimension": t, "granularity": "month"}]}))
        if measures:
            out.append((view["name"] + " (all)", {"measures": measures, "dimensions": dimensions[:5]}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--org", type=int, default=24)
    ap.add_argument("--iss", default="gemini-brain")
    ap.add_argument("--aud", default="accutax-cube")
    args = ap.parse_args()
    # strip(): a value cut from an env file written on Windows ends in \r.
    secret = os.environ.get("CUBE_SECRET", "").strip()
    if len(secret) < 32:
        print("Set CUBE_SECRET to the CUBEJS_API_SECRET both Cubes use.", file=sys.stderr)
        return 2

    def auth() -> str:
        return token(secret, args.org, args.iss, args.aud)

    meta_a = get(args.before, "/cubejs-api/v1/meta", auth())
    meta_b = get(args.after, "/cubejs-api/v1/meta", auth())
    for name, m in (("before", meta_a), ("after", meta_b)):
        if "error" in m:
            print(f"/meta failed on {name}: {m['error']}", file=sys.stderr)
            return 2

    failures = 0
    if json.dumps(meta_a, sort_keys=True) != json.dumps(meta_b, sort_keys=True):
        failures += 1
        print("DIFF /meta: the member catalog changed")

    checked = 0
    for label, q in queries(meta_a):
        a = normalize_sql(get(args.before, "/cubejs-api/v1/sql", auth(), q))
        b = normalize_sql(get(args.after, "/cubejs-api/v1/sql", auth(), q))
        checked += 1
        if a != b:
            failures += 1
            print(f"DIFF {label}\n  before: {a[:400]}\n  after:  {b[:400]}")
        elif a.startswith("ERROR"):
            failures += 1
            print(f"ERROR on both for {label}: {a[:300]}")

    views = sum(1 for c in meta_a.get("cubes", []) if c.get("type") == "view")
    print(f"{views} views, {checked} SQL queries compared, /meta compared: {failures} difference(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
