"""The words of a review for sales invoices.

The rules are written once, with the words of a purchase review (supplier, bill, input VAT is claimed). A sales review
runs the same rules on sales invoices, where the other party is the customer and VAT is charged, so its sentences are
rewritten here by a fixed list of phrases. Tests read every rule's sentence for sales and fail on a word left over.
"""
from __future__ import annotations

from typing import Any

SIDES = ("purchases", "sales")

#: Longest and most specific phrases first, so a short one never splits a longer one.
_SALES = (
    ("input VAT is not being recorded", "output VAT is not being charged"),
    ("buys taxable goods or services", "sells taxable goods or services"),
    ("zero-rated, exempt or out-of-scope purchases", "zero-rated, exempt or out-of-scope sales"),
    ("The bill has no supplier name", "The invoice has no customer name"),
    ("the supplier has no TRN recorded", "the customer has no TRN recorded"),
    ("supplier's usual bill", "customer's usual invoice"),
    ("supplier's only bill", "customer's only invoice"),
    ("Supplier TRNs", "Customer TRNs"),
    ("Supplier TRN", "Customer TRN"),
    ("supplier TRN", "customer TRN"),
    ("supplier's TRN", "customer's TRN"),
    ("Supplier names", "Customer names"),
    ("supplier names", "customer names"),
    ("The same supplier", "The same customer"),
    ("supplier name", "customer name"),
    ("one-time supplier", "one-time customer"),
    ("the supplier's", "the customer's"),
    ("this supplier's", "this customer's"),
    ("is claimed", "is charged"),
    ("Bills", "Invoices"),
    ("bills", "invoices"),
    ("The bill", "The invoice"),
    (" bill", " invoice"),
    ("supplier", "customer"),
    ("Supplier", "Customer"),
)


def for_side(text: Any, side: str) -> Any:
    """The sentence for a purchase review, or the same sentence with the words of a sales review."""
    if side != "sales" or not isinstance(text, str):
        return text
    for old, new in _SALES:
        text = text.replace(old, new)
    return text
