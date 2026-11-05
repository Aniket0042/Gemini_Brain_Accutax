"""Local backend for trying the document upload UI by hand (docs/DOCUMENT_UPLOAD_PLAN.md).

    python scripts/dev/upload_demo_server.py          # serves http://127.0.0.1:8000, then run the frontend (npm run dev)

The real app, with a fake login (any email and password) and chat sessions kept in memory, because the shared
database and Cube cannot be reached from a laptop. Real: uploads (checks, store, reader on Bedrock) and the agent
on Bedrock, so it needs AWS credentials. The agent has no figure tools (no Cube): it cannot check a bill against the
books. Uploads are on, and are stored in a temporary folder that is new on every start. Never deploy this.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]   # the Gemini_Brain_Accutax folder
sys.path[:0] = [str(ROOT / "backend" / "src"), str(ROOT / "backend")]

from gemini_brain.config.settings import settings  # noqa: E402

settings.uploads_enabled = True
settings.upload_dir = tempfile.mkdtemp(prefix="uploads-dev-")
settings.vat_kb_enabled = False

from fastapi.routing import APIRoute  # noqa: E402
from gemini_brain.api import auth, routes  # noqa: E402
from gemini_brain.api.app import app  # noqa: E402
from gemini_brain.memory import session_memory as sm  # noqa: E402

USER = {"id": 1, "email": "dev@accutax.test"}
ORGS = [int(o) for o in os.environ.get("DEMO_ORGS", "24,25").split(",")]

SESSIONS, MESSAGES, STATES = {}, {}, {}


def ensure_session(session_id, user_id, organization_id=None, organization_ids=None, db_name="", **kw):
    ids = organization_ids or ([organization_id] if organization_id is not None else [])
    SESSIONS.setdefault(session_id, {"id": session_id, "user_id": int(user_id), "organization_id": ids[0] if len(ids) == 1 else None,
                                     "organization_ids": sorted(ids), "name": "New Chat", "created_at": None, "updated_at": None})
    return True


def get_session_record(session_id, db_name=""):
    rec = SESSIONS.get(session_id)
    return {**rec, "conversation_state": STATES.get(session_id, {})} if rec else None


def save_message(session_id, role, content, db_name="", blocks=None):
    MESSAGES.setdefault(session_id, []).append({"role": role, "content": content, "created_at": None, "blocks": blocks})


def last_assistant_blocks(session_id, blocks, db_name=""):
    for m in reversed(MESSAGES.get(session_id, [])):
        if m["role"] == "assistant":
            m["blocks"] = blocks
            return


patches = {
    "ensure_session": ensure_session,
    "get_session_record": get_session_record,
    "save_message_by_session": save_message,
    "update_last_assistant_blocks": last_assistant_blocks,
    "get_history_by_session": lambda sid, limit=6, db_name="": [{"role": m["role"], "content": m["content"]} for m in MESSAGES.get(sid, [])],
    "get_transcript_by_session": lambda sid, limit=50, db_name="": list(MESSAGES.get(sid, [])),
    "get_state_by_session": lambda sid, db_name="": dict(STATES.get(sid, {})),
    "update_state_by_session": lambda sid, state, db_name="": STATES.__setitem__(sid, dict(state)),
    "verify_session_ownership": lambda sid, uid, db_name="": True,
    "list_sessions_for_user_org": lambda *a, **k: [],
    "delete_session": lambda sid, uid, db_name="": bool(SESSIONS.pop(sid, None) is not None or MESSAGES.pop(sid, None) is not None),
}
for name, fn in patches.items():
    setattr(sm, name, fn)

from gemini_brain.memory import context_window  # noqa: E402

context_window.set_context_window_usage = lambda sid, held, db_name="": None
auth.fetch_accutax_accessible_orgs = lambda *a, **k: []
routes.fetch_accutax_accessible_orgs = lambda *a, **k: []


def fake_login(payload: dict):
    token = auth.create_access_token(user_id=USER["id"], email=USER["email"], allowed_org_ids=ORGS)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600, "user_id": USER["id"], "email": USER["email"],
            "allowed_org_ids": ORGS, "tenants": auth.placeholder_tenants(ORGS)}


app.router.routes.insert(0, APIRoute("/api/v1/auth/login-json", fake_login, methods=["POST"]))

# For scripted checks only: DEMO_FILES=<folder> serves its files at /api/v1/_demo_files/<name>, so a test page can
# build File objects from them without a file picker.
if os.environ.get("DEMO_FILES"):
    from fastapi.responses import FileResponse

    def demo_file(name: str):
        path = Path(os.environ["DEMO_FILES"]) / Path(name).name
        return FileResponse(path)

    app.router.routes.insert(0, APIRoute("/api/v1/_demo_files/{name}", demo_file, methods=["GET"]))
auth._fetch_accutax_orgs = lambda token, user_id: None
auth._query_user_allowed_orgs = lambda user_id, db_name="", **_: None

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("DEMO_PORT", "8000")), log_level="warning")
