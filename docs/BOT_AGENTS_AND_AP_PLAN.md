# Bots for more agents: make bots agent-agnostic, then build the AP bot

Status: Part A built 2026-11-03 (not committed or deployed; VM acceptance check A10.2 still to do). Part B not built.
Written 2026-11-02.

Part A as built differs from the text below in three small ways: the engine's position is split into `position`
(the raw tool call) and `summarize_position` (the box), so one call serves both the try run and the run summary;
`BotAgent` also carries `draft_prompt` and `fallback_template` (server side, not in `GET /bots/agents`); and the
browser keeps the agents in `botText.js` (`setBotAgents`, `agentMeta`) instead of passing an `agents` prop to every
page. The forwards left in A3 (`run._due_period`, `run.finding_detail`, `run._return_position`,
`inbox.vat_at_stake`) are still there; delete them in A10.
Related: `docs/VAT_BOT_IMPLEMENTATION_PLAN.md` (the VAT bot, built), `docs/ACCUTAX_BOTS_PLAN.md` (all bots),
`docs/ACCUTAX_AGENT_CATALOG.md` (the agent list), `docs/design/accutax-vat-bots.html` (the screens).

This plan is written so that a developer or a smaller model can build it one task at a time. Each task says what to
change, in which file, with which names, how to test it, and when it is done. Do the tasks in order and run the tests
after each one.

---

## 0. Read this first

### 0.1 Why two parts

The bots code was built for one agent. `agent` is fixed to `"vat"` in the draft, the watches are a VAT list, the
review engine is `review_vat`, and about a hundred strings in the screens say "VAT". A second agent would mean
copying all of it.

- **Part A** makes bots agent-agnostic. VAT must behave exactly as it does today afterwards. No new agent yet.
- **Part B** adds the second agent, **AP / Bills**, on top of Part A. After Part A, each later agent (AR, Compliance,
  Reporting, Invoice, Reconciliation, Delivery Challan) is mostly a list of checks, templates and wording.

Part A is deployable on its own. Do not start Part B until Part A's acceptance check (A10) passes.

### 0.2 What AP needs that VAT did not

| | VAT bot | AP bot |
|---|---|---|
| Engine | `review_vat` (purchases and sales) | `review_vat` for duplicates, missing details and unusual bills; new code for overdue, due soon and new supplier |
| "Deadline" schedules | before the VAT return, after a period | none; weekly, monthly and a new daily |
| Money figure | VAT at stake on the return | amount that could be paid twice; what is due soon |
| Position box | the VAT return (output, input, net, due date) | payables: due in the next 7 days, overdue |
| Per-document monitor | bills and sales invoices | bills only |
| Time-based checks | none | overdue and due soon (a bill gets worse as days pass, with no new document) |

### 0.3 Rules for whoever builds this

1. **No database change.** `ai_bots.agent` and `ai_bot_findings.detail` already exist. If you think you need DDL, stop and ask.
2. **Read only.** A bot never pays, edits or deletes a bill. `Rules.change_records` stays `"never"` for every agent.
3. **Figures come from tools and code, never from the model.**
4. **Tenant safety.** Organizations come from the user's `allowed_org_ids`, checked on the server. Every Cube query goes
   through `cube_client.load(..., organization_ids=...)`.
5. **VAT must not change.** Every existing bot test must pass unchanged, except where a function gains an `agent`
   parameter (add it with a default of `"vat"` so old calls keep working).
6. **Match the code around you**: same naming, short module docstrings, comments only where the reason is not obvious.
7. **Run the tests** after each task (commands in 0.4). Commit only your own files on `deploy/vm-3535`.
8. **No paid model run per task.** One paid evaluation at the end (task B11).

### 0.4 Commands

Backend tests (from `Gemini_Brain_Accutax/backend`):

```bash
.venv/Scripts/python -m pytest tests/unit/test_bot_*.py -q -p no:cacheprovider
```

Full suite: `.venv/Scripts/python -m pytest tests/unit -q -p no:cacheprovider`. Three tests in `test_cube_model.py`
already fail and are not related to bots.

Frontend build (from `Gemini_Brain_Accutax/frontend`): `npm run build`. Local preview: `gemini-brain-demo-backend` and
`gemini-brain-frontend` from `.claude/launch.json`. The demo backend has no database or Cube, so feed the bot pages
sample answers from the browser console, as in earlier checks.

### 0.5 Words

| Word | Meaning |
|---|---|
| Agent | The job in code (`agent/profiles.py`): `vat`, `ap`. Chat agents and bot agents use the same keys. |
| Bot agent | What a bot needs from an agent: its checks, templates, engine and wording. New: `bots/agents.py`. |
| Watch | One thing a bot checks. Maps to rules the engine understands. |
| Document watch | Can be checked on one saved document (duplicate, missing details, new supplier). The monitor runs these. |
| State watch | Depends on today's date, not on a new document (overdue, due soon). Only scheduled runs check these. |
| Finding | One problem. Has a stable `key`, stored in `ai_bot_findings`. |

---

## Part A: bots that work for any agent

### A1. The registry (`bots/agents.py`)

**Goal.** One place that says what each bot agent is. Everything else asks it.

```python
@dataclass(frozen=True)
class BotAgent:
    key: str                          # "vat", "ap"
    name: str                         # "VAT", "Accounts payable"
    watches: Tuple[Watch, ...]
    templates: Tuple[Template, ...]
    schedule_kinds: Tuple[str, ...]   # which of weekly, monthly, daily, before_deadline, after_period_end
    engine: Engine
    credits_per_run: int
    copy: Dict[str, Any]              # words for the screens, see A2
    columns: Tuple[Dict[str, Any], ...]   # the bills table in the inbox: [{"key": "number", "label": "Bill"}, ...]

_AGENTS: Dict[str, BotAgent] = {}
def register(agent: BotAgent) -> None
def get(key: str) -> BotAgent        # KeyError for an unknown key
def all_agents() -> List[BotAgent]
def public(agent: BotAgent) -> Dict[str, Any]   # what GET /bots/agents returns: everything except the engine
```

`Engine` is a `Protocol` (in the same file):

```python
class Engine(Protocol):
    def calls(self, watch_ids: Sequence[str], sensitivity: str) -> List[Dict[str, Any]]:
        """The checks to run, one dict per engine call. Merges the rules of all chosen watches."""
    def run(self, call: Dict[str, Any], *, org_ids: Sequence[int], period: Optional[Dict[str, str]], subject: str,
            deadline: float, exclude: AbstractSet[str], unlimited: bool) -> Dict[str, Any]:
        """One call's result in review_vat's shape: side, period, bills_checked, organizations, groups, findings,
        groups_found, findings_found, groups_by_rule, findings_by_rule."""
    def period(self, draft: BotDraft, subject: str) -> Optional[Dict[str, str]]:
        """The period a scheduled run checks, or None for the engine's own default window."""
    def position(self, org_ids: Sequence[int], subject: str, deadline: float) -> Optional[Dict[str, Any]]:
        """The box under the intro: {"kind": "vat_return" | "payables", ...}. None when it cannot be worked out."""
    def stake(self, rule: str, detail: Dict[str, Any]) -> float:
        """Money the finding puts at risk; 0 when it is not an amount."""
    def detail(self, item: Dict[str, Any], side: str) -> Dict[str, Any]:
        """What the inbox card keeps for a finding (side, why, level, currency, bills)."""
```

Import `Watch` from `watches.py` and `Template` from `templates.py` for typing only (use `TYPE_CHECKING`), to avoid
import loops. `register` is called at the bottom of `bots/agents_vat.py` (A3) and later `bots/agents_ap.py` (B2); import
both from `bots/__init__.py`'s lazy loader or from `agents.get` on first use.

**Tests** (`tests/unit/test_bot_agents.py`): `get("vat")` works; `get("nope")` raises `KeyError`; `public()` has no
engine and is JSON-serialisable; every agent's watch ids are unique within the agent.

**Done when** the tests pass and no other file imports the registry yet.

### A2. What goes in `copy`

The words that are VAT-specific today, per agent. Keys (use exactly these; the screens read them):

| Key | VAT value (today's text) |
|---|---|
| `label` | "VAT agent" |
| `default_name` | "VAT Watch" |
| `lede` | "Assistants that check your books on a schedule…" (the Bots home line stays agent-neutral; this is the agent's own) |
| `create_hint` | placeholder text of the sentence box |
| `examples` | the three example chips on the Create page |
| `tip` | "Tip: under any VAT answer in chat, press Make this a bot…" |
| `stake_label` | "At stake this quarter" |
| `stake_note` | "VAT that could be wrong on your return if these stay unfixed: …" |
| `problem_word` | "VAT problems" |
| `readonly_rule` | "Change bills, invoices or file a VAT return" |
| `notes_placeholder` | "Rent from Al Noor Properties is billed in two parts; don't flag those as duplicates" |
| `watch_bar` | `{watch_id: "Recorded twice", ...}` short names for the preview bars |
| `rule_titles` | `{rule: "Bill may be recorded twice", ...}` and `sales_rule_titles` for the sales side |
| `dismiss_labels` | `{rule: "It's zero-rated"}`; default "Not a problem" |
| `watch_short` | `{watch_id: "duplicates"}` for the one-line summary |
| `summary_cards` | which cards the Bots home shows, see A8 |

Move the strings that are hardcoded today in `frontend/src/components/bots/botText.js` (`WATCH_SHORT`, `WATCH_INFO`,
`WATCH_BAR`, `TEMPLATE_META`, `RULE_LABEL`) and `BotInbox.jsx` (`SALES_TITLE`) into the VAT agent's `copy`. Do not
change a single word of them.

### A3. VAT as the first registered agent (`bots/agents_vat.py`)

**Goal.** Wrap what exists. No behaviour change.

- `VatEngine.calls` is today's `watches.review_calls`.
- `VatEngine.run` calls `vat_review.tool.run({**call, "period": period or {"preset": "this_quarter"}, "limit": 50}, ...,
  exclude=exclude, unlimited=unlimited)`.
- `VatEngine.period` is `run._due_period` (moves here).
- `VatEngine.position` is `run._return_position` plus the `vat_return.tool.run({"which": "next_due"})` call from
  `preview.try_period`; its dict gets `"kind": "vat_return"`.
- `VatEngine.stake` is `inbox.vat_at_stake`.
- `VatEngine.detail` is `run.finding_detail`.
- `schedule_kinds = ("weekly", "monthly", "before_deadline", "after_period_end")` (daily is added in A7 for AP only).
- `credits_per_run = 90`.

Keep `run._due_period`, `run.finding_detail` etc. as one-line forwards for now so old tests pass; delete them in A10.

**Tests:** `tests/unit/test_bot_agents.py`: `VatEngine.calls(["duplicate_bill","vat_rate_wrong","sales_vat_wrong"],
"balanced")` equals the output `watches.review_calls` gave before (copy the expected value from
`tests/unit/test_bot_watches.py`).

### A4. Make the modules ask the registry

Add an `agent: str = "vat"` parameter, default `"vat"`, to each function below, and look the agent up with
`agents.get(agent)`. Callers that have a draft or a bot pass `draft.agent` / `bot["agent"]`.

| File | Function | Change |
|---|---|---|
| `bots/schema.py` | `BotDraft.agent` | `Literal["vat"]` becomes `str`, with a `field_validator` that rejects keys not in `agents.all_agents()`. Default stays `"vat"`. `name` default stays "VAT Watch"; `validate` (below) sets the agent's `default_name` when the name is empty. |
| `bots/watches.py` | `all_watches`, `get`, `default_ids`, `review_calls`, `watch_for_rule` | take `agent`; read from `agents.get(agent).watches`; `review_calls` becomes `agents.get(agent).engine.calls(...)`. `Watch` gains `desc: str`, `bar: str` and `kind: Literal["document","state"] = "document"`. |
| `bots/templates.py` | `all_templates`, `get` | take `agent`; templates live on the agent. |
| `bots/validate.py` | `validate` | checks 3 and 4 use the draft's agent's watches; the empty-name check uses `default_name`; check 6 rejects a schedule kind not in the agent's `schedule_kinds` ("This bot cannot run 'before the VAT return'", fixed=False). |
| `bots/cost.py` | `CREDITS_PER_RUN` | replaced by `agents.get(draft.agent).credits_per_run`. |
| `bots/preview.py` | `try_period` | loop over `engine.calls(...)` and call `engine.run(...)`; return `position` from `engine.position(...)` under the key `"position"` and keep the old `"return"` key (VAT only) so the intro keeps working until A8. |
| `bots/run.py` | `run_bot`, `_run`, `_current_findings` | `_due_period` and `_return_position` become `engine.period` / `engine.position`; `finding_detail` becomes `engine.detail`; summary stores `position` and keeps `return_position` for VAT. Credits use `credits_per_run`. |
| `bots/monitor.py` | `handle_document_event` | per bot: `agent = agents.get(bot["agent"])`; build calls with `agent.engine.calls`; **skip watches whose `kind` is `"state"`**; run with `engine.run`. A bot whose agent has no purchases-side call ignores `invoice.saved`, and the reverse. |
| `bots/inbox.py` | `enrich`, `build` | `enrich(f, agent)`: `at_stake` from `engine.stake`; titles are applied in the browser from `copy`. `build` receives the bots so it can look up each finding's agent by `bot_id`. |
| `bots/draft.py` | `from_sentence`, `_SYSTEM_PROMPT`, the tool schema | take `agent`; the prompt text, the watch enum and the fallback template come from the agent. VAT's prompt text must stay byte-identical (move it into `copy["draft_prompt"]`). |
| `api/bot_routes.py` | see A5 | |

**How to find every call site:** `grep -rn "watches\.\(review_calls\|get\|all_watches\|default_ids\|watch_for_rule\)\|templates\.\|vat_review_tool\|vat_return_tool\|CREDITS_PER_RUN" backend/src/gemini_brain/bots backend/src/gemini_brain/api/bot_routes.py`.

**Tests:** the whole existing bot suite passes. Add `tests/unit/test_bot_agents.py::test_defaults_keep_vat_behaviour`:
calling each changed function with no `agent` gives the same result as before.

### A5. API changes (`api/bot_routes.py`, `api/models.py`)

- New `GET /api/v1/bots/agents`: `{"agents": [public(a) for a in all_agents()]}`. The browser loads this once.
- `GET /templates` and `GET /watches` already take `?agent=`; stop special-casing `"vat"` (`if agent != "vat"`): unknown
  agent is a 404.
- `POST /draft/from-template`, `/draft/from-sentence`, `/draft/from-chat`, `/draft/check`, `/try`: request models gain
  `agent: str = "vat"`; `from-chat` no longer rejects non-VAT agents, it rejects keys not in the registry (400).
  `/draft/check` and `/try` read the agent from the draft.
- `POST /bots` and `PATCH /bots/{id}`: the draft's agent is validated by the schema; `store.create` already writes
  `agent`.
- `GET /bots/summary`: returns `cards` per agent present in the user's bots (see A8) instead of the fixed VAT fields;
  keep the old fields (`open_problems`, `next_return`, `credits`, `next_run`) so the current Bots home works until A8.
- `GET /bots/inbox`: unchanged shape; each finding gains `agent`.

**Tests:** `tests/unit/test_bot_routes.py`: `GET /agents` lists `vat`; `?agent=nope` is 404; a draft with `agent="nope"`
is 422.

### A6. Daily schedule kind

AP needs "bills due in 3 days" checked every day.

- `schema.Schedule.kind` gains `"daily"`; no extra field (time only).
- `schedule.next_runs`: for `daily`, the next `count` days at `time`, strictly after `now`.
- `cost._raw_runs_per_month`: daily = 22 (weekdays only is not modelled; use 30). Use 30.
- `validate.py`: a `daily` schedule needs only a valid time.
- `frontend/src/components/bots/botText.js`: `scheduleLine` returns "Every day at 08:00"; `scheduleChip` returns
  "Daily · 08:00".
- `BotBuilderPage.jsx`: `KINDS` gains `['daily', 'Every day']`; the row shows only the time. The kind list shown comes
  from the agent's `schedule_kinds` (A8).

**Tests:** `test_bot_schedule.py`: daily from Friday 30 Oct 2026 10:00 with time 08:00 gives 31 Oct, 1 Nov, 2 Nov;
`test_bot_cost.py`: one daily schedule, one org, is 30 × 90 = 2700.

### A7. Stop the poller assuming VAT

`bots/poller.py` is already agent-neutral: it hands new bills and invoices to the monitor per organization. Only
`store.active_org_ids` matters. No change, but add one test that a bot with `agent="ap"` is returned by
`store.list_active_bots_for_org` (it filters by status and org, not agent).

### A7a. Findings keep their rule when it changes

`store.upsert_findings` updates `state`, `amount`, `detail` and `last_seen_run` on a key it has seen, but not `rule`.
AP needs a bill's finding to move from "due soon" to "overdue" under the same key (B3). Add `rule = EXCLUDED.rule` to
the `ON CONFLICT ... DO UPDATE SET` list. **Test:** upsert the same key twice with different rules and read the second
back.

### A8. Frontend: stop hardcoding VAT

The screens read the agent's words and checks from `GET /bots/agents`.

1. `services/bots.js`: `fetchBotAgents(token)`.
2. `App.jsx`: load agents once when signed in (`botAgents` map by key); pass `agents={botAgents}` to `BotsHome`, `BotCreate`,
   `BotBuilderPage`, `BotDetail`, `BotInbox`, `BotIntroMessage`. Until it loads, render with the VAT copy bundled as a
   fallback so the pages never show empty (copy `agents_vat.py`'s `copy` into a `DEFAULT_AGENT` constant in `botText.js`).
3. `botText.js`: delete `WATCH_INFO`, `WATCH_BAR`, `TEMPLATE_META`, `RULE_LABEL`, `WATCH_SHORT`, `DELIVER_LABEL` stays.
   `botSummary(draft, agent)`, `scheduleChip`, `inShort(draft, agent)` take the agent meta.
4. `BotCreate.jsx`: when more than one agent exists, a row of agent chips above the sentence box ("VAT", "Accounts
   payable"); the choice sets `agent` on `createDraftFromSentence` / `createDraftFromTemplate` and the templates list
   (`fetchBotTemplates(agent)`). Placeholder, examples and tip come from the chosen agent's `copy`.
5. `BotBuilderPage.jsx`: the "VAT agent" pill, `KINDS` options (filter by `schedule_kinds`), the watch groups (group by
   `side`; titles "Bills you received" / "Invoices you sent" for sides that exist), the read-only rule text, the notes
   placeholder, the preview bars, all from the agent. The "Before the VAT return" day chips stay as they are, shown only
   when the kind is offered.
6. `BotInbox.jsx`: titles from `copy.rule_titles`, dismiss labels from `copy.dismiss_labels`, the stake card title and
   note from `copy.stake_label` / `copy.stake_note`, table columns from the agent's `columns`. The filter by bot already
   works for several agents.
7. `BotsHome.jsx`: the four cards come from `summary.cards` (list of `{label, value, note, tone}`). VAT keeps its four.
   Template cards show the templates of every agent the user can create (one list, grouped by agent name).
8. `BotIntroMessage.jsx`: the stat tiles and the position box depend on `position.kind`: `vat_return` is today's box;
   `payables` is B9.
9. `Sidebar.jsx` and `BotDetail.jsx`: the pill text from `copy.label`.

**Done when** the frontend builds, the VAT flow looks exactly as before in the local preview (compare the screenshots
from the earlier phases), and `grep -rn "VAT" frontend/src/components/bots/*.jsx` finds only strings that belong to the
VAT-return schedule kind and to the intro's VAT-return box.

### A9. Backend `summary` cards

`GET /bots/summary` builds `cards` per agent from the user's bots. For VAT, the four cards that exist today:
next run, open problems (new, duplicates and other), next VAT return, bot usage. Put the card builder behind the
registry: `BotAgent.summary_cards(bots, findings, ...)`. A fifth engine method is not needed; use a plain function
field on `BotAgent`.

### A10. Acceptance check for Part A and clean-up

1. All bot tests pass; the full suite has only the three known Cube failures.
2. On the VM, after deploying: an existing VAT bot's page, builder, inbox and intro look and behave as before; Run now
   on org 27 gives the same counts as before Part A (September: 102 bills, 102 invoices, 24 duplicate groups).
3. Delete the forwards left in A3.
4. Update `docs/VAT_BOT_IMPLEMENTATION_PLAN.md` with one line pointing here.

---

## Part B: the AP / Bills bot

### B1. The checks (watches)

Ids are per agent. Rules for the three reused watches are exactly VAT's purchases-side rules for those watches.

| Watch id | Label | kind | Rules | Default on |
|---|---|---|---|---|
| `duplicate_bill` | A bill looks recorded twice | document | `same_number`, `same_total_near_date`, `similar_vendor_same_day` | yes |
| `bill_overdue` | A bill is past its due date | state | `bill_overdue` | yes |
| `bill_due_soon` | A bill is due soon | state | `bill_due_soon` | yes |
| `new_supplier` | A large bill from a new supplier | document | `new_supplier_large` | yes |
| `unusual_bill` | A bill far above the supplier's usual amount | document | `large_for_supplier`, `large_one_off_supplier` (plus `round_amount` on "more") | yes |
| `bill_missing_details` | A bill is missing its supplier or number, or is dated in the future | document | `missing_supplier`, `missing_number`, `future_date` | no |

VAT-rate checks are not part of AP (they belong to the VAT bot).

**Thresholds by sensitivity** (no new UI; the sensitivity buttons already exist). Finance must sign these off, as for
the VAT thresholds:

| | fewer | balanced | more |
|---|---|---|---|
| Overdue: at least N days past due | 14 | 1 | 1 |
| Due soon: due within N days | 2 | 3 | 7 |
| New supplier: first bill at least | 20,000 | 5,000 | 1,000 (org currency) |

### B2. The AP agent (`bots/agents_ap.py`)

Register `BotAgent(key="ap", name="Accounts payable", ...)` with:

- `watches` from B1, with `desc` and `bar` text (short, sentence case).
- `schedule_kinds = ("daily", "weekly", "monthly")`.
- `credits_per_run = 90` for now (see decision 4).
- `copy`: default name "Bill Guard"; label "AP agent"; example chips: "Every Monday, check my bills for duplicates and tell
  me what is due this week", "Tell me every morning about bills that are overdue", "Warn me about a large bill from a
  new supplier"; `stake_label` "Could be paid twice"; `stake_note` "The total of the extra copies, if every group
  proves to be a duplicate."; `problem_word` "problems"; `readonly_rule` "Pay, change or delete bills"; `rule_titles`:
  `bill_overdue` "Bill is overdue", `bill_due_soon` "Bill is due soon", `new_supplier_large` "Large bill from a new
  supplier", plus the existing purchases titles; `dismiss_labels`: `bill_overdue` "I'll handle it", `bill_due_soon`
  "I'll handle it".
- `columns`: Bill, Date, Due, Supplier, Amount, Outstanding.
- `summary_cards`: next run; open problems; **due in the next 7 days** (amount and count); **overdue** (amount and count).

### B3. The engine: `bots/ap_engine.py` and `bots/payables_scan.py`

`ApEngine.calls(watch_ids, sensitivity)` returns up to three calls:

```python
{"kind": "review", "side": "purchases", "mode": "all", "rules": [...]}          # duplicates, missing, unusual
{"kind": "payables", "rules": ["bill_overdue", "bill_due_soon"], "overdue_days": N, "due_soon_days": M}
{"kind": "new_supplier", "min_amount": X}
```

Only calls with at least one chosen rule are returned.

`ApEngine.run` dispatches on `call["kind"]`:

- **review**: `vat_review.tool.run({**call_params, "period": period or <last 90 days>, "limit": 50}, ...)`. `period` for
  AP is the last 90 days as `{"start", "end"}` (`ApEngine.period` returns it; `semantic.periods.today_in` gives today).
- **payables** (`payables_scan.open_bills(org_ids, today, subject, deadline)`): one Cube query on the `payables` view
  (`semantic/model/views/payables.yml`): dimensions `payables.document_id`, `document_number`, `vendor_name`,
  `document_date`, `due_date`, `days_overdue`, `payables.currency`, `payables.organization_name`; measure
  `payables.outstanding`; filter `{"member": "payables.due_date", "operator": "beforeDate", "values": [<today +
  due_soon_days + 1 as YYYY-MM-DD>]}` (`cube_client.load` passes the query to Cube unchanged, so `filters` work; the
  payables view is "as of today" and has no `timeDimensions`, see its description in `payables.yml`). Use the same query
  shape and row cleaning as `vat_review/scan.py` (`_query`, `_row`, `cell_text`), and `limit` 5000 with a note when
  the scan hits it. Build one item per open bill whose due date is on or before today + `due_soon_days`:
  - `days_overdue >= overdue_days` → rule `bill_overdue`, severity `high` if 30 or more days, else `medium`;
  - else due within `due_soon_days` → rule `bill_due_soon`, severity `low`.

  **One item per bill, and the key does not include the rule**, so a bill that was "due soon" and becomes "overdue"
  is the same finding with a new rule (A7a), not a second one. Key:
  `sha256(f"ap|bill|{org_id}|{document_id}")[:24]`. When the bill is paid it leaves the payables view, so it is not
  found, so the run marks it `fixed` (the Inbox shows it under Fixed). Say "Paid or no longer due" in the Fixed list
  text for AP.
- **new_supplier** (`payables_scan.new_suppliers(org_ids, period, min_amount, ...)`): two queries on the `purchases`
  view. (1) bills in the period: `vendor_name`, `document_id`, `document_number`, `document_date`, measures
  `net_purchases`, `input_vat`. (2) the same vendors' bills before the period start: `vendor_name` with measure
  `bill_count`, `timeDimensions` date range from `2000-01-01` to the day before the period. A supplier is new when it has
  no row in (2). Item per new supplier whose first bill in the period is at least `min_amount` (amount plus VAT).
  Rule `new_supplier_large`; key `sha256(f"ap|new_supplier|{org_id}|{vendor_name}")[:24]` (one finding per supplier,
  however many bills it sent). Document the limit: a supplier that appears under a different name looks new.

Every item has the same fields as a `review_vat` finding: `key`, `rule`, `organization_id`, `organization_name`,
`currency`, `severity`, `why` (a sentence written in code from the numbers: "Gulf Agri Supplies bill B-0417 for AED
12,600.00 was due on 18 Oct and is 12 days overdue."), and `bill` with `document_id`, `document_number`,
`document_date`, `vendor_name`, `net_purchases`, `input_vat`, plus `due_date`, `outstanding`, `days_overdue`.

The merged result has `groups_found`, `findings_found`, `groups_by_rule` and `findings_by_rule` so `preview.try_period`
can count per watch without changes. Items from the payables and new-supplier calls go in `findings` (not `groups`).
`exclude` (the dismissed keys) is honoured by dropping excluded keys. `unlimited=True` returns every item.

`ApEngine.detail(item, side)`: the VAT engine's detail plus `due` and `outstanding` per bill (`finding_detail` copies
only some fields; extend it to copy `due_date`, `outstanding`, `days_overdue` when present, for every agent).

`ApEngine.stake(rule, detail)`: duplicates: the amount plus VAT of every copy after the first; every other rule: 0. The
"due soon" and "overdue" amounts are shown by the position box, not as "could be paid twice".

`ApEngine.position(org_ids, subject, deadline)`: one Cube query on `payables`: measures `outstanding` and
`overdue_amount`, `overdue_count`, `open_count`, with a filter for bills due in the next 7 days (a second query).
Return `{"kind": "payables", "currency": ..., "due_next_7_days": amount, "due_next_7_days_count": n, "overdue": amount,
"overdue_count": n, "organizations": k}`; the first organization's currency, and `organizations` counts the orgs the
figures cover (sum amounts only when all orgs share a currency, otherwise report the first org and say so).

**Tests** (`tests/unit/test_ap_engine.py`): fake `cube_client.load` with fixed rows (see how
`tests/unit/test_vat_review.py::fake_cube` does it). Cover: a bill 12 days overdue, one due in 2 days, one due in 20
days (not returned); sensitivity changes (`fewer` hides a 5-day-overdue bill); key does not change when a bill goes from
due soon to overdue; a paid bill (absent from the second run) is marked fixed by `run_bot`; new supplier with and
without earlier bills; the amount threshold; `exclude` drops a key; `unlimited` is passed through; position sums.

### B4. Templates

| id | title | summary | draft |
|---|---|---|---|
| `ap_weekly_check` | Weekly payables check | Every Monday: duplicates, bills due this week and what is overdue. | name "Bill Guard"; weekly Monday 08:00; defaults on; deliver inbox + bell |
| `ap_pay_run_prep` | Pay-run prep | Every Thursday: the bills due next week and anything overdue. | name "Pay-run Prep"; weekly Thursday 09:00; watches `bill_due_soon`, `bill_overdue`; sensitivity `more` (7-day window) |
| `ap_daily_watch` | Daily payables watch | Every morning: new overdue bills, duplicates and large bills from new suppliers. | name "Daily Bill Guard"; daily 08:00; all watches except `bill_missing_details`; deliver inbox + bell |

Cost shown on the card comes from `cost.monthly_credits` (daily is 30 runs, so the card will show about 2,700 credits a
month: see decision 4).

### B5. Sentence to draft for AP

`draft.py` builds the prompt from the agent. The AP prompt (store it in `copy["draft_prompt"]`) is the VAT prompt with
these mappings instead of the VAT ones: "overdue", "late bills", "unpaid" is `bill_overdue`; "due this week", "due
soon", "coming due" is `bill_due_soon`; "duplicates" is `duplicate_bill`; "new supplier", "new vendor" is
`new_supplier`; "unusual", "much bigger than usual" is `unusual_bill`; "every morning" and "daily" is a daily
schedule; "before the return" is not offered (tell the model not to produce `before_deadline` for AP). Same rules: never
invent an organization, never put figures in a field, at most three questions.

The sentence box on the Create page picks the agent from the agent chips (A8.4). If the user types a sentence about
VAT while the AP chip is selected, the model cannot produce VAT watches (the enum is AP's): the unmatched part goes to
`unclear` and becomes a question on the understood panel. That is acceptable.

### B6. "Make this a bot" from an AP answer

`ResponseView.jsx` shows the button only when `isVatAgent`. Replace `isVatAgent` with `botAgentKey`: the answer's agent
if it is in the list of agents that have bots (from `GET /bots/agents`), else none. The click passes that agent to
`createDraftFromChat` (A5 allows it). AP chat answers carry `agent: "ap"` already.

### B7. Inbox and bot page for AP

All from the agent's `copy` and `columns` (A8). Check these specifically:

- A finding with `bill_overdue` shows "Bill is overdue", the sentence, the bill table with Due and Outstanding, and the
  amount shown on the card is the outstanding amount (`at_stake` is 0 for these, so `BotInbox` falls back to
  `f.amount`; set `amount` to the outstanding when saving AP findings in `run._current_findings`).
- "Not a problem" is labelled "I'll handle it" for overdue and due-soon (`dismiss_labels`). Dismissing a due-soon or
  overdue finding keeps that bill quiet until the bot is told otherwise. Decision 3 asks whether it should come back
  when the bill becomes overdue.
- Bot page: Findings tab, What it learned, Settings: nothing changes.
- Inbox side panel "Fixed this week": for AP the sentence is "Bill B-0417 from Gulf Agri Supplies is no longer open
  (Tue 27 Oct)", from `copy["fixed_text"]`.

### B8. The monitor for AP

A saved bill triggers the document watches only (`duplicate_bill`, `unusual_bill`, `bill_missing_details`,
`new_supplier`). `monitor.handle_document_event` skips `kind="state"` watches (A4). `new_supplier` needs a per-document
form: for a saved bill, check whether its supplier has an earlier bill; reuse `payables_scan.new_suppliers` with the
period set to the bill's date and filter to the saved document ids, as the monitor does for the other checks. Invoice
events (`invoice.saved`) find no AP calls on the sales side and do nothing.

**Test:** `tests/unit/test_bot_monitor.py`: an AP bot with `bill_overdue` on a `bill.saved` event runs only its document
watches; an `invoice.saved` event does nothing for an AP bot.

### B9. The intro in chat for AP

`BotIntroMessage.jsx` branches on `run.summary.position.kind`:

- `payables`: stat tiles "documents checked", "duplicate groups", "to review" (all other findings), "due in 7 days"
  (amount); the position box "Payables · due in the next 7 days" with rows: Due in 7 days (amount, count), Overdue
  (amount, count). Intro text: "Hi, I'm Bill Guard. I checked N bills for the last 90 days. I found …" (period words
  "the last 90 days").
- The closing text lists the schedules ("I'll run every Monday at 08:00") and says "I only read your books; I never pay
  or change a bill."

The summary stores both `position` and, for VAT, the old `return_position`; the intro reads `position` first.

### B10. Tests that cover the whole AP flow

- `test_bot_agents.py`: the AP agent registers; its watch ids are unique; every template validates (`validate()` returns
  no unfixed issues; schedule kinds are allowed).
- `test_bot_validate.py`: an AP draft with a `before_deadline` schedule gets an unfixed issue; a VAT draft with `daily`
  does too.
- `test_bot_run.py` (or a new `test_ap_run.py`): a full `run_bot` for an AP bot with a fake engine: findings saved with
  details; a bill paid between two runs becomes `fixed`; the summary has `position.kind == "payables"`.
- `test_bot_inbox.py`: AP `stake` for duplicates; `columns` present in `GET /bots/agents`.
- Frontend: `npm run build`, then the local preview with sample data for AP (agent chip on Create, AP builder, AP inbox
  card, AP intro). Compare with the VAT screens for consistency.

### B11. Real data check and one paid run

1. On the VM, in process, read only, org 27: build the three AP templates' drafts, run `try_period` for each; record
   the counts (bills checked, overdue, due soon, new suppliers, duplicate groups). Look at five items by eye against the
   Accutax app. Org 27 is the best org for demos (`docs/VAT_BOT_IMPLEMENTATION_PLAN.md`).
2. One paid evaluation (batch, not per task): extend `backend/scripts/eval/bot_sentences.py` with an `--agent ap` option and
   `backend/tests/data/bot_sentences_ap.json` (20 sentences: overdue, due soon, duplicates, new supplier, daily, weekly,
   two watches, organization named and not found, vague, three that are not about a bot). Target: schedule and watches
   right on at least 18 of 20, and no invented organization. Run it once on the VM, without `--mock`. Put the table in
   this document under B12.

### B12. Results

(Fill in after B11.)

---

## Order, size and deploys

| Step | Tasks | Rough size | Deploy |
|---|---|---|---|
| 1 | A1 to A4, A7, A7a | 1 day | Backend only; VAT unchanged; deploy and check A10 steps 1 and 2 |
| 2 | A5, A6, A9 | 0.5 day | Backend; `GET /agents` live; daily kind live but unused |
| 3 | A8, A10 | 1 day | Frontend; VAT looks the same; compare screens |
| 4 | B1 to B5 | 1.5 days | Backend; AP drafts work through the API |
| 5 | B6 to B10 | 1.5 days | Frontend and the rest; AP bots can be created |
| 6 | B11 | 0.5 day | One paid run and the real-data check |

Rough sizes are for one developer who knows the bots code. No database change in any step.

## Open decisions (need the person)

| # | Decision | Default in this plan |
|---|---|---|
| 1 | Thresholds for overdue, due soon and new supplier (B1) | the table in B1; finance sign-off still needed |
| 2 | Name shown to users: "Accounts payable" or "AP / Bills" (the chat agent says "AP / Bills Agent") | "Accounts payable" for bots, chat name unchanged |
| 3 | Should a dismissed "due soon" finding come back when the bill becomes overdue? | No: dismissed stays dismissed (same key); revisit after the demo |
| 4 | Credits per run: today every run records 90 credits whether or not a model was called, and scheduled runs call no model. AP daily would show about 2,700 credits a month. Measure real cost and show that, or keep the flat estimate? | Keep the flat 90 for now; measure in B11 and set `credits_per_run` per agent |
| 5 | Should AP also watch bills against purchase orders or supplier statements (the chat AP agent can read an uploaded bill)? | Not in this plan |
| 6 | Supplier bank-detail changes are a classic fraud check, but the books have no bank-details field | Not possible until a field exists; note it for the DBA list |
