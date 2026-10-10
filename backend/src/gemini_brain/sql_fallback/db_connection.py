"""Old import path for gemini_brain.db.connection, kept until the SQL fallback is removed.

The module object itself is aliased, so patching either path changes the same functions.
"""
import sys

from gemini_brain.db import connection as _connection

sys.modules[__name__] = _connection
