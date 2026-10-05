"""Is this a question about UAE VAT law (R8)?

Only questions the router already sends to the knowledge-answer path are checked, so this
mainly has to tell VAT rules apart from general accounting, app how-to and the user's own
figures. When it says no, the answer is exactly what AccuTax AI gives today.
"""
from __future__ import annotations

import re

# Words that only come up when the question is about VAT or FTA rules.
_VAT_TERMS = re.compile(
    r"\b(vat|value[- ]added tax|fta|federal tax authority|tax invoices?|simplified tax invoice|input tax|"
    r"output tax|reverse[- ]charge|zero[- ]rat\w*|standard[- ]rat\w*|exempt|exemption|designated zones?|"
    r"tax group|tax registration|trn|registration threshold|voluntary disclosure|tax return|tax period|"
    r"place of supply|date of supply|deemed supply|profit margin scheme|bad debt relief|apportionment|"
    r"going concern|disbursement|e-?invoic\w*|electronic invoic\w*|accredited service provider|"
    r"tourist refund|tax refunds? for tourists|refund of (vat|input tax)|executive regulation|"
    r"decree[- ]law|cabinet decision|ministerial decision|public clarification|vatp\d+|taxp\d+|"
    r"administrative penalt\w*|tax procedures|concerned goods|concerned services|digital currenc\w*|"
    r"precious metals|metal scrap|new[- ]residences?|mosques?)\b", re.I)

# Rule-shaped phrasing; together with a tax word it marks a law question even without "VAT".
_RULE_PHRASES = re.compile(
    r"\b(can we (claim|reclaim|recover|charge|use|issue|zero)|do we (need|have) to|must (we|i)|"
    r"is (it|this|the supply) (taxable|zero[- ]rated|exempt|subject)|who (accounts|pays|must|charges|can claim)|"
    r"what (is|are) the (rule|rules|penalt\w+|treatment|rate|deadline|time limit|criteria|conditions)|"
    r"how (do|should) (we|i) (treat|account|convert|value|calculate)|treatment of|time limit|deadline)\b", re.I)
_TAX_WORD = re.compile(r"\b(tax|vat|penalt\w+|refund|invoice|supply|supplies|registration|registered)\b", re.I)

# Asking for the user's own numbers or records: that is a data question, never a law question.
_OWN_DATA = re.compile(
    r"\b(show|list|display|give me|pull|fetch|export|download)\b.{0,40}\b(my|our|the)\b"
    r"|\bhow much (vat|tax|output tax|input tax)\b.{0,30}\b(did|do|have|has|will)\b.{0,10}\b(we|i|you)\b"
    r"|\b(my|our)\s+(total|vat payable|vat liability|vat return for|revenue|sales|expenses|invoices for|balance|outstanding)\b"
    r"|\b(this|last|current|previous|next)\s+(month|quarter|year|period)'?s?\b.{0,40}\b(vat|tax)\s+(payable|liability|due|amount|collected|paid)\b"
    r"|\b(vat|tax)\s+(payable|liability|collected|paid)\b.{0,30}\b(this|last|current|previous)\s+(month|quarter|year|period)\b"
    r"|\bwhich (customers|suppliers|vendors|invoices|bills)\b|\btop \d+\b"
    r"|\b(INV|BILL|PO|CN|DN)[-\s]?\d+|\b(invoice|bill|order|receipt)\s*(no\.?|number|#)\s*\d+|#\d{2,}"
    r"|\b(balance sheet|trial balance|profit and loss|p&l|cash flow statement|dashboard)\b", re.I)

# A period on its own ("last quarter") usually means the user's figures, but law questions name one
# too: "We found an error in last quarter's return ... voluntary disclosure, or correct it in the next
# return?" was sent to the data path. The period blocks the question only without a law-only term.
_PERIOD = re.compile(r"\b(last|this|previous|current|past)\s+(week|month|quarter|year|financial year)\b", re.I)
# Terms that name a VAT rule or procedure, never a figure. Generic words such as "VAT", "tax return" or
# "penalty" stay out: "our VAT return last quarter" asks for the user's own return.
_LAW_ONLY = re.compile(
    r"\b(voluntary disclosures?|bad debt relief|profit margin scheme|deemed supply|reverse[- ]charge|"
    r"apportionment|administrative penalt\w*|going concern|place of supply|date of supply|"
    r"executive regulation|decree[- ]law|cabinet decision|ministerial decision|public clarification|"
    r"time limit)\b", re.I)


# How to do something in the Accutax app ("How do I generate a VAT return in Accutax?") is app
# guidance, answered from the app guide, not from FTA law.
_APP_HOW_TO = re.compile(
    r"\b(in|on|using|with|inside|from)\s+(accutax|the app|this app|the system|the software)\b"
    r"|\bwhere (is|are|can i find|do i find|can i see)\b"
    r"|\bhow (do|can) i (create|generate|add|record|enter|set up|setup|configure|find|open|export|print|"
    r"download|upload|edit|delete|post|reconcile|import)\b", re.I)


def is_vat_law_question(question: str) -> bool:
    q = question.strip()
    if not q or _OWN_DATA.search(q) or _APP_HOW_TO.search(q):
        return False
    if _PERIOD.search(q) and not _LAW_ONLY.search(q):
        return False
    if _VAT_TERMS.search(q):
        return True
    return bool(_RULE_PHRASES.search(q) and _TAX_WORD.search(q))
