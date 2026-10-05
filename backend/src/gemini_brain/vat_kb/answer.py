"""What the answer model is given for a VAT law question (R9, R10) and the Sources block shown
under the answer (R11)."""
from __future__ import annotations

import re
from datetime import date

from gemini_brain.vat_kb.calc import calculations_for, monthly_equivalents
from gemini_brain.vat_kb.retrieval import SearchResult

VAT_ANSWER_RULES = """
UAE VAT KNOWLEDGE BASE
This question is about UAE VAT law. Official Federal Tax Authority (FTA) documents relevant to it are
given below as numbered SOURCES. For this answer, the following rules take priority over the general
guidelines above.

Accuracy
1. Use only the SOURCES. Never invent article numbers, decision numbers, dates, amounts or rates.
   Never state a benefit, risk, penalty effect or option that the SOURCES do not state. When a source
   gives a fallback ("in the event that ...", "if there is no ..."), present it as that fallback, not
   as an option the user may choose.
2. If the SOURCES do not answer the question, say so in one sentence and do not guess.
3. When sources conflict, or one amends or replaces another, apply the most recent one and say from
   which date the rule applies. Older guides can describe rules that were changed later: if an older
   guide and a newer decision or public clarification disagree, follow the newer one and say the rule
   changed. Describe an earlier (replaced) rule only if a source marked "Law or decision" states it:
   FTA guidance can describe rules that were already out of date when it was published, so never take
   a previous rate, amount or penalty from guidance alone.
4. Keep the issue date and the effective date of a document apart; a rule applies from its effective
   date. If a document explains a law or decision, give the date the law or decision took effect.
5. Name the party each rule applies to (supplier, recipient, reseller, importer, tax group member,
   director). Never move a condition from one party to another. When a source sets different rules
   or dates for different groups (for example by revenue or for government entities), give each group
   its own rule and date; never apply one group's rule or date to everyone.
6. Apply thresholds, exceptions and exclusions exactly as written, including any limit on an exception.
   Say plainly when something is outside the scope of VAT or is not a supply at all. Compare a
   threshold only with the figure the source measures it against (for example a difference between
   two amounts, or revenue). If the user has not given that figure, state the threshold as a
   condition ("if the difference exceeds AED 250,000, ...") and give no verdict on it; never compare
   it with a different figure from the question or from CALCULATIONS.
7. If the answer depends on facts the user has not given, state the condition ("zero-rated only if ...")
   instead of a yes/no verdict.
8. For any figure, use the numbers in CALCULATIONS when given (including the monthly equivalent of a
   yearly rate); do not do your own arithmetic. Amounts the user gives include VAT unless they say otherwise.
   When the question gives amounts, show the working: the formula from the source, the user's figures
   and each result from CALCULATIONS, for example "AED 100,000 x 60% = **AED 60,000**". When the
   question gives no amounts but the rule is a formula, state the formula in words; do not make up an
   example with your own figures.
9. When a source describes changes made by more than one law or decision, attribute each change only
   to the law or decision the source says made it, and when asked what a law changed, list every
   change the sources give for that law (including repealed articles and new time limits). A summary
   list of amended articles that covers several laws does not show which law made which change: use
   the detailed section for each law ("introduced by ... and effective from ...").
10. Correcting an error in a past return: give the correction route exactly as the sources set it out,
    including which return the correction goes in (for example "whichever is earlier") and when a
    voluntary disclosure is needed instead. The correction is included in a later return; never write
    "amend", "amended return" or "submit the corrected return", because a submitted return is not
    changed.
    Wrong: "**Step 2 - Amend the return:** ..." / "**Action:** Amend the next VAT return to include the
    AED 8,000."
    Right: "**Step 2 - Include the correction:** ..." / "**Action:** Include the AED 8,000 in your next
    VAT return and pay it by that return's due date."

Depth and layout: write as an experienced UAE tax adviser explaining the rule to a finance manager.
11. Always open with two or three sentences that answer the question directly, before any heading or
    bullet; never start the answer with a bullet. Put the decisive rate, amount, date or treatment in
    bold.
12. Then a bold heading that fits the question, for example "**How to calculate the value**",
    "**Key points to apply the penalty**", "**Conditions for zero-rating**" or "**Who accounts for
    the VAT**". Under it:
    - for a procedure or a calculation, numbered steps written as "**Step 1 - Open market value:** ...";
    - otherwise bullets that start with a short bold label such as "**Rate:**", "**Timing:**",
      "**Scope:**", "**Conditions:**" or "**Exception:**".
    Each step or bullet is one or two complete sentences that explain the rule and how it applies to
    the user's situation, naming the article, decision or clarification when the source gives it. Put
    cases that differ (for example voluntary disclosure and tax assessment) as sub-bullets.
13. Where it helps, add one more short bold section such as "**Exceptions**", "**What changed**" or
    "**Documents to keep**" (the records or evidence the sources require). Write "**What changed**"
    only when a source states that the rule changed and names the law or decision that changed it and
    the date it took effect. Name that decision exactly as the source does (for example "Article 10
    was amended by Cabinet Decision No. 17 of 2026"), not as an amendment to the decision it amended.
    An amendment did not introduce a rule, threshold or amount that an older source already states; a
    decision that restates or reissues an existing rule did not introduce it. The issue date of a
    consolidated text ("... and its amendments") is not the date of any change. These sections are
    optional: leave one out rather than fill it without a source.
14. End with one line starting "**Action:**" that says what the user should do next. Always fill it:
    one or two complete sentences with a concrete step for the user's situation. Never leave
    "**Action:**" empty or end the answer on it.
15. Usually 200 to 400 words and never more than 550; shorter for a simple yes/no or a single figure.
    Leave out points the question does not need. No tables unless asked, and no headings other than
    bold lines like those above.
16. If a source applies the same rule to another situation the user is likely to meet (for example the
    same penalty for a related violation), add it as one bullet.

Citations
17. Put the source number in square brackets after the sentence, step or bullet it supports, e.g. [1]
    or [2][3]. Cite a source once per paragraph, step or bullet, not after every sub-bullet, and never
    cite CALCULATIONS.
18. Do not write a list of sources, a "Sources:" line or any web address; the source list is shown under
    your answer automatically.

Tone
19. Start directly with the answer: the first sentence states the answer itself. Do not greet,
    introduce yourself, name yourself or Accutax, or write a lead-in such as "Let me explain".
    Wrong: "Let me explain the VAT treatment of both sales." / "Here are the requirements:"
    Right: "The first sale is **zero-rated** and the resale in year 5 is **exempt** [1]."
20. End after the "Action" line: no closing summary or remark.
21. Do not mention Accutax screens, menus, buttons, features or plans in this answer, and do not offer
    to show how to do it in Accutax. Filings with the FTA (registration, VAT returns, voluntary
    disclosures, refund claims) are made on the FTA's EmaraTax portal.
"""


def _body(chunk_text: str) -> str:
    """Chunk text without its header line (the header is printed once per source)."""
    return chunk_text.split("\n", 1)[1] if "\n" in chunk_text else chunk_text


def _kind(chunk) -> str:
    return "Law or decision" if chunk.doc_type == "statute" else "FTA guidance"


def _calculations(question: str, result: SearchResult) -> list[str]:
    """Exact figures for the answer: from the user's amounts, and the monthly rate of any yearly rate
    the sources charge monthly (14% per annum -> 1.17% per month)."""
    try:
        lines = calculations_for(question)
    except Exception:   # a figure we cannot parse must not cost the answer its sources
        lines = []
    try:
        lines += monthly_equivalents(h.chunk.text for h in result.hits)
    except Exception:
        pass
    return lines


def _source_line(n: int, chunk) -> str:
    parts = [f"[{n}] {chunk.title}", _kind(chunk)]
    if chunk.issue_date and chunk.issue_date.upper() != "NA":
        parts.append(f"issued {chunk.issue_date}")
    if chunk.effective_date:
        parts.append(f"effective {chunk.effective_date}")
    return " | ".join(parts)


def prompt_block(question: str, result: SearchResult) -> str:
    """System-prompt section: answer rules, exact calculations, and the sources grouped by number."""
    numbers = {chunk.url: n for n, chunk in enumerate(result.sources(), 1)}
    grouped: dict[int, list] = {}
    firsts: dict[int, object] = {}
    for hit in result.hits:
        n = numbers[hit.chunk.url]
        grouped.setdefault(n, []).append((hit.chunk.position, _body(hit.chunk.text)))
        firsts.setdefault(n, hit.chunk)

    today = date.today()
    lines = [VAT_ANSWER_RULES,
             f"Today's date is {today:%d %B %Y}. A rule whose effective date is on or before today is the "
             "current law; describe earlier rules only as the previous position.", ""]
    calcs = _calculations(question, result)
    if calcs:
        lines.append("CALCULATIONS (exact):")
        lines += [f"- {c}" for c in calcs]
        lines.append("")
    lines.append("SOURCES:")
    for n in sorted(grouped):
        lines.append(_source_line(n, firsts[n]))
        lines += [body for _, body in sorted(grouped[n], key=lambda pb: pb[0])]   # in document order
        lines.append("")
    return "\n".join(lines)


EXCERPT_CHARS = 320
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?")


def _excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    body = " ".join(_body(text).split())
    if len(body) <= limit:
        return body
    cut = body[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + " …"


def evidence_numbers(question: str, result: SearchResult) -> list:
    """Every number in the passages given to the model, plus the exact calculations. The answer's
    figures (Article 48, AED 375,000, 14%) come from law text, not data rows, so the grounding check
    must look here instead of reporting them as unverified."""
    found = set()
    texts = [h.chunk.text for h in result.hits]
    texts += [f"{h.chunk.issue_date} {h.chunk.effective_date}" for h in result.hits]
    texts += _calculations(question, result)
    for text in texts:
        for whole, frac in _NUMBER_IN_TEXT.findall(text):
            try:
                found.add(float(whole.replace(",", "") + (f".{frac}" if frac else "")))
            except ValueError:
                continue
    return sorted(found)


_LEAD_IN = re.compile(
    r"^(accutax ai here\b|let me (explain|break|walk|outline|clarify|summari[sz]e)\b|here(?:'s| is| are)\b"
    r"|i'?ll (explain|outline)\b|based on [^\n]{0,120}?,\s*here(?:'s| is| are)\b)", re.I)
_CLOSING = re.compile(r"^(this (is|represents|marks)\b|overall\b|in summary\b|in short\b|for businesses\b)", re.I)
_CITED = re.compile(r"\[\d{1,2}\]")
_SELF_INTRO = re.compile(r"^\s*(hi|hello)?[,!.]?\s*accutax ai here[.!:,]?\s*", re.I)   # "Accutax AI here. The value is ..."
# Rule 20 forbids Accutax screens and menus in a VAT law answer; a smaller model still wrote
# "You can view your VAT liability in Accutax under **Taxes > VAT Summary**". Drop that sentence.
_APP_SENTENCE = re.compile(r"(?<=[.!?:])[ \t]*[^.!?\n]*\bAccutax\b[^.!?\n]*[.!?]|^[ \t]*[^.!?\n]*\bAccutax\b[^.!?\n]*[.!?][ \t]*",
                           re.I | re.M)
_CALC_CITE = re.compile(r"\s*\[CALCULATIONS?\]", re.I)   # the model now and then cites the calculations
_LABELLED = re.compile(r"^(\*\*|[-*+] |\d+[.)] |#)")   # "**What to do:** ...", list items, headings


def tidy_answer(answer: str) -> str:
    """Drop a lead-in first paragraph ("Let me explain ...", "Based on ..., here are ...:") and an uncited
    closing remark ("This is a major change ..."). The prompt asks for neither, but the model still adds
    them now and then; neither carries a fact (neither has a citation)."""
    answer = _SELF_INTRO.sub("", _CALC_CITE.sub("", answer or ""))
    answer = _APP_SENTENCE.sub("", answer)
    paras = [p for p in re.split(r"\n\s*\n", answer.strip()) if p.strip()]
    if len(paras) > 1 and "\n" not in paras[0].strip() and _LEAD_IN.match(paras[0].strip()) \
            and not _CITED.search(paras[0]):
        paras = paras[1:]
    last = paras[-1].strip() if paras else ""
    if len(paras) > 1 and "\n" not in last and not _CITED.search(last) \
            and (_CLOSING.match(last) or not _LABELLED.match(last)):
        paras = paras[:-1]
    return "\n\n".join(paras) if paras else answer


def sources_block(question: str, result: SearchResult) -> dict:
    """The cited FTA documents for the citation chips and the sources list under the answer.

    Built from the document list and the retrieved passages, never from model output, so a title
    or link cannot be invented. `text` is a plain-markdown version for clients that do not know
    this block type yet."""
    best: dict = {}
    for hit in result.hits:                       # hits are best-first: keep each document's best passage
        best.setdefault(hit.chunk.url, hit)
    sources, rows = [], []
    for n, chunk in enumerate(result.sources(), 1):
        issued = chunk.issue_date if chunk.issue_date and chunk.issue_date.upper() != "NA" else ""
        sources.append({
            "n": n,
            "title": chunk.title,
            "url": chunk.url,
            "kind": _kind(chunk),
            "category": chunk.category,
            "issued": issued,
            "effective": chunk.effective_date,
            "excerpt": _excerpt(best[chunk.url].chunk.text),
        })
        rows.append(f"{n}. [{chunk.title}]({chunk.url})" + (f" (issued {issued})" if issued else ""))
    return {
        "type": "fta_sources",
        "authority": "Federal Tax Authority",
        "jurisdiction": "UAE",
        "sources": sources,
        "evidence_numbers": evidence_numbers(question, result),
        "text": "**Sources — Federal Tax Authority**\n\n" + "\n".join(rows),
    }
