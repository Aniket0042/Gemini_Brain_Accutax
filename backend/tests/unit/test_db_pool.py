"""The database connection pool behind get_connection (Phase 0.6)."""
from types import SimpleNamespace

import pytest
from psycopg2 import extensions

from gemini_brain.sql_fallback import db_connection as db

IDLE, INTRANS, INERROR, UNKNOWN = (extensions.TRANSACTION_STATUS_IDLE, extensions.TRANSACTION_STATUS_INTRANS,
                                   extensions.TRANSACTION_STATUS_INERROR, extensions.TRANSACTION_STATUS_UNKNOWN)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.dead:
            raise db.psycopg2.OperationalError("server closed the connection")
        self.conn.status = INTRANS


class FakeConn:
    def __init__(self):
        self.closed = 0
        self.autocommit = False
        self.status = IDLE
        self.rollbacks = 0
        self.dead = False

    @property
    def info(self):
        return SimpleNamespace(transaction_status=self.status)

    def get_transaction_status(self):
        return self.status

    def rollback(self):
        self.rollbacks += 1
        self.status = IDLE

    def close(self):
        self.closed = 1

    def cursor(self):
        return FakeCursor(self)


@pytest.fixture
def opened(monkeypatch):
    made = []

    def fake_connect(*args, **kwargs):
        conn = FakeConn()
        made.append(conn)
        return conn

    monkeypatch.setattr(db.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(db.settings, "db_pool_enabled", True)
    monkeypatch.setattr(db.settings, "db_pool_max", 2)
    db.close_pools()
    yield made
    db.close_pools()


def test_a_closed_connection_is_reused(opened):
    first = db.get_connection()
    first.close()
    second = db.get_connection()
    assert len(opened) == 1 and second._conn is opened[0]
    second.close()


def test_an_open_transaction_is_rolled_back_on_return(opened):
    conn = db.get_connection()
    with conn.cursor() as cur:
        cur.execute("UPDATE x SET y = 1")
    conn.autocommit = True
    conn.close()
    assert opened[0].rollbacks == 1 and opened[0].status == IDLE and opened[0].autocommit is False


def test_a_failed_transaction_is_rolled_back_on_return(opened):
    conn = db.get_connection()
    opened[0].status = INERROR
    conn.close()
    assert opened[0].rollbacks == 1
    assert db.get_connection()._conn is opened[0]


def test_a_broken_connection_is_discarded(opened):
    conn = db.get_connection()
    opened[0].status = UNKNOWN
    conn.close()
    assert opened[0].closed
    again = db.get_connection()
    assert again._conn is opened[1]


def test_closing_twice_is_harmless(opened):
    conn = db.get_connection()
    conn.close()
    conn.close()
    a, b = db.get_connection(), db.get_connection()
    assert a._conn is not b._conn


def test_a_full_pool_serves_a_direct_connection(opened):
    a, b = db.get_connection(), db.get_connection()
    c = db.get_connection()  # pool max is 2
    assert c._pool is None and len(opened) == 3
    c.close()
    assert opened[2].closed  # direct connections are really closed
    a.close()
    b.close()


def test_an_idle_dead_connection_is_replaced(opened, monkeypatch):
    conn = db.get_connection()
    conn.close()
    opened[0].dead = True
    monkeypatch.setattr(db, "_PING_AFTER_IDLE_SECONDS", 0.0)
    again = db.get_connection()
    assert again._conn is opened[1] and opened[0].closed


def test_the_pool_can_be_switched_off(opened, monkeypatch):
    monkeypatch.setattr(db.settings, "db_pool_enabled", False)
    conn = db.get_connection()
    assert isinstance(conn, FakeConn)
