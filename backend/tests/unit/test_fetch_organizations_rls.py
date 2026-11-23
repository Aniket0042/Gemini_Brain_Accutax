"""public.organizations is protected by row-level security keyed on app.current_org, so the name lookup must declare
each company before it reads it. A fake connection stands in for Postgres and applies the same rule."""
from gemini_brain.api import auth

ROWS = {6: (6, "connectify.global", "Dubai", "AED", "LLC"), 7: (7, "acme corp", None, "USD", None), 9: (9, "other co", None, "AED", None)}


class FakeCursor:
    def __init__(self, conn):
        self.conn, self.result = conn, []

    def execute(self, sql, params=None):
        if sql.startswith("SET LOCAL app.current_org"):
            self.conn.current = int(params[0])
            self.conn.log.append(("set", self.conn.current))
        elif "FROM public.organizations WHERE id" in sql:
            # RLS: only the declared company is visible
            self.result = [ROWS[params[0]]] if self.conn.current == params[0] and params[0] in ROWS else []
        else:  # unfiltered read with nothing declared: RLS shows nothing
            self.result = [ROWS[self.conn.current]] if self.conn.current in ROWS else []

    def fetchall(self):
        return self.result

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.current, self.log, self.autocommit, self.closed = None, [], True, False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.current = None  # SET LOCAL ends with the transaction
        self.log.append(("commit",))

    def close(self):
        self.closed = True


def test_names_are_read_one_company_at_a_time(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(auth, "get_connection", lambda db_name="": conn)
    tenants = auth.fetch_organizations_from_db([7, 6, 6])
    assert [t["name"] for t in tenants] == ["connectify.global", "acme corp"]
    assert tenants[0]["display_name"] == "connectify.global (Dubai)"
    assert conn.log == [("set", 6), ("commit",), ("set", 7), ("commit",)]  # sorted, de-duplicated, never two at once
    assert conn.autocommit is True and conn.closed


def test_only_the_requested_companies_are_returned(monkeypatch):
    monkeypatch.setattr(auth, "get_connection", lambda db_name="": FakeConn())
    assert [t["id"] for t in auth.fetch_organizations_from_db([6])] == [6]  # 7 and 9 are never read


def test_database_error_still_falls_back_to_empty(monkeypatch):
    def boom(db_name=""):
        raise RuntimeError("down")
    monkeypatch.setattr(auth, "get_connection", boom)
    assert auth.fetch_organizations_from_db([6]) == []
