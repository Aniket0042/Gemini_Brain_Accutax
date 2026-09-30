"""
auth.py — Authentication, JWT token management, password hashing, and tenant isolation DDL.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import bcrypt

from gemini_brain.config.settings import settings
from gemini_brain.sql_fallback.db_connection import get_connection

logger = logging.getLogger("gemini_brain.api.auth")

# OAuth2 scheme for Swagger UI Authorize button integration
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    description="Enter your email and password in the Authorize dialog to acquire a JWT token.",
    auto_error=False,
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a stored password hash or plaintext."""
    if not hashed_password:
        return False
    try:
        pw_bytes = plain_password.encode("utf-8")[:72]
        hash_bytes = hashed_password.encode("utf-8")
        if bcrypt.checkpw(pw_bytes, hash_bytes):
            return True
    except Exception:
        pass
    # Fallback comparison if password in database was stored in plaintext
    return plain_password == hashed_password


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    pw_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt(10)
    return bcrypt.hashpw(pw_bytes, salt).decode("utf-8")


import os

def get_jwt_secret() -> str:
    """Retrieve JWT secret from settings or environment. Raises ValueError if unconfigured."""
    secret = settings.jwt_secret or os.getenv("JWT_SECRET", "")
    if not secret:
        raise ValueError(
            "JWT_SECRET is required and was not provided in settings, parameters, or environment."
        )
    return secret


def create_access_token(
    user_id: int,
    email: str,
    allowed_org_ids: list[int],
    expires_delta: timedelta | None = None,
    accutax_token: str = "",
) -> str:
    """Create a signed JWT access token containing user identity and allowed organization IDs.

    ``accutax_token`` is the upstream Accutax bearer this session should use for
    REST calls. Carrying it as a claim inside our own signed token keeps
    verification stateless: previously the upstream token was handed to the
    client as-is and could only be validated by looking it up in an in-process
    dict, so every session died on restart and tokens minted by one worker were
    rejected by another. The client already held this value, so nothing new is
    disclosed by embedding it.
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.jwt_expiration_minutes)

    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "allowed_org_ids": allowed_org_ids,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    if accutax_token:
        payload["accutax_token"] = accutax_token

    token = jwt.encode(
        payload,
        get_jwt_secret(),
        algorithm=settings.jwt_algorithm,
    )
    return token


_BADGE_COLORS = ("indigo", "emerald", "amber", "purple")


def placeholder_tenants(org_ids: list[int]) -> list[dict[str, Any]]:
    """Minimal tenant entries for when the organizations table cannot be read."""
    return [
        {
            "id": int(oid),
            "name": f"Organization {oid}",
            "display_name": f"Organization {oid}",
            "tag": "Tenant",
            "badge_color": _BADGE_COLORS[i % len(_BADGE_COLORS)],
            "industry": "",
            "currency": "AED",
            "description": "",
        }
        for i, oid in enumerate(org_ids)
    ]


def fetch_organizations_from_db(org_ids: list[int] | None = None, db_name: str = "") -> list[dict[str, Any]]:
    """Fetch organizations directly from PostgreSQL public.organizations table."""
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        try:
            if org_ids:
                cur.execute(
                    """
                    SELECT id, name, emirate, currency, company_type
                    FROM public.organizations
                    WHERE id = ANY(%s)
                    ORDER BY id ASC;
                    """,
                    (list(org_ids),),
                )
            else:
                cur.execute(
                    """
                    SELECT id, name, emirate, currency, company_type
                    FROM public.organizations
                    ORDER BY id ASC;
                    """
                )
            rows = cur.fetchall()
            tenants = []
            for i, r in enumerate(rows):
                oid = int(r[0])
                name = r[1] or f"Organization {oid}"
                emirate = r[2] or ""
                currency = r[3] or "AED"
                display_label = f"{name} ({emirate})" if emirate else name
                tenants.append({
                    "id": oid,
                    "name": name,
                    "display_name": display_label,
                    "tag": emirate or "Tenant",
                    "badge_color": _BADGE_COLORS[i % len(_BADGE_COLORS)],
                    "industry": str(r[4] or ""),
                    "currency": currency,
                    "description": "",
                })
            return tenants
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning("Failed to query organizations table from DB: %s", e)
        return []


# Tokens we've personally seen returned by a successful upstream Accutax
# login. We can't verify their signature (Accutax signs with its own secret,
# not ours), so a token is only trusted on later requests if it's in here --
# i.e. we ourselves witnessed it being issued after a real password check.
# Bounded to avoid unbounded growth; oldest entries are evicted first.
_TRUSTED_UPSTREAM_TOKENS: dict[str, dict[str, Any]] = {}
_MAX_TRUSTED_TOKENS = 5000
_ACCUTAX_ORG_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_ACCUTAX_ORG_CACHE_TTL_SECONDS = 60.0


def _remember_trusted_token(token: str, claims: dict[str, Any]) -> None:
    if len(_TRUSTED_UPSTREAM_TOKENS) >= _MAX_TRUSTED_TOKENS:
        _TRUSTED_UPSTREAM_TOKENS.pop(next(iter(_TRUSTED_UPSTREAM_TOKENS)))
    _TRUSTED_UPSTREAM_TOKENS[token] = claims


def authenticate_with_accutax_api(email: str, password: str) -> dict[str, Any] | None:
    """Attempt upstream authentication via Accutax Backend API.
    
    Returns token payload dict on success, or None on failure/unreachable.
    """
    try:
        import httpx
        url = f"{settings.accutax_base_url.rstrip('/')}/auth/login"
        resp = httpx.post(
            url,
            json={"email": email, "password": password},
            timeout=httpx.Timeout(2.5, connect=1.5),
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            if isinstance(data, dict) and data.get("error") is True:
                logger.info("Upstream Accutax login returned error (%s) — falling back to local credentials", data.get("message"))
                return None

            token = (
                data.get("token")
                or data.get("access_token")
                or (data.get("data") if isinstance(data.get("data"), dict) else {}).get("token")
            )
            if token:
                try:
                    claims = jwt.decode(token, options={"verify_signature": False})
                except Exception:
                    claims = {}
                raw_user_id = claims.get("userId") or claims.get("user_id") or claims.get("sub")
                try:
                    user_id = int(raw_user_id)
                except (TypeError, ValueError):
                    # Never guess an identity: a token we cannot tie to a user
                    # would otherwise be served as some default account.
                    logger.warning("Upstream Accutax token carries no usable user id; rejecting upstream login")
                    return None
                user_email = claims.get("email") or email
                allowed_orgs = _live_allowed_org_ids(user_id, token) or []
                logger.info("Successfully authenticated with upstream Accutax API for %s (userId=%d)", user_email, user_id)
                _remember_trusted_token(token, {
                    "sub": str(user_id),
                    "userId": user_id,
                    "email": user_email,
                    "allowed_org_ids": allowed_orgs,
                    "accutax_token": token,
                })
                return {
                    "access_token": token,
                    "user_id": user_id,
                    "email": user_email,
                    "allowed_org_ids": allowed_orgs,
                }
    except Exception as e:
        logger.info("Upstream Accutax API unreachable (%s) — using local auth fallback", e)
    return None


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token, extracting user ID and claims.

    Raises HTTPException(401) for any token we cannot establish is genuine --
    either signed with our own JWT_SECRET, or one we ourselves saw returned by
    a successful upstream Accutax login (see _TRUSTED_UPSTREAM_TOKENS).
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")

    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=[settings.jwt_algorithm])
    except Exception as e:
        accutax_claims = _decode_accutax_app_token(token)
        if accutax_claims is not None:
            return accutax_claims
        # Not signed with our own secret -- only legitimate if it's a token we
        # ourselves saw an upstream Accutax login return. Anything else is
        # rejected outright: we must never trust claims from an unverifiable token.
        cached = _TRUSTED_UPSTREAM_TOKENS.get(token)
        if cached is not None:
            return dict(cached)
        logger.warning("Rejected access token that failed verification (reason: %s)", e)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token") from e


def fetch_accutax_accessible_orgs(token: str, user_id: int) -> list[dict[str, Any]]:
    """Owned + collaborator organizations from Accutax, matching the dashboard switcher."""
    return _fetch_accutax_orgs(token, user_id) or []


def _fetch_accutax_orgs(token: str, user_id: int) -> list[dict[str, Any]] | None:
    """Like fetch_accutax_accessible_orgs, but None when Accutax could not answer.

    An empty list is a real answer (the user has no organizations) and must not
    be confused with a failed lookup, or revoked access could not be told apart
    from an outage.
    """
    if not token or not user_id:
        return None
    now = time.time()
    cache_key = f"{user_id}:{token[:24]}"
    cached = _ACCUTAX_ORG_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _ACCUTAX_ORG_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        import httpx
        url = f"{settings.accutax_base_url.rstrip('/')}/auth/all_organizations"
        resp = httpx.get(
            url,
            params={"user_id": str(user_id)},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(4.0, connect=1.5),
        )
        if resp.status_code != 200:
            logger.warning("Accutax all_organizations returned %s", resp.status_code)
            return None
        body = resp.json()
        raw = body.get("data") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            return None
        tenants: list[dict[str, Any]] = []
        for org in raw:
            if not isinstance(org, dict) or org.get("id") is None:
                continue
            try:
                oid = int(org["id"])
            except (TypeError, ValueError):
                continue
            label = (
                org.get("organization_name")
                or org.get("name")
                or org.get("display_name")
                or f"Organization {oid}"
            )
            tenants.append(
                {
                    "id": oid,
                    "name": str(label),
                    "display_name": str(label),
                    "tag": "Collaborator" if org.get("is_collaborator") else "Owner",
                    "badge_color": "emerald",
                    "industry": str(org.get("industry") or ""),
                    "currency": str(org.get("currency") or "AED"),
                    "description": "",
                }
            )
        _ACCUTAX_ORG_CACHE[cache_key] = (now, tenants)
        return tenants
    except Exception as e:
        logger.warning("Failed to load Accutax organizations for user %s: %s", user_id, e)
        return None


def _decode_accutax_app_token(token: str) -> dict[str, Any] | None:
    """Accept the Nest JWT the Accutax SPA already has, if ACCUTAX_JWT_SECRET is set."""
    secret = settings.accutax_jwt_secret or os.getenv("ACCUTAX_JWT_SECRET") or os.getenv("JWT_SECRET_KEY") or ""
    if not secret:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except Exception:
        return None

    user_id = claims.get("userId") or claims.get("user_id") or claims.get("sub")
    if user_id is None:
        return None
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return None

    # Live access is resolved per request in get_current_user. The org this
    # token was issued for is kept only as the last-resort fallback there.
    token_orgs: list[int] = []
    try:
        if claims.get("organization_id") is not None:
            token_orgs.append(int(claims["organization_id"]))
    except (TypeError, ValueError):
        pass

    return {
        "sub": str(user_id_int),
        "userId": user_id_int,
        "email": claims.get("email") or "",
        "allowed_org_ids": token_orgs,
        "accutax_token": token,
    }


def _load_seed_users() -> list[dict[str, Any]]:
    """Parse SEED_TEST_USERS, a JSON list of {name, email, password, org_ids}."""
    raw = (settings.seed_test_users or "").strip()
    if not raw:
        return []
    try:
        entries = json.loads(raw)
    except ValueError as e:
        logger.error("SEED_TEST_USERS is not valid JSON; no test users seeded: %s", e)
        return []
    if not isinstance(entries, list):
        logger.error("SEED_TEST_USERS must be a JSON list; no test users seeded.")
        return []

    seeds: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        try:
            seeds.append({
                "name": str(entry.get("name") or entry["email"]),
                "email": str(entry["email"]),
                "password": str(entry["password"]),
                "org_ids": [int(o) for o in entry.get("org_ids") or []],
            })
        except (AttributeError, KeyError, TypeError, ValueError):
            # Index only: the entry itself holds a password.
            logger.error("Skipping malformed SEED_TEST_USERS entry at index %d.", index)
    return seeds


def init_auth_db(db_name: str = "") -> None:
    """Create the local auth tables and seed the accounts listed in SEED_TEST_USERS.

    Does nothing when SEED_TEST_USERS is empty, so a database whose users and
    organizations belong to the Accutax backend is never written to at startup.
    """
    seed_users = _load_seed_users()
    if not seed_users:
        logger.info("SEED_TEST_USERS is empty; skipping auth DB initialization.")
        return

    conn = get_connection(db_name)
    cur = conn.cursor()
    try:
        # 1. Create users table if it does not already exist
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.users (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255),
                email VARCHAR(255) UNIQUE NOT NULL,
                password VARCHAR(255) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Create user_organizations table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS public.user_organizations (
                id SERIAL PRIMARY KEY,
                user_id INT NOT NULL,
                organization_id INT NOT NULL,
                role VARCHAR(50) NOT NULL DEFAULT 'member',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_user_org UNIQUE (user_id, organization_id)
            );
        """)

        conn.commit()

        # 3. Seed test accounts if missing
        # Check existing columns in public.users to build robust INSERT statement
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='users';")
        cols = {r[0] for r in cur.fetchall()}

        for seed in seed_users:
            name, email, password, orgs = seed["name"], seed["email"], seed["password"], seed["org_ids"]
            cur.execute("SELECT id, password FROM public.users WHERE email = %s;", (email,))
            row = cur.fetchone()
            if not row:
                pw_hash = hash_password(password)
                if "image_url" in cols:
                    cur.execute(
                        """
                        INSERT INTO public.users (
                            name, email, password, image_url, email_verified, phone_number,
                            eid_number, license_number, mfa_secret, mfa_enabled, is_super_admin
                        ) VALUES (%s, %s, %s, '', false, '', '', '', '', false, false) RETURNING id;
                        """,
                        (name, email, pw_hash),
                    )
                else:
                    cur.execute(
                        "INSERT INTO public.users (name, email, password) VALUES (%s, %s, %s) RETURNING id;",
                        (name, email, pw_hash),
                    )
                user_id = cur.fetchone()[0]
                logger.info("Created seed user: %s (id=%d)", email, user_id)
            else:
                user_id = row[0]

            # Sync user_organizations
            for org_id in orgs:
                cur.execute(
                    """
                    INSERT INTO public.user_organizations (user_id, organization_id, role)
                    VALUES (%s, %s, 'member')
                    ON CONFLICT (user_id, organization_id) DO NOTHING;
                    """,
                    (user_id, org_id),
                )
        conn.commit()
        logger.info("Auth DB initialized successfully.")
    except Exception as e:
        conn.rollback()
        logger.error("Failed to initialize auth DB: %s", e)
    finally:
        cur.close()
        conn.close()


def get_user_by_email(email: str, db_name: str = "") -> dict[str, Any] | None:
    """Fetch user record by email from public.users. None if absent or the DB is unreachable."""
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        try:
            cur.execute("SELECT id, email, password FROM public.users WHERE email = %s;", (email,))
            row = cur.fetchone()
            if row:
                return {"id": row[0], "email": row[1], "password": row[2]}
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning("Failed to query user by email from DB: %s", e)
    return None


def get_user_allowed_orgs(user_id: int, db_name: str = "") -> list[int]:
    """Organizations the user is granted in user_organizations or owns via organizations.user_id."""
    return _query_user_allowed_orgs(user_id, db_name) or []


_DB_ORG_CACHE: dict[int, tuple[float, list[int]]] = {}


def _query_user_allowed_orgs(user_id: int, db_name: str = "") -> list[int] | None:
    """Like get_user_allowed_orgs, but None when the database could not answer."""
    try:
        conn = get_connection(db_name)
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT organization_id FROM public.user_organizations WHERE user_id = %s
                UNION
                SELECT id FROM public.organizations WHERE user_id = %s
                ORDER BY 1;
                """,
                (user_id, user_id),
            )
            return [int(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning("Failed to fetch user allowed orgs from DB: %s", e)
        return None


def _live_allowed_org_ids(user_id: int, accutax_token: str = "") -> list[int] | None:
    """Current organization access from the source of truth, or None if unreachable.

    Accutax's own organization list comes first, since it is what the dashboard
    switcher shows (owners and collaborators). The database is the fallback,
    and the only source for locally issued tokens. Both answers are cached for
    the same TTL, so revoked access stops working within that window.
    """
    if accutax_token:
        live = _fetch_accutax_orgs(accutax_token, user_id)
        if live is not None:
            return [int(t["id"]) for t in live]

    now = time.time()
    cached = _DB_ORG_CACHE.get(user_id)
    if cached and (now - cached[0]) < _ACCUTAX_ORG_CACHE_TTL_SECONDS:
        return cached[1]
    from_db = _query_user_allowed_orgs(user_id)
    if from_db is not None:
        _DB_ORG_CACHE[user_id] = (now, from_db)
    return from_db


def resolve_allowed_org_ids(
    user_id: int,
    accutax_token: str = "",
    token_org_ids: list[int] | None = None,
) -> list[int]:
    """Organizations this user may query right now.

    The org list signed into a token is a snapshot from login, so it keeps
    granting access after that access is revoked. It is used only when no live
    source can answer. With no live answer and no snapshot, access is refused.
    """
    live = _live_allowed_org_ids(user_id, accutax_token)
    if live is not None:
        return live
    if token_org_ids is not None:
        logger.warning(
            "Live organization lookup failed for user %s; using the org list from the token",
            user_id,
        )
        return [int(o) for o in token_org_ids]
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not verify organization access. Try again shortly.",
    )


_audit_logger = logging.getLogger("gemini_brain.audit.org_access")


def authorize_org_scope(
    requested: Iterable[Any] | None,
    user: CurrentUser,
    *,
    action: str,
) -> list[int]:
    """The single gate for every organization a request names.

    Returns the requested IDs, de-duplicated in request order, when the user may
    access all of them. Refuses the whole request if any one is outside the
    user's live allow-list: dropping it silently would answer a different
    question than the one asked. An empty allow-list grants nothing. Naming no
    organization returns [] and leaves the default to the caller.

    Every decision is written to the org-access audit log.
    """
    try:
        orgs = list(dict.fromkeys(int(o) for o in (requested or []) if o is not None))
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization IDs must be integers.",
        ) from e

    limit = settings.max_orgs_per_query
    if len(orgs) > limit:
        _audit_logger.warning(
            "org_access denied action=%s user=%s requested=%s reason=too_many limit=%d",
            action, user.user_id, orgs, limit,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {limit} organizations can be queried together.",
        )

    allowed = {int(o) for o in (user.allowed_org_ids or [])}
    denied = [o for o in orgs if o not in allowed]
    if denied:
        _audit_logger.warning(
            "org_access denied action=%s user=%s requested=%s denied=%s allowed_count=%d",
            action, user.user_id, orgs, denied, len(allowed),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is not in the caller's allowed tenant list.",
        )

    if orgs:
        _audit_logger.info("org_access granted action=%s user=%s orgs=%s", action, user.user_id, orgs)
    return orgs


class CurrentUser:
    """Class representing authenticated user claims extracted from JWT token."""

    def __init__(
        self,
        user_id: int,
        email: str,
        allowed_org_ids: list[int],
        raw_token: str = "",
        accutax_token: str = "",
    ):
        self.user_id = user_id
        self.email = email
        self.allowed_org_ids = allowed_org_ids
        self.raw_token = raw_token
        # Set only when the caller holds a genuine Accutax bearer; raw_token
        # falls back to our own JWT, which Accutax does not accept.
        self.accutax_token = accutax_token


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> CurrentUser:
    """FastAPI Dependency: Validates the bearer JWT. Requires authentication -- no
    unauthenticated request may access tenant financial data."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_access_token(token)
    # No default identity: a verified token that names no user is refused
    # rather than served as some fallback account.
    raw_user_id = payload.get("sub") or payload.get("userId")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError) as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token does not identify a user",
            headers={"WWW-Authenticate": "Bearer"},
        ) from e

    email = payload.get("email", "")
    accutax_token = payload.get("accutax_token") or ""
    # Resolved live on every request, so revoked access ends within the lookup
    # cache TTL instead of lasting until the token expires. A live answer of []
    # means the user has no organizations and is final.
    token_org_ids = (payload.get("allowed_org_ids") or []) if "allowed_org_ids" in payload else None
    allowed_org_ids = resolve_allowed_org_ids(user_id, accutax_token, token_org_ids)

    # Accutax REST calls need the upstream bearer, which rides as a claim. Fall
    # back to the presented token for locally-issued tokens that carry none.
    upstream_token = accutax_token or token

    return CurrentUser(
        user_id=user_id,
        email=email,
        allowed_org_ids=[int(o) for o in allowed_org_ids],
        raw_token=upstream_token,
        accutax_token=accutax_token,
    )
