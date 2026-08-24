import sys
from pathlib import Path
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

import psycopg2
from gemini_brain.config.settings import settings

conn = psycopg2.connect(
    host=settings.db_host,
    port=settings.db_port,
    dbname=settings.db_name,
    user=settings.db_user,
    password=settings.db_password
)
cur = conn.cursor()
tables = [
    'expense', 'projects', 'items', 'income', 'income_items',
    'delivery_notes', 'delivery_notes_items', 'delivery_note_lines',
    'warehouses', 'chart_of_accounts', 'contacts', 'bank_accounts'
]
for t in tables:
    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{t}' ORDER BY ordinal_position;")
    cols = [r[0] for r in cur.fetchall()]
    print(f"Table '{t}':", cols)
conn.close()
