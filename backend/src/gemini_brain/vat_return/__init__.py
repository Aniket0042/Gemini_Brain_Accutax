"""VAT return position from the books, tax periods and due dates, and the penalty calculator.

The books hold the VAT on every issued document but no filed return (the vat_returns table is empty), so the position is
computed from the documents and the answer says so. Plan: docs/VAT_AGENT_COMPLETION_PLAN.md, package 5.
"""
