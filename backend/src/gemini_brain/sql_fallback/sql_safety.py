"""
sql_safety.py — SQL read-only validation.

Re-exports `assert_read_only` from agents/executor.py, which is the more
thorough of what used to be two independently-maintained implementations
here and there: this module's own version only blocked 6 keywords
(insert/update/delete/drop/alter/truncate); executor.py's blocks 18 —
including DO/CALL/COPY/GRANT/REVOKE/CREATE and the filesystem/sleep/dblink
escape hatches — and also rejects multi-statement input and anything that
doesn't start with SELECT/WITH. Kept as a separate import path (rather than
inlining the check here) so every existing caller of
`gemini_brain.sql_fallback.sql_safety.assert_read_only` keeps working
unchanged while transparently getting the stronger check.
"""
from __future__ import annotations

from gemini_brain.agents.executor import assert_read_only

__all__ = ["assert_read_only"]
