"""
build_pii_labelled.py — Writes tests/data/pii_labelled.json, the labelled strings the recognizers are scored on.

    python scripts/eval/build_pii_labelled.py

Every value is synthetic (seeded, so the file is the same on every run). Check digits are computed here with
their own code, not with privacy/recognizers.py, so a wrong check in the recognizers shows up as a miss.
Each case is {"text", "entities": [{"kind", "text"}], "group"}: "positive" cases hold identifiers, "negative"
cases hold figures, dates, document numbers and codes that must not be flagged.
"""
from __future__ import annotations

import json
import random
import string
from pathlib import Path
from typing import Dict, List

OUT = Path(__file__).resolve().parents[2] / "tests" / "data" / "pii_labelled.json"
R = random.Random(20261017)

# ── Check digits, written independently of the recognizers ──────────────────


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return str((10 - total % 10) % 10)


def luhn_valid(number: str) -> bool:
    return luhn_digit(number[:-1]) == number[-1]


def iban_ae(bban: str) -> str:
    numeric = "".join(str(int(c, 36)) for c in bban + "AE00")
    return f"AE{98 - int(numeric) % 97:02d}{bban}"


D = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
     [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
     [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
     [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
P = [[0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
     [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
     [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]
INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]


def verhoeff_digit(body: str) -> str:
    c = 0
    for i, ch in enumerate(reversed(body)):
        c = D[c][P[(i + 1) % 8][int(ch)]]
    return str(INV[c])


def verhoeff_valid(number: str) -> bool:
    return verhoeff_digit(number[:-1]) == number[-1]


B36 = string.digits + string.ascii_uppercase


def gstin_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(body):
        v = B36.index(ch) * (2 if i % 2 else 1)
        total += v // 36 + v % 36
    return B36[(36 - total % 36) % 36]


# ── Values ──────────────────────────────────────────────────────────────────

def digits(n: int, first: str = string.digits) -> str:
    return R.choice(first) + "".join(R.choice(string.digits) for _ in range(n - 1))


def letters(n: int) -> str:
    return "".join(R.choice(string.ascii_uppercase) for _ in range(n))


def trn() -> str:
    return "100" + digits(12)


def emirates_id(sep: bool) -> str:
    body = "784" + digits(4, "12") + digits(7)
    raw = body + luhn_digit(body)
    return f"{raw[:3]}-{raw[3:7]}-{raw[7:14]}-{raw[14]}" if sep else raw


def iban(spaced: bool) -> str:
    value = iban_ae(digits(3) + digits(16))
    return " ".join(value[i:i + 4] for i in range(0, len(value), 4)) if spaced else value


def uae_mobile() -> str:
    core = R.choice("0245678") + digits(7)
    return R.choice([f"05{core[0]} {core[1:4]} {core[4:]}", f"+971 5{core[0]} {core[1:4]} {core[4:]}",
                     f"05{core}", f"009715{core}", f"+9715{core[0]}-{core[1:4]}-{core[4:]}"])


def email() -> str:
    user = R.choice(["ahmed.k", "priya", "accounts", "j.smith", "finance.team", "rahul_m", "sara+bills"])
    host = R.choice(["alnoor.ae", "gmail.com", "zenith-foods.com", "mail.example.co.in", "outlook.com"])
    return f"{user}{R.randint(1, 99)}@{host}"


def card() -> str:
    body = R.choice("45") + digits(14)
    raw = body + luhn_digit(body)
    return R.choice([raw, " ".join(raw[i:i + 4] for i in range(0, 16, 4)), "-".join(raw[i:i + 4] for i in range(0, 16, 4))])


def pan() -> str:
    return letters(3) + R.choice("PCHFATBLJG") + letters(1) + digits(4) + letters(1)


def gstin() -> str:
    body = f"{R.randint(1, 37):02d}" + pan() + R.choice("123456789") + "Z"
    return body + gstin_digit(body)


def aadhaar(spaced: bool) -> str:
    body = digits(11, "23456789")
    raw = body + verhoeff_digit(body)
    return f"{raw[:4]} {raw[4:8]} {raw[8:]}" if spaced else raw


def cin() -> str:
    return R.choice("LU") + digits(5) + R.choice(["MH", "KA", "DL", "TN"]) + str(R.randint(1990, 2025)) + \
        R.choice(["PLC", "PTC", "OPC"]) + digits(6)


def ifsc() -> str:
    return R.choice(["SBIN", "HDFC", "ICIC", "UTIB", "KKBK"]) + "0" + digits(6)


def india_mobile_intl() -> str:
    core = digits(10, "6789")
    return R.choice([f"+91 {core[:5]} {core[5:]}", f"+91-{core}", f"+91{core}"])


def upi() -> str:
    return R.choice(["ravi.k", "9876543210", "priya", "shop.kumar"]) + "@" + R.choice(["okaxis", "ybl", "paytm", "oksbi"])


# ── Cases ───────────────────────────────────────────────────────────────────

cases: List[Dict] = []


def pos(template: str, **values: tuple) -> None:
    text = template.format(**{k: v[1] for k, v in values.items()})
    cases.append({"group": "positive", "text": text,
                  "entities": [{"kind": v[0], "text": v[1]} for v in values.values()]})


def neg(text: str) -> None:
    cases.append({"group": "negative", "text": text, "entities": []})


for _ in range(14):
    pos(R.choice(["Supplier TRN {a} on bill BILL-{n}", "TRN: {a}", "Is the TRN {a} valid?", "vat no {a} for this vendor",
                  "Their tax registration number is {a}."]).replace("{n}", digits(4)), a=("TRN", trn()))
for _ in range(10):
    pos(R.choice(["Emirates ID {a}", "EID: {a}", "Staff ID card shows {a}", "id no {a} on the visa copy"]),
        a=("EMIRATES_ID", emirates_id(R.random() < 0.7)))
for _ in range(12):
    pos(R.choice(["Pay to IBAN {a}", "IBAN: {a} (Emirates NBD)", "Bank details {a}, swift EBILAEAD", "Account {a}"]),
        a=("IBAN", iban(R.random() < 0.4)))
for _ in range(14):
    pos(R.choice(["Call me on {a}.", "Mobile {a}", "Contact Ahmed at {a} for the delivery", "whatsapp {a}",
                  "Driver: {a}"]), a=("PHONE", uae_mobile()))
for _ in range(14):
    pos(R.choice(["Email {a} the statement", "Send it to {a}.", "contact: {a}", "Ask {a} about bill 2041"]),
        a=("EMAIL", email()))
for _ in range(10):
    pos(R.choice(["Card {a} was charged", "Paid with visa {a}", "card no {a}", "Refund to {a}"]), a=("CARD", card()))
for _ in range(10):
    pos(R.choice(["PAN {a}", "Vendor PAN: {a}", "Their PAN is {a}.", "permanent account number {a}"]), a=("PAN", pan()))
for _ in range(12):
    pos(R.choice(["GSTIN {a}", "GST no: {a}", "Supplier GSTIN {a} on invoice", "{a} is their GST number"]),
        a=("GSTIN", gstin()))
for _ in range(12):
    pos(R.choice(["Aadhaar {a}", "aadhar no {a}", "UID {a} for the director", "KYC: {a}"]),
        a=("AADHAAR", aadhaar(R.random() < 0.6)))
for _ in range(6):
    pos(R.choice(["TAN {a}", "Deductor TAN: {a}"]), a=("TAN", letters(4) + digits(5) + letters(1)))
for _ in range(6):
    pos(R.choice(["CIN {a}", "Company CIN: {a}", "registered as {a}"]), a=("CIN", cin()))
for _ in range(4):
    pos("LLPIN {a}", a=("LLPIN", letters(3) + "-" + digits(4)))
for _ in range(8):
    pos(R.choice(["IFSC {a}", "Bank IFSC: {a}", "transfer via {a}", "branch code {a}"]), a=("IFSC", ifsc()))
for _ in range(8):
    pos(R.choice(["UPI {a}", "pay {a}", "VPA: {a}"]), a=("UPI_ID", upi()))
for _ in range(10):
    pos(R.choice(["Call {a}", "mobile: {a}", "{a} is the accountant's number"]), a=("PHONE", india_mobile_intl()))
for _ in range(6):
    core = digits(10, "6789")
    pos(R.choice(["mob {a}", "phone: {a}", "call me on {a}"]), a=("PHONE", core))
for _ in range(6):
    pos(R.choice(["UTR {a}", "NEFT ref no {a}", "RRN {a}"]), a=("UTR", R.choice([letters(4) + digits(12), digits(12)])))
for _ in range(4):
    pos(R.choice(["EWB {a}", "e-way bill {a}"]), a=("EWAY_BILL", digits(12, "123456789")))
for _ in range(4):
    pos("IRN {a}", a=("IRN", "".join(R.choice("0123456789abcdef") for _ in range(64))))
for _ in range(8):
    pos(R.choice(["Passport {a}", "passport no: {a}", "ppt {a}"]), a=("PASSPORT", letters(1) + digits(7)))

# Several identifiers in one line.
for _ in range(16):
    t, e, m = trn(), email(), uae_mobile()
    pos("Supplier TRN {a}, email {b}, mobile {c}.", a=("TRN", t), b=("EMAIL", e), c=("PHONE", m))
for _ in range(8):
    pos("GSTIN {a} and PAN {b} on file; IFSC {c}", a=("GSTIN", gstin()), b=("PAN", pan()), c=("IFSC", ifsc()))
for _ in range(8):
    pos("Refund AED 1,250.00 to IBAN {a} for card {b}", a=("IBAN", iban(False)), b=("CARD", card()))

# ── Negatives: figures, dates, document numbers and codes ──
fixed_negatives = [
    "Net AED 18,450.00 plus VAT AED 922.50", "Total 1,234,567.89 AED for Q3 2026", "Revenue 100234567800.50",
    "Amount 1002345678000.50", "bill INV-BULK-26-20241216180347-114231", "journal JE-24-2026-00000773",
    "document BILL0000123 dated 2026-09-12", "PO-2026-000123 is pending", "Account 1100-200 Cash on Hand",
    "VAT rate 5% on 2026-10-01", "Period 2026-07-01 to 2026-09-30", "Invoice INV-2026-000451 for 3,150.75",
    "Order 9876543210 shipped", "Batch 2345 6789 0123 4567", "SKU AB12CD34 out of stock", "ref 4f9a2c7e",
    "Hash 3b5d5c3712955042212316173ccd37a0", "Quantity 784 units at 19.99", "784-units sold in 2026",
    "Score 5012345678 points", "Q3 margin 34.2%", "Outstanding 150000.00 over 90 days", "TRN missing on 12 bills",
    "Supplier TRN check: 15 digits required", "Open payables 2,045,330.10", "Week 42 of 2026",
    "Expense category Utilities 12,400", "Item code ABCDE-1234-F", "Bank charges 52.50 on 2026-09-30",
    "Credit note CN-26-0007 for 1,000.00", "VAT return period Jul 2026", "Opening balance 0.00",
    "Journal JE-31-2026-00012045 posted", "Bills dated 2025-12-31 and 2026-01-01", "Total 99,999,999.99",
    "1,000,000 AED target", "Reference INV/2026/000123", "Due in 30 days", "Code GST5 applied",
    "ABCD0123456 is a shelf code", "Ledger account 4000 Sales", "Invoice number 100234",
    "Phone bill 450.00 for September", "Card fees 3.5%", "Aging bucket 61-90 days",
]
for t in fixed_negatives:
    neg(t)
for _ in range(25):
    neg(f"Paid AED {R.randint(1, 9_999_999):,}.{R.randint(0, 99):02d} on {R.randint(2024, 2026)}-{R.randint(1, 12):02d}-"
        f"{R.randint(1, 28):02d}")
for _ in range(20):
    neg(f"bill INV-BULK-{R.randint(24, 33)}-{digits(14)}-{digits(6)}")
for _ in range(15):
    while True:
        n = digits(12, "23456789")
        if not verhoeff_valid(n):
            break
    neg(f"Batch {n} closed")
for _ in range(15):
    while True:
        n = R.choice("45") + digits(15)
        if not luhn_valid(n):
            break
    neg(f"Ticket {n} resolved")
for _ in range(10):
    bad = iban(False)
    neg("Draft " + bad[:2] + f"{(int(bad[2:4]) + 1) % 100:02d}" + bad[4:] + " (check digits wrong)")
for _ in range(10):
    neg(f"Ticket {digits(15, '2789')} raised")
for _ in range(10):
    neg(f"Doc {letters(3)}{R.choice('KMNQRSUVWXYZ')}{letters(1)}{digits(4)}{letters(1)} archived")

if __name__ == "__main__":
    positives = sum(1 for c in cases if c["group"] == "positive")
    OUT.write_text(json.dumps({"description": __doc__.strip().splitlines()[0], "cases": cases}, indent=1,
                              ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases ({positives} positive, {len(cases) - positives} negative) -> {OUT}")
