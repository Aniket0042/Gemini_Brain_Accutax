"""
apply_sql_functions.py — Applies Phase 4 SQL functions and migrations to PostgreSQL.
"""
import os
import sys
from pathlib import Path
import psycopg2

# Add src to path
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from gemini_brain.config.settings import settings

SQL_DIR = Path(__file__).resolve().parent.parent / "sql" / "functions"

MIGRATION_FILES = [
    "001_fn_project_expense_rollup.sql",
    "002_fn_inventory_movement.sql",
    "003_fn_gl_profitability.sql",
]

def apply_migrations():
    print("=" * 60)
    print("APPLYING PHASE 4 SQL FUNCTIONS TO DATABASE")
    print("=" * 60)
    print(f"Target DB from settings: {settings.db_host}:{settings.db_port}/{settings.db_name}")
    
    # Try connecting with settings or fallbacks
    conn = None
    targets = [
        (settings.db_host, settings.db_port, settings.db_name, settings.db_user, settings.db_password),
        ("127.0.0.1", 5433, settings.db_name, "postgres", "12345678"),
        ("127.0.0.1", 5432, settings.db_name, "postgres", "12345678"),
        ("127.0.0.1", 5433, "postgres", "postgres", "12345678"),
    ]
    
    active_target = None
    for host, port, db, user, pwd in targets:
        try:
            print(f"Attempting connection to {host}:{port}/{db} as {user}...")
            conn = psycopg2.connect(
                host=host,
                port=port,
                dbname=db,
                user=user,
                password=pwd,
                connect_timeout=3,
            )
            print(f"[OK] CONNECTED to {host}:{port}/{db}")
            active_target = (host, port, db, user, pwd)
            break
        except Exception as e:
            print(f"  Connection failed: {e}")
            conn = None
            
    if not conn:
        print("\n[FAIL] Could not establish PostgreSQL connection.")
        sys.exit(1)
        
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for fname in MIGRATION_FILES:
                fpath = SQL_DIR / fname
                if not fpath.exists():
                    print(f"[WARN] File not found: {fpath}")
                    continue
                print(f"\nApplying migration: {fname}...")
                sql_content = fpath.read_text(encoding="utf-8")
                cur.execute(sql_content)
                print(f"[OK] Successfully applied {fname}")
                
        print("\n" + "=" * 60)
        print("ALL PHASE 4 SQL FUNCTIONS SUCCESSFULLY CREATED IN DATABASE!")
        print("=" * 60)
    except Exception as e:
        print(f"\n[FAIL] Error applying migration: {e}")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    apply_migrations()
