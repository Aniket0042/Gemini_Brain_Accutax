"""check_guide_drift.py — detect when the Accutax UI no longer matches the App Guide.

The App Guidance answers (knowledge/accutax_guide.md) and the "Open in
Accutax" buttons (knowledge/guide_loader.SECTION_ROUTES) were verified against
the live app by hand. When the frontend changes — a route renamed, a button
relabelled — the guide silently goes stale. This script pulls the deployed
frontend's JS bundle (public, no login) and checks:

  1. Every SECTION_ROUTES path is still a route the app defines.   (error)
  2. Every **bold** UI label in the guide still appears in the app. (warning)

Usage (from backend/):
    .venv/Scripts/python.exe scripts/ops/check_guide_drift.py
    .venv/Scripts/python.exe scripts/ops/check_guide_drift.py --url https://accutaxai.netlify.app --strict

Exit code 0 = no route drift (label warnings allowed unless --strict),
1 = drift found, 2 = could not fetch the app. Run it in CI or on a schedule
after each frontend deploy.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from gemini_brain.config.settings import settings  # noqa: E402
from gemini_brain.knowledge.guide_loader import SECTION_ROUTES, load_app_guide  # noqa: E402

_BUNDLE_RE = re.compile(r'src="(/assets/index-[^"]+\.js)"')
_ROUTE_DEF_RE = re.compile(r'path:"(/[^"]*)"')
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _fetch(url: str, attempts: int = 3) -> str:
    # The bundle is ~8 MB; a slow host sometimes truncates it (IncompleteRead).
    last_error: Exception = RuntimeError("no attempt made")
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "accutax-guide-drift-check"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_error = e
    raise last_error


def fetch_bundle(app_url: str) -> str:
    base = app_url.rstrip("/")
    html = _fetch(base + "/")
    match = _BUNDLE_RE.search(html)
    if not match:
        raise RuntimeError(f"no main bundle <script> found at {base}/")
    return _fetch(base + match.group(1))


def _route_pattern(route: str) -> re.Pattern:
    return re.compile("^" + re.sub(r":[A-Za-z]+", r"[^/]+", re.escape(route).replace(r"\:", ":")) + "$")


def check_routes(bundle: str) -> list:
    """SECTION_ROUTES paths the app no longer defines."""
    defined = set(_ROUTE_DEF_RE.findall(bundle))
    patterns = [_route_pattern(r) for r in defined if ":" in r]
    missing = []
    for heading, route in sorted(SECTION_ROUTES.items()):
        path = route.split("?", 1)[0]
        if path in defined:
            continue
        if any(p.match(path) for p in patterns):
            # A parameterised route (e.g. /reports/:reportType) matches; the
            # concrete value must still be known to the app somewhere.
            if f'"{path.rsplit("/", 1)[-1]}"' in bundle:
                continue
        missing.append((heading, route))
    return missing


#: Labels the app assembles at runtime ("Create New " + document type,
#: type + " Number"), so they never appear as one literal in the bundle.
#: Verified on screen; each part is checked instead.
_DYNAMIC_LABELS = {
    "Create New Quote": ("Create New", "Quote"),
    "Credit_note Number": ("Number",),
}
_BOX_RANGE_RE = re.compile(r"^\d+[a-z]?\s*[–-]\s*\d+[a-z]?$")


def _labels(guide: str) -> set:
    labels = set()
    for raw in _BOLD_RE.findall(guide):
        text = " ".join(raw.split())
        # Descriptive emphasis ("Important:", "Known gotcha:") is ours, not the app's.
        if text.endswith(":") or len(text) > 60:
            continue
        for part in re.split(r" > | / ", text):
            part = part.strip().rstrip("*").strip()
            if part.startswith("+ "):  # "+" is an icon, not text
                part = part[2:]
            part = re.sub(r"\s*\(N\)$", "", part)  # count placeholder, e.g. "Reconcile (N)"
            if len(part) >= 3 and not _BOX_RANGE_RE.match(part):  # FTA box ranges "1a–1g"
                labels.add(part)
    return labels


def _present(label: str, lowered_bundle: str) -> bool:
    parts = _DYNAMIC_LABELS.get(label, (label,))
    return all(p.lower() in lowered_bundle for p in parts)


def check_labels(bundle: str, guide: str) -> list:
    """Bold guide labels that no longer appear anywhere in the app bundle."""
    lowered = bundle.lower()
    return sorted(label for label in _labels(guide) if not _present(label, lowered))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=settings.accutax_app_url, help="Accutax web app URL")
    parser.add_argument("--strict", action="store_true", help="fail on label drift too")
    args = parser.parse_args()

    try:
        bundle = fetch_bundle(args.url)
    except Exception as e:
        print(f"ERROR: could not fetch app bundle from {args.url}: {e}")
        return 2

    guide = load_app_guide()
    missing_routes = check_routes(bundle)
    missing_labels = check_labels(bundle, guide)

    print(f"Checked {len(SECTION_ROUTES)} routes and {len(_labels(guide))} labels against {args.url}")
    if missing_routes:
        print(f"\nROUTE DRIFT ({len(missing_routes)}) — button would open a page that no longer exists:")
        for heading, route in missing_routes:
            print(f"  {route:40} section: {heading}")
    if missing_labels:
        print(f"\nLABEL DRIFT ({len(missing_labels)}) — guide names a label the app no longer shows:")
        for label in missing_labels:
            print(f"  {label}")
    if not missing_routes and not missing_labels:
        print("No drift.")

    if missing_routes or (args.strict and missing_labels):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
