"""The numbers the anomaly rules use, in one place so finance can review and change them.

Nothing here is a legal limit except the UAE rates. The rest are starting values chosen to flag few bills; they
have not been signed off by finance (open decision in docs/VAT_REVIEW_PLAN.md).
"""
from __future__ import annotations

#: UAE standard VAT rate. Any rate other than this and zero on a purchase is a finding.
STANDARD_RATE = 0.05
#: A VAT amount within this of 5% of the net is correct: the larger of ROUNDING_AMOUNT and ROUNDING_RATE x net
#: (rounding on line level).
ROUNDING_AMOUNT = 0.05
ROUNDING_RATE = 0.002

#: When this share of an organization's bills carries no VAT, say it once instead of listing every bill.
ZERO_VAT_SYSTEMIC_SHARE = 0.80
#: A bill is large for its supplier when it exceeds the larger of MULTIPLE x median and median + SPREAD x spread
#: of the supplier's other bills, and the supplier has at least SUPPLIER_MIN_BILLS bills.
SUPPLIER_MIN_BILLS = 6
SUPPLIER_MULTIPLE = 3.0
SUPPLIER_SPREAD = 6.0
#: ...unless the supplier has at least SAME_AMOUNT_REPEATS other bills within SAME_AMOUNT_TOLERANCE of that amount.
SAME_AMOUNT_REPEATS = 2
SAME_AMOUNT_TOLERANCE = 0.02
#: A UAE tax registration number has this many digits. When this share of the bills that carry VAT show a TRN that
#: is missing or has another length, say it once as a pattern instead of listing every bill.
TRN_DIGITS = 15
TRN_SYSTEMIC_SHARE = 0.80
#: A round amount (a multiple of ROUND_STEP, at least ROUND_MIN) is only odd when round amounts are rare.
ROUND_STEP = 1000.0
ROUND_MIN = 5000.0
ROUND_SYSTEMIC_SHARE = 0.20
#: A one-off supplier's bill is odd when it is in the top (1 - ONE_OFF_PERCENTILE) share of the organization's bills.
ONE_OFF_PERCENTILE = 0.95
ONE_OFF_MIN_ORG_BILLS = 50
