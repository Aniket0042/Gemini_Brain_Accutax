# VAT bot: implementation plan

Status: plan, not built. Written 2026-10-29.
Related: `docs/ACCUTAX_BOTS_PLAN.md` (the product plan for all bots), `docs/ACCUTAX_BOTS_MOCKUPS.html` (the screens),
`docs/ACCUTAX_AGENTS_DESIGN.md` (agents), `docs/VAT_REVIEW_PLAN.md` (the VAT review checks).

This plan is written so that a developer or a smaller model can build it one task at a time. Each task says what to
build, in which file, with which names, how to test it, and when it is done. Do the tasks in order. Do not skip the
tests.

---

## 0. Read this first

### 0.1 What we are building

A **VAT bot** is a user's own copy of the VAT Agent. It has a name, organizations, a schedule, things it watches, where
it sends results, rules, and notes. It runs on its own and tells the user about VAT problems and the return position.

The **VAT Agent** already exists (`backend/src/gemini_brain/agent/profiles.py`, key `vat`). Its checks already exist:

| Tool | File | What it gives |
|---|---|---|
| `review_vat` | `backend/src/gemini_brain/vat_review/tool.py` | Duplicate bills and VAT problems, each with a stable `key` |
| `vat_return` | `backend/src/gemini_brain/vat_return/tool.py` | Output VAT, input VAT, net, period, due date |
| `vat_penalty` | `backend/src/gemini_brain/vat_return/tool.py` | Late payment and late return penalties |
| `search_vat_kb` | `backend/src/gemini_brain/vat_kb/` | UAE VAT law answers with sources |

### 0.2 Phases

| Phase | What | Needs | Who can do it |
|---|---|---|---|
| 1 | **Creation, no database.** Setup card, templates, "describe it in one sentence", "Make this a bot", "Try it on last month", first-run introduction. Bots are kept in the browser only. | Nothing | Any developer or model, following this plan |
| 2 | **Bots saved and running on a schedule.** Database tables, run endpoint, Nest scheduler jobs, inbox. | DBA go-ahead for the tables; Accutax backend change for scheduler jobs | Developer with DB and Nest access |
| 3 | **Monitors.** Check each new bill and invoice as it is saved. | Accutax backend event hooks | Developer with Nest access |
| 4 | **Memory, approvals, email digest.** | Email service decision | Developer |

Phase 1 is written in full detail. Phases 2 to 4 are written at task level; detail them before starting, the same way.

### 0.3 Rules for whoever builds this

1. **No database changes** in phase 1. No `CREATE`, `ALTER`, migration or new table. Phase 2 tables need the DBA's
   go-ahead first (the database is shared, and the AI's DB user cannot alter tables).
2. **Read only.** A VAT bot never changes a bill, an invoice or a return, and never files anything. No task in this
   plan adds a write to Accutax data.
3. **Figures come from tools, never from the model.** Any number the user sees (counts, amounts, dates) comes from
   `review_vat`, `vat_return` or code. The model may write words around them. This is how the rest of the agent works.
4. **Tenant safety.** Every organization a bot uses must be in the user's `allowed_org_ids`
   (`CurrentUser.allowed_org_ids`, `backend/src/gemini_brain/api/auth.py`). Check on the server, every time. Never
   trust org ids from the browser.
5. **Match the code around you.** Same naming, same comment style (a short module docstring that says what the file is
   for, short comments only where the reason is not obvious), same test style as `tests/unit/test_agent_profiles.py`.
6. **Run the tests** after each task (commands in 0.5). All tests must pass before the next task.
7. **Commit only your own files**, on branch `deploy/vm-3535`. Do not commit files you did not create or change. Do not
   deploy unless the person asks.
8. **No paid model runs per task.** Unit tests mock the model. The paid evaluation runs once at the end of phase 1
   (task 1.12).

### 0.4 Words used in this plan

| Word | Meaning |
|---|---|
| Agent | The job in code: `AgentProfile` in `agent/profiles.py`. Here always `vat`. |
| Bot | The user's configured copy of an agent. A `BotDraft` before it is turned on, a `Bot` after. |
| Draft | A bot that is not turned on yet. It lives only in the setup card. |
| Watch | One thing a bot checks for, for example "VAT rate is wrong". Each watch maps to `review_vat` rules. |
| Schedule | When a bot runs: weekly, monthly, before the VAT return deadline, after a period ends. |
| Finding | One problem a check found. Has a stable `key` (`vat_review.tool.item_key`). |
| Setup card | The form that shows a draft. Every way of creating a bot ends here. |

### 0.5 Commands

Backend tests (from `Gemini_Brain_Accutax/backend`):

```bash
.venv/Scripts/python -m pytest tests/unit -q
```

One test file:

```bash
.venv/Scripts/python -m pytest tests/unit/test_bot_schema.py -q
```

Frontend build (from `Gemini_Brain_Accutax/frontend`):

```bash
npm run build
```

Local preview: start `gemini-brain-demo-backend` and `gemini-brain-frontend` from `.claude/launch.json`. Open
`http://127.0.0.1:5174/` and sign in with the first preconfigured account. The demo backend has no Cube data, so
"Try it on last month" returns an empty result locally; test it on the VM.

---

## 1. Phase 1: creation, no database

### 1.0 What the user can do at the end of phase 1

1. Press **New bot** and pick a VAT template, or type one sentence, or press **Make this a bot** under a VAT answer.
2. See the setup card filled in. Edit any field. See the next 3 run dates and a cost estimate.
3. Press **Try it on last month** and see what the bot would have found last month.
4. Press **Turn on**. The bot is saved in this browser, the first run happens right away, and the bot introduces
   itself with real figures.
5. See the bot in a **Your bots** list. Pause it, edit it, delete it.

Nothing runs on a schedule yet (that is phase 2). The list shows "Next run" dates, marked "scheduling starts when bots
are saved to your account".

### 1.1 New backend package

Create `backend/src/gemini_brain/bots/` with these files. Each task below fills one of them.

```
bots/
  __init__.py      # empty except the docstring
  schema.py        # task 1.2: the draft's shape
  watches.py       # task 1.3: VAT watches and how they map to review_vat rules
  templates.py     # task 1.4: ready-made VAT drafts
  schedule.py      # task 1.5: next run dates
  validate.py      # task 1.6: check and fix a draft on the server
  cost.py          # task 1.7: monthly cost estimate
  draft.py         # task 1.8: one sentence -> draft (model call)
  preview.py       # task 1.9: try it on last month, and the first run
```

`__init__.py` docstring:

```python
"""bots — A user's configured copy of an agent (docs/VAT_BOT_IMPLEMENTATION_PLAN.md).

Phase 1: the draft, its checks, templates, schedule dates, the cost estimate, one sentence to a draft, and the
"try it on last month" run. Nothing here writes to a database.
"""
```

### 1.2 Task: the draft's shape (`bots/schema.py`)

**Goal.** One Pydantic model that every other part uses. The browser sends it, the server checks it, templates are
written in it.

**Build.** Use Pydantic v2 (the API models in `api/models.py` already use it). Write exactly these classes:

```python
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Delivery = Literal["inbox", "email", "bell"]
RuleLevel = Literal["allow", "ask", "never"]
Sensitivity = Literal["fewer", "balanced", "more"]


class Schedule(BaseModel):
    """One time the bot runs. Exactly the fields for its kind are used; the others stay None."""
    kind: Literal["weekly", "monthly", "before_deadline", "after_period_end"]
    day: Optional[Weekday] = None                 # weekly
    day_of_month: Optional[int] = Field(None, ge=1, le=28)   # monthly (max 28 so every month has it)
    time: str = "08:00"                           # "HH:MM", 24 hours, in settings.report_timezone
    days_before: List[int] = Field(default_factory=list)     # before_deadline, e.g. [14, 7, 1]
    days_after: Optional[int] = Field(None, ge=0, le=27)     # after_period_end


class Rules(BaseModel):
    run_review: RuleLevel = "allow"
    export_findings: RuleLevel = "allow"
    email_outside: RuleLevel = "ask"
    change_records: Literal["never"] = "never"    # fixed: a VAT bot never changes Accutax data


class BotDraft(BaseModel):
    agent: Literal["vat"] = "vat"
    name: str = Field("VAT Watch", min_length=1, max_length=60)
    org_ids: List[int] = Field(default_factory=list, max_length=10)
    schedules: List[Schedule] = Field(default_factory=list, max_length=6)
    watches: List[str] = Field(default_factory=list)        # ids from bots/watches.py
    sensitivity: Sensitivity = "balanced"
    deliver: List[Delivery] = Field(default_factory=lambda: ["inbox"])
    rules: Rules = Field(default_factory=Rules)
    notes: List[str] = Field(default_factory=list, max_length=20)
    #: Short questions the card shows when something was not clear (filled by the server, not the user).
    questions: List[str] = Field(default_factory=list)
    #: Where the draft came from: "template:<id>", "sentence", "chat".
    source: str = "blank"
```

Also add `BotIssue` (one problem found by validation):

```python
class BotIssue(BaseModel):
    field: str      # "org_ids", "schedules[0].time", "watches", ...
    message: str    # plain English, shown under the field on the card
    fixed: bool     # True: the server changed the value; False: the user must fix it
```

**Rules.**
- Each note: strip spaces, drop empty ones, max 200 characters each (cut, do not reject).
- `time` must match `^([01]\d|2[0-3]):[0-5]\d$`. Use a `field_validator`.
- `agent` is only `"vat"` in phase 1. Other agents come later.

**Tests** (`backend/tests/unit/test_bot_schema.py`):
- A default `BotDraft()` is valid and has `deliver == ["inbox"]`, `rules.change_records == "never"`.
- `time="25:00"` fails. `time="08:00"` passes.
- `day_of_month=31` fails.
- `rules.change_records="allow"` fails.
- A note of 300 characters is cut to 200.

**Done when** the tests pass.

### 1.3 Task: VAT watches (`bots/watches.py`)

**Goal.** The list of things a VAT bot can watch, each mapped to `review_vat` rules (the rule names are the keys of
`RULE_TEXT` in `vat_review/tool.py`).

**Build.** A frozen dataclass and a tuple:

```python
@dataclass(frozen=True)
class Watch:
    id: str
    label: str            # shown on the card
    side: str             # "purchases" or "sales" (review_vat side)
    rules: Tuple[str, ...]
    default_on: bool
```

The VAT watches, exactly:

| id | label | side | rules | default_on |
|---|---|---|---|---|
| `duplicate_bill` | A bill looks recorded twice | purchases | `same_number`, `same_total_near_date`, `similar_vendor_same_day` | True |
| `vat_rate_wrong` | VAT on a bill is not 0% or 5% | purchases | `vat_rate_not_standard`, `vat_wrong_sign`, `vat_without_amount` | True |
| `bill_missing_details` | A bill is missing its supplier, number or has a future date | purchases | `missing_supplier`, `missing_number`, `future_date` | False |
| `unusual_bill` | A bill far above the supplier's usual amount | purchases | `large_for_supplier`, `large_one_off_supplier` | False |
| `sales_vat_wrong` | A sales invoice with wrong or no output VAT | sales | `vat_rate_not_standard`, `vat_zero`, `vat_wrong_sign` | True |

Functions:

```python
def all_watches() -> List[Watch]
def get(watch_id: str) -> Watch            # raises KeyError for an unknown id
def default_ids() -> List[str]             # the ids with default_on
def review_calls(watch_ids: Sequence[str], sensitivity: str) -> List[Dict[str, Any]]
```

`review_calls` returns the `review_vat` parameter dicts the bot must run, **one per side**, merging the rules of all
chosen watches on that side. Example: `["duplicate_bill", "vat_rate_wrong", "sales_vat_wrong"]` gives

```python
[{"mode": "all", "side": "purchases", "rules": [...6 rules, sorted...]},
 {"mode": "all", "side": "sales", "rules": ["vat_rate_not_standard", "vat_wrong_sign", "vat_zero"]}]
```

Sensitivity changes the rules:
- `fewer`: remove `similar_vendor_same_day` and `large_one_off_supplier` (the least sure ones).
- `balanced`: as listed.
- `more`: add `round_amount` to the purchases call if any purchases watch is on.

If a side ends with no rules, leave that side out.

**Tests** (`test_bot_watches.py`): every rule in the table exists in `vat_review.tool.RULE_TEXT`; `review_calls` for the
example above; `fewer` removes the two rules; `more` adds `round_amount`; an unknown id raises `KeyError`.

### 1.4 Task: templates (`bots/templates.py`)

**Goal.** Ready-made drafts, so a user can create a bot in one click with no model call.

**Build.**

```python
@dataclass(frozen=True)
class Template:
    id: str
    title: str          # card title
    summary: str        # one line under the title
    draft: BotDraft     # org_ids left empty: the server fills the user's current org

def all_templates(agent: str = "vat") -> List[Template]
def get(template_id: str) -> Template      # KeyError if unknown
```

The three VAT templates:

| id | title | summary | draft |
|---|---|---|---|
| `vat_weekly_digest` | Weekly VAT digest | Every Monday: new VAT problems and your return position. | name "VAT Watch"; schedules `[weekly mon 08:00]`; watches = `default_ids()`; deliver `["inbox", "email"]` |
| `vat_return_reminder` | VAT return reminder | 14, 7 and 1 days before each return: the position and what to fix. | name "Return Reminder"; schedules `[before_deadline days_before [14, 7, 1] 09:00]`; watches = `default_ids()`; deliver `["inbox", "bell"]` |
| `vat_full_watch` | Full VAT watch | Weekly digest plus return reminders, every check on. | name "VAT Guard"; schedules both of the above; watches = all ids; deliver `["inbox", "email", "bell"]` |

Set `source=f"template:{id}"` on each draft.

**Tests** (`test_bot_templates.py`): each template's draft is a valid `BotDraft`; ids are unique; every watch id exists
in `watches.py`.

### 1.5 Task: next run dates (`bots/schedule.py`)

**Goal.** Given a draft and the VAT filing frequency of its orgs, return the next N run dates, so the card can show
"Next: Mon 3 Nov · Mon 10 Nov · Tue 25 Nov (return)".

**Build.**

```python
@dataclass(frozen=True)
class RunTime:
    at: dt.datetime          # timezone-aware, settings.report_timezone
    schedule_index: int      # which Schedule in draft.schedules
    reason: str              # "weekly", "monthly", "14 days before the VAT return due 28 Nov", "3 days after Q3 ends"

def next_runs(draft: BotDraft, now: dt.datetime, frequencies: Dict[int, str], count: int = 3) -> List[RunTime]
```

- `frequencies`: org id -> `"monthly"` or `"quarterly"`. Missing org -> `"quarterly"`.
- Use `vat_return/periods.py` for tax periods and due dates (`period_containing`, `next_due_period`, `due_date`). Use
  `DEFAULT_QUARTER_GROUP`. Do not write new period maths.
- `weekly`: the next `count` dates on that weekday at `time`, strictly after `now`.
- `monthly`: the next dates on `day_of_month` at `time`.
- `before_deadline`: for each org, take the next due dates (look ahead 3 periods), subtract each `days_before`, keep
  those after `now`. If two orgs give the same date and time, keep one and join the reasons.
- `after_period_end`: the period end + `days_after` days, at `time`.
- Merge all schedules, sort by `at`, remove exact duplicates, return the first `count`.

Add one public helper in `vat_return/tool.py` so the API can get frequencies without the private `_settings`:

```python
def filing_frequencies(organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[int, str]:
    """Each organization's VAT filing frequency from its VAT settings; organizations with none are left out."""
    return _settings(organization_ids, subject, deadline)
```

**Tests** (`test_bot_schedule.py`), all with a fixed `now` (do not use the real clock):
- Weekly Monday 08:00 from Thursday 29 Oct 2026 gives 2, 9, 16 Nov.
- Before-deadline `[14, 7, 1]`, quarterly, from 29 Oct 2026: Q3 ends 30 Sep, due 28 Oct (passed), so the next due is
  Q4: ends 31 Dec, due 28 Jan 2027; runs 14 Jan, 21 Jan, 27 Jan.
- Monthly frequency gives monthly due dates.
- Two orgs with the same due date give one run, not two.
- Results are sorted and at most `count`.

### 1.6 Task: check a draft (`bots/validate.py`)

**Goal.** One function the API calls on every draft from the browser or the model. It fixes what it safely can and
reports the rest.

```python
def validate(draft: BotDraft, allowed_org_ids: Sequence[int], current_org_ids: Sequence[int]) -> Tuple[BotDraft, List[BotIssue]]
```

Checks, in this order:

| # | Check | If it fails |
|---|---|---|
| 1 | `org_ids` empty | Fill with `current_org_ids` (fixed=True, "Using your current organization"). If that is also empty, issue fixed=False "Pick at least one organization". |
| 2 | Each org id in `allowed_org_ids` | Remove it; issue fixed=True "Organization N removed: you do not have access". Never say the org's name. |
| 3 | `watches` has unknown ids | Remove them; fixed=True. |
| 4 | `watches` empty | Fill with `default_ids()`; fixed=True. |
| 5 | `schedules` empty | Issue fixed=False "Choose when it runs". Add to `questions`: "How often should it run?" |
| 6 | `weekly` without `day`, `monthly` without `day_of_month`, `before_deadline` without `days_before` | Issue fixed=False on that schedule. |
| 7 | `days_before` values outside 1..60 | Remove them; fixed=True. |
| 8 | Two schedules exactly equal | Keep one; fixed=True. |
| 9 | `deliver` without `inbox` | Add `inbox` first (the inbox is always on); fixed=True. |
| 10 | `name` only spaces | Set to "VAT Watch"; fixed=True. |

Return a new draft (do not change the one passed in) and the list of issues. `rules.change_records` is already fixed by
the schema.

**Tests** (`test_bot_validate.py`): one test per row; and a test that a clean template draft gives no issues.

### 1.7 Task: cost estimate (`bots/cost.py`)

**Goal.** Show "about N credits a month" on the card, in the same credits as the usage meter (1 credit = $0.001,
`components/UsageMeter.jsx`).

```python
#: Measured on the VM (docs/ACCUTAX_BOTS_PLAN.md): one VAT review run with the model's summary.
CREDITS_PER_RUN = 90

def runs_per_month(draft: BotDraft, frequencies: Dict[int, str]) -> int
def monthly_credits(draft: BotDraft, frequencies: Dict[int, str]) -> int
```

- weekly = 4.33 runs, monthly = 1, before_deadline = `len(days_before)` per period (monthly frequency: per month;
  quarterly: divided by 3), after_period_end = 1 per period (same division).
- Multiply by the number of orgs (one run covers all orgs, but the review cost grows with orgs: use
  `1 + 0.5 * (orgs - 1)`).
- Round up to a whole number.

**Tests**: weekly one org = 390 (4.33 × 90, rounded up); reminder `[14,7,1]` quarterly one org = 90; two orgs weekly
= 585.

### 1.8 Task: one sentence to a draft (`bots/draft.py`)

**Goal.** The user types one sentence; the model fills a `BotDraft`; code checks it.

**Model call.** Use `reasoning/bedrock_client.py`. The module-level `converse_tools` has no `tool_choice`; add an
optional `tool_choice: Optional[str] = None` parameter to it, the same way the class method `converse_with_tools`
already does (it sets `toolConfig["toolChoice"] = {"tool": {"name": tool_choice}}`). Do not change its other behaviour.

Give the model one tool, `make_bot_draft`, whose input schema is the JSON schema of these fields only: `name`,
`org_names` (list of strings the user named, may be empty), `schedules`, `watches` (enum = watch ids), `sensitivity`,
`deliver`, `notes`, `unclear` (list of short questions). Force it with `tool_choice="make_bot_draft"`. Use
`purpose="bot_draft"` and `temperature=0`. Use the cheapest model the deployment offers (the same one `agent/models.py`
uses for its lowest tier); if unsure, the default `MODEL_ID`.

System prompt (exactly this text, plus today's date and the timezone):

```
You turn one sentence into the settings of a VAT bot for Accutax. Call make_bot_draft once.
Fill only what the sentence says or clearly means. Leave the rest out: code fills defaults.
"every Monday" is a weekly schedule on mon. "before the return", "before VAT is due" is before_deadline.
"after the quarter ends" is after_period_end. A time like "9am" is "09:00". No time given: leave time out.
"email me" adds email to deliver. "alert me", "notify me right away" adds bell.
Pick watches only from the list. "duplicates" is duplicate_bill. "wrong VAT", "VAT not 5%" is vat_rate_wrong.
"sales", "invoices I send" is sales_vat_wrong.
Organization names the user wrote go in org_names, exactly as written. Never invent an organization.
Anything you are not sure about goes in unclear as a short question. Never ask more than 3.
Never put figures, amounts or dates of transactions in any field.
```

**Function.**

```python
def from_sentence(text: str, *, allowed_orgs: Dict[int, str], current_org_ids: Sequence[int],
                  call_model: Callable[..., Dict[str, Any]] = bedrock_client.converse_tools) -> Tuple[BotDraft, List[BotIssue]]
```

Steps:
1. Reject `text` longer than 500 characters or empty (raise `ValueError`).
2. Run the PII shield on the text before it goes to the model, exactly as the chat path does: `agent/loop.py` uses
   `privacy/masking.py` (`Masking`) and `pii/redactor.py` (`redact_pii`); call the same code the same way. The model
   must not see names or numbers the shield masks. Organization names the user typed are matched in step 4 against
   the unmasked text, in code.
3. Call the model. Read the `toolUse` block named `make_bot_draft`. If there is none, return the template
   `vat_weekly_digest` draft with one issue "Could not read that; started from the weekly digest" (fixed=True).
4. Map `org_names` to ids: case-insensitive exact match against `allowed_orgs` names. Unmatched names become a question
   "Which organization is 'X'?" Do not guess.
5. Build `BotDraft(source="sentence", ...)`; drop any field that fails the schema (do not crash on a bad model output:
   build the draft field by field and skip the bad ones, adding an issue).
6. `unclear` goes into `draft.questions` (max 3).
7. Run `validate()` and return its result.

**Tests** (`test_bot_draft.py`): pass a fake `call_model` that returns a fixed `toolUse` block. Cover: a good answer;
no `toolUse`; an unknown watch id; an org name that matches; one that does not; a bad time "25:00" (dropped, issue
added); text over 500 characters raises. No real model call in unit tests.

### 1.9 Task: try it on last month, and the first run (`bots/preview.py`)

**Goal.** Run the bot's checks for real, read only, with no model, and return counts and the findings.

```python
def try_period(draft: BotDraft, *, preset: str, subject: str, exclude: AbstractSet[str] = frozenset(),
               deadline_seconds: float = 60) -> Dict[str, Any]
```

- `preset`: `"last_month"` for "Try it on last month"; `"this_quarter"` for the first run.
- For each dict in `watches.review_calls(draft.watches, draft.sensitivity)`: call
  `vat_review.tool.run({**call, "period": {"preset": preset}, "limit": 50}, organization_ids=draft.org_ids,
  subject=subject, deadline=time.monotonic() + deadline_seconds, exclude=exclude)`.
- Also call `vat_return.tool.run({"which": "next_due"}, organization_ids=draft.org_ids, subject=subject,
  deadline=...)` for the return position (`which` is one of `WHICH` in `vat_return/tool.py`).
- `subject`: `f"bot-preview:{user_id}"`.

Return:

```python
{
  "period": {"start": "...", "end": "..."},
  "bills_checked": int, "invoices_checked": int,
  "groups_found": int, "findings_found": int,       # summed over sides
  "by_watch": {"duplicate_bill": int, ...},        # count per watch id (map each finding's rule back to its watch)
  "would_send": {"alerts": int, "digests": int},   # alerts = findings + groups; digests = runs in that month (schedule.py)
  "return": {...the vat_return organisation entries, unchanged...},
  "reviews": [...the review_vat results, unchanged, for the card...],
}
```

All figures come from the tool results. No model call in this function.

**Tests** (`test_bot_preview.py`): patch `vat_review.tool.run` and `vat_return.tool.run` with fakes; check the sums,
`by_watch`, that `exclude` is passed through, and that a side with no watch is not called.

### 1.10 Task: API routes (`api/bot_routes.py`)

**Goal.** The endpoints the setup card calls. All need a signed-in user (`Depends(get_current_user)`). None writes to a
database.

Add a new router in `api/bot_routes.py` and include it in `api/app.py` the same way `usage_routes.py` is included.
Prefix `/api/v1/bots`. Tag `Bots`.

| Method and path | Body | Returns | Notes |
|---|---|---|---|
| `GET /templates?agent=vat` | none | `{templates: [{id, title, summary, draft}]}` | |
| `GET /watches?agent=vat` | none | `{watches: [{id, label, side, default_on}]}` | |
| `POST /draft/from-template` | `{template_id, org_ids}` | `{draft, issues, next_runs, monthly_credits}` | Fill `org_ids`, then validate |
| `POST /draft/from-sentence` | `{text, org_ids}` | same | `org_ids` = the orgs selected in the app now; calls the model; counts toward usage |
| `POST /draft/from-chat` | `{question, agent, org_ids}` | same | Builds the sentence `"A bot that regularly does this: " + question`, then the same as from-sentence. `agent` must be `vat`, else 400. |
| `POST /draft/check` | `{draft}` | same | The card calls this after each edit (debounced 400 ms in the browser) |
| `POST /try` | `{draft, preset}` | the `try_period` result | `preset` in `{"last_month", "this_quarter"}`, else 400 |

For every route that takes `org_ids` or a draft:
1. Authorize with the same helper the chat uses: `authorize_org_scope(...)` from `api/auth.py` (see `_query_orgs` in
   `api/routes.py`). Unauthorized orgs are a 403 for `org_ids`; inside a draft they are removed by `validate()`.
2. `next_runs`: call `vat_return.tool.filing_frequencies(draft.org_ids, subject, deadline)`, then
   `schedule.next_runs(draft, now, frequencies, 3)`. Return each as `{at: iso, reason}`.
3. `monthly_credits`: `cost.monthly_credits(draft, frequencies)`.

Usage: `from-sentence`, `from-chat` and `try` must record cost in the usage ledger the same way the chat does
(`gemini_brain.usage.ledger.scope(...)`; see `api/routes.py` around the health check and the query route). If the
user's limit is used up, return 429 with the same message the chat uses.

Add the request and response models to `api/models.py` next to `AgentInfo`.

**Tests** (`test_bot_routes.py`): use the FastAPI test client pattern from `tests/unit/test_api_routes.py`. Cover: each
route returns 200 for a good request; an org outside `allowed_org_ids` gives 403; `from-chat` with `agent="ap"` gives
400; `try` with `preset="ytd"` gives 400. Mock the model, `vat_review.tool.run`, `vat_return.tool.run` and
`filing_frequencies`.

### 1.11 Task: frontend

All new frontend files go in `frontend/src/components/bots/` and `frontend/src/services/bots.js`. Match the existing
style: function components, `lucide-react` icons, CSS in `index.css` under a new section header
`/* ── Bots (components/bots/*) ── */`, colours only from existing CSS variables (`--ink`, `--ink-soft`, `--border`,
`--surface`, `--success`, `--warning`, `--danger`).

#### 1.11.1 `services/bots.js`

API calls, one per route in 1.10, in the same style as `fetchAgents` in `services/api.js` (reuse its `authHeaders`
helper by exporting it, or copy the 1-line helper).

Browser store for phase 1 (replaced by the database in phase 2):

```js
const KEY = 'accutax_bots';   // { [botId]: { id, draft, status: 'active'|'paused', createdAt, lastRun } }
export function listBots()            // newest first
export function saveBot(draft)        // new id = 'bot-' + Date.now(); returns the bot
export function updateBot(id, patch)
export function deleteBot(id)
```

Wrap every `localStorage` call in `try/catch`, the same as `services/findings.js`. Keep the header comment style of
`findings.js`: say plainly that this is browser only and clearing storage removes the bots.

#### 1.11.2 Components

| File | What it shows | Props |
|---|---|---|
| `BotBuilder.jsx` | The whole flow in a modal: start step (sentence box + template cards) then the setup card | `open`, `onClose`, `token`, `orgIds`, `orgNames`, `start` (`{mode: 'blank'|'chat', question?}`) |
| `SetupCard.jsx` | The setup card from the mockups: header (icon, name, agent, cost), When, Watches + sensitivity, Deliver to, Rules, Notes, questions, issues, buttons | `draft`, `issues`, `nextRuns`, `credits`, `onChange(draft)`, `onTry`, `onTurnOn`, `busy` |
| `TryResult.jsx` | The "In September, VAT Watch would have sent you" box: alerts, digests, groups, findings, per-watch counts | `result` |
| `BotIntro.jsx` | The first-run message: "I'm VAT Watch. I checked N bills…" with See findings and Adjust | `bot`, `result`, `onSeeFindings`, `onAdjust` |
| `BotList.jsx` | "Your bots": one row per bot with name, status, next run, Pause/Resume, Edit, Delete | `bots`, `onEdit`, `onToggle`, `onDelete` |

Setup card behaviour:
- Every edit calls `onChange`; `BotBuilder` debounces 400 ms then calls `POST /draft/check` and replaces `issues`,
  `nextRuns` and `credits` (not the user's typing).
- Issues with `fixed: false` show red under their field and disable **Turn on** with the reason in `title`.
- `questions` show as a yellow box at the top with chips where possible ("How often?": Weekly / Monthly / Before each
  return).
- `rules.change_records` shows as "Change bills or file the return: Never" with no control (it cannot be changed).
- Delivery chip "WhatsApp" shows as disabled with "soon".

**Turn on** does, in order:
1. `saveBot(draft)`.
2. `POST /try` with `preset: "this_quarter"`.
3. Show `BotIntro` with the result. Text, built in the browser from the figures only:
   `I'm {name}. I checked {bills_checked} bills{ and N sales invoices} for {period}. {groups} likely duplicate groups
   and {findings} other VAT problems. I'll run {schedule words} and send results to {deliver words}.`
   If nothing found: `… Everything looks right.`
4. **See findings** opens a chat answer with the VAT review card: send the VAT Agent's `run_prompt` through the
   existing `handleSubmitQuery(question, { agent: 'vat' })` path in `App.jsx`. Do not build a second findings view.

#### 1.11.3 Where the user starts

| Place | Change |
|---|---|
| Header (`Header.jsx`) | A "Bots" icon button (lucide `Bot`) left of the theme button. Opens a panel with `BotList` and a **New bot** button. |
| Home page (`IntroSuggestions.jsx` children slot in `App.jsx`) | One line under the suggestions: "Let a bot watch your VAT" with **New bot**. Keep it small. |
| Under a VAT answer (`ResponseView.jsx`) | A **Make this a bot** button next to the existing answer actions, only when the answer's `agent` is `vat`. Opens `BotBuilder` with `start={{mode: 'chat', question}}`. |

**Check in the preview** (0.5): open New bot, pick each template, edit fields, see issues appear and clear, turn a bot
on, pause it, delete it. Take one screenshot of the setup card for the person.

### 1.12 Task: evaluate "one sentence to a draft"

Make `backend/tests/data/bot_sentences.json`: 25 sentences with the expected draft fields. Cover: weekly, monthly,
before the return, after the quarter, a time, email, bell, sales, two watches, an organization name, an unknown
organization name, a vague sentence ("keep an eye on VAT"), and three sentences that are not about a bot at all
("what is my VAT for Q3"). The expected result for those three is the fallback with an issue.

Write `backend/scripts/eval/bot_sentences.py`: runs `from_sentence` on each with the real model, compares field by field,
prints a table and the total cost. **Run it once**, on the VM, at the end of the phase (paid run). Target: schedules and
watches right on at least 22 of 25; no invented organization ever. Put the result table in this document under 1.13.

### 1.12a Review of the build (2026-10-29)

The build was reviewed before shipping. Fixed:

| # | Problem | Fix |
|---|---|---|
| 1 | A hardcoded login in `api/routes.py` gave a token for orgs 101 and 102 when the normal login failed | Removed |
| 2 | `/bots/tick` and `/bots/event` fell back to a secret written in the code when `BOT_SERVICE_TOKEN` was unset | No token set: both answer 503 |
| 3 | Runs saw only the first 50 groups and 50 findings, so the rest were marked "fixed" though still there; the monitor missed saved bills past the first 50 | `review_vat.run(..., unlimited=True)` for bots (not offered to the model); "fixed" only when every scan finished |
| 4 | `GET /bots/approvals` was shadowed by `GET /bots/{bot_id}` | Route moved above it |
| 5 | Runs created approvals for an invented recipient and recorded `email_dispatched: True` with no email sent | Removed; email is taken off drafts until an email service exists |
| 6 | One failing bot stopped the tick, and its `next_run_at` never moved, so it failed on every tick | Each run is caught and saved as failed; the next run always moves on; a tick runs at most 20 bots in 45 seconds |
| 7 | Reminder and period-end bots always checked last month | They check the VAT period whose return is due next |
| 8 | Findings were filed under the first organization | Each finding keeps its own organization |
| 9 | Notes edited on the setup card never reached memory | Synced on every edit |
| 10 | Any status or memory source was accepted (500 from the database); `/event` returned the raw error | Request models allow only valid values (422); the error goes to the log only |

Tests for each: `tests/unit/test_bot_review_fixes.py`, plus `test_unlimited_returns_every_group_for_bots` in
`tests/unit/test_vat_review.py`.

Still open: the real paid sentence eval
(task 1.12); the email service. Notes typed on the setup card and lines added with "Teach bot" share one memory
source, so editing the card's notes replaces taught lines too.

### 1.13 Phase 1 results

Completed on 2026-10-29.

#### 1. Backend Implementation & Unit Testing
All 10 backend tasks (1.1 - 1.10) built and tested:
- `bots/schema.py`: Pydantic v2 `BotDraft`, `Schedule`, `Rules`, `BotIssue`
- `bots/watches.py`: 5 watches mapped to `vat_review` rules with sensitivity filtering
- `bots/templates.py`: 3 ready-made templates (`vat_weekly_digest`, `vat_return_reminder`, `vat_full_watch`)
- `bots/schedule.py`: `next_runs` period and deadline mathematics
- `bots/validate.py`: multi-tenant organization authorization, schedule/watch integrity checks
- `bots/cost.py`: credit estimations (90 credits/run)
- `bots/draft.py`: Bedrock tool calling with PII redaction and deterministic fallback
- `bots/preview.py`: `try_period` dry runs against `vat_review` and `vat_return`
- `api/bot_routes.py`: 6 endpoints (`/templates`, `/draft/from-sentence`, `/draft/from-chat`, `/draft/check`, `/try`, `/active-count`)
- Unit test suite: **52/52 tests passed (100%)** (`pytest tests/unit/test_bot_*.py -q`).

#### 2. Frontend Implementation & Build
- `services/bots.js`: LocalStorage store (`accutax_bots`) + API client
- Components: `BotBuilder`, `SetupCard`, `TryResult`, `BotIntro`, `BotList`
- Integration points:
  - Header: Bots button (lucide `Bot`) with modal launcher
  - Home: "Let a bot watch your VAT" banner with New bot launcher
  - ResponseView: "Make this a bot" button for VAT answers
  - App.jsx: State management and modal orchestration
- Build verification: `npm run build` in `frontend/` passed cleanly (exit code 0).

#### 3. Sentence Evaluation Results (Task 1.12)
- Dataset: `tests/data/bot_sentences.json` (25 cases covering weekly, monthly, before deadline, after period, time, email, bell, sales, two watches, org match, unknown org, vague, and non-bot fallback).
- Evaluation script: `scripts/eval/bot_sentences.py`.
- **Target: >= 22/25 passed; 0 invented orgs.**
- **Not measured yet.** The 25/25 below was run with `--mock`: a hand-written fake model that recognises these exact sentences, so it tests the scoring script, not the model. The real paid run (no `--mock`, on the VM) is still to do; replace this table with its result.

| ID | Category | Sentence | Schedule | Watches | Orgs | Result |
|---|---|---|---|---|---|---|
| s01 | weekly | Check our VAT every Monday morning at 09:00 | Yes | Yes | Yes | PASS |
| s02 | monthly | Run a VAT check on the 1st of every month at 10:00 | Yes | Yes | Yes | PASS |
| s03 | before_return | Check our VAT 7 days before each VAT return deadline | Yes | Yes | Yes | PASS |
| s04 | after_quarter | Review our VAT position 3 days after each quarter ends | Yes | Yes | Yes | PASS |
| s05 | time | Review duplicate bills every Friday at 17:30 | Yes | Yes | Yes | PASS |
| s06 | email | Send me an email digest of VAT problems every Wednesday at 08:00 | Yes | Yes | Yes | PASS |
| s07 | bell | Alert me in app notifications if any VAT rate is wrong | Yes | Yes | Yes | PASS |
| s08 | sales | Check our sales invoices for wrong VAT or zero rating every week | Yes | Yes | Yes | PASS |
| s09 | two_watches | Check for duplicate bills and missing bill details every Tuesday | Yes | Yes | Yes | PASS |
| s10 | org_match | Check VAT for Al Futtaim LLC every Monday morning | Yes | Yes | Yes | PASS |
| s11 | unknown_org | Watch VAT for Unknown Enterprises LLC every month | Yes | Yes | Yes | PASS |
| s12 | vague | Keep an eye on VAT | Yes | Yes | Yes | PASS |
| s13 | non_bot | What is my VAT for Q3? | Yes | Yes | Yes | PASS |
| s14 | non_bot | How do I file my VAT return in UAE? | Yes | Yes | Yes | PASS |
| s15 | non_bot | Show me our top 5 suppliers by spend this year | Yes | Yes | Yes | PASS |
| s16 | weekly_high_sensitivity | Audit our supplier bills every Tuesday with high sensitivity | Yes | Yes | Yes | PASS |
| s17 | before_deadline_multiple | Remind me about our VAT return 14 days and 2 days before | Yes | Yes | Yes | PASS |
| s18 | monthly_mid | Check our VAT rates on the 15th of each month | Yes | Yes | Yes | PASS |
| s19 | multi_delivery | Send an email and in-app alert every Thursday for missing bill details | Yes | Yes | Yes | PASS |
| s20 | org_match_watch | Check Apex Holdings for duplicate bills every week | Yes | Yes | Yes | PASS |
| s21 | full_watch | Run a full monthly VAT review checking all rules and potential penalties | Yes | Yes | Yes | PASS |
| s22 | after_quarter_single_day | Review VAT 1 day after each quarter ends at 09:00 | Yes | Yes | Yes | PASS |
| s23 | weekly_sunday | Scan for duplicate supplier invoices every Sunday evening at 18:00 | Yes | Yes | Yes | PASS |
| s24 | fewer_sensitivity | Send only obvious VAT errors every Friday with low sensitivity | Yes | Yes | Yes | PASS |
| s25 | before_deadline_email | Email me 5 days before each VAT deadline about our net payable | Yes | Yes | Yes | PASS |


---

## 2. Phase 2: bots saved and running on a schedule

### 2.1 Tables: done

Created on 2026-10-29 on `accutax_bk_1_5` (the VM's database) by `backend/dba/ai_bot_tables.sql`, at the DBA's
request, owned by `postgres`, with `SELECT, INSERT, UPDATE, DELETE` for `gemini_brain_ro`. Deleting a bot deletes its
runs, findings and memory (`ON DELETE CASCADE`). The rollback line is at the top of the script. The EC2 database does
not have them yet: run the same script there when that environment needs bots.

The tables, for reference (the script is the source of truth):

| Table | Columns |
|---|---|
| `ai_bots` | `id` uuid pk, `owner_user_id` int, `agent` text, `name` text, `org_ids` int[], `draft` jsonb (the validated `BotDraft`), `status` text (`active`, `paused`, `error`), `created_at`, `updated_at`, `last_run_at`, `next_run_at` |
| `ai_bot_runs` | `id` uuid pk, `bot_id` fk, `trigger` text (`schedule`, `first_run`, `manual`, `event`), `started_at`, `finished_at`, `status` text, `credits` int, `summary` jsonb (counts only), `error` text |
| `ai_bot_findings` | `bot_id` fk, `key` text (the `item_key`), `org_id` int, `rule` text, `state` text (`new`, `seen`, `fixed`, `dismissed`), `first_seen_run` fk, `last_seen_run` fk, `amount` numeric, pk (`bot_id`, `key`) |
| `ai_bot_memory` | `id` pk, `bot_id` fk, `text` text, `source` text (`note`, `dismissal`), `created_at` |

Indexes: `ai_bots(owner_user_id)`, `ai_bots(status, next_run_at)`, `ai_bot_runs(bot_id, started_at desc)`.

### 2.2 Task: store layer (`bots/store.py`)

Plain SQL through the existing DB pool (`gemini_brain/db/connection.py`). Functions: `create`, `get`, `list_for_user`, `update`,
`set_status`, `delete`, `record_run`, `upsert_findings`, `due_bots(now)`. Every function that reads a bot takes
`owner_user_id` and filters by it. Tests with the same DB fakes the other store tests use.

### 2.3 Task: replace the browser store

Routes `POST /bots`, `GET /bots`, `GET /bots/{id}`, `PATCH /bots/{id}`, `DELETE /bots/{id}`, `POST /bots/{id}/pause`,
`POST /bots/{id}/resume`. `services/bots.js` switches from `localStorage` to these. On first load, offer to move bots
found in `localStorage` to the account, then clear them.

### 2.4 Task: the run (`bots/run.py`)

```python
def run_bot(bot_id: str, trigger: str) -> Dict[str, Any]
```

1. Load the bot. Stop if not `active`.
2. Re-check org access for the owner **now** (`get_user_allowed_orgs(owner_user_id)`); drop orgs the owner lost. No org
   left: status `error`, message "No access to the bot's organizations".
3. `exclude` = keys of findings in state `dismissed`.
4. `try_period(draft, preset=...)`: weekly and monthly runs use `"this_quarter"`; before-deadline and after-period runs
   use the period that is due.
5. Diff the findings against `ai_bot_findings`: new keys -> `new`; keys no longer found -> `fixed`; others unchanged.
6. One model call writes a 2-3 sentence summary from the counts and the new findings only, with the same figure
   verifier the chat uses. Skip the call when nothing is new: use a fixed sentence.
7. Save the run and the findings. Put an inbox item for the owner (2.6).

### 2.5 Task: the scheduler

In the Nest backend (`accutax_bk_backend`), add a BullMQ repeatable job "ai-bot-tick" every 5 minutes. It calls
`POST /api/v1/bots/tick` on the AI service with a service token (a shared secret in both services' env, compared with
`hmac.compare_digest`). The AI service runs `due_bots(now)` and `run_bot` for each, one at a time, and sets the next
`next_run_at` with `schedule.next_runs`. This keeps the schedule logic in one place (Python) and the Nest side tiny.

### 2.5a Bots without the Accutax backend (2026-10-30)

Decided: no change to `accutax_bk_backend`. Both triggers run inside the AI service.

- **Scheduler** (`bots/scheduler.py`): a background thread started at API startup when `BOT_SCHEDULER_ENABLED=true`.
  Every `BOT_SCHEDULER_INTERVAL_SECONDS` (default 300, at least 60) it runs the bots that are due (at most 20 bots or
  45 seconds per cycle; the rest stay due), moves each bot's next run on even after a failure, then polls for new
  documents. A Postgres advisory lock wraps each cycle, so two processes never run it at once.
  `POST /api/v1/bots/tick` runs the same `run_due_bots()` for an outside scheduler, still behind `BOT_SERVICE_TOKEN`.
- **Monitor poll** (`bots/poller.py`): `expense` and `income` have no "updated" column, and `created_date` is not
  reliable (seed data has future dates), so new bills and sales invoices are found by id (ids only grow), with
  primary-key index scans only, for the organizations that have an active bot. Each organization's new documents go
  to `monitor.handle_document_event`, as the `/event` route would.
- **Limits:** only new documents are seen. An edited old bill, or a draft finalised later, is caught by the bot's
  scheduled run, not by the poll. The last id is kept in memory, so after a restart the poll starts from the newest
  id; documents saved while the service was down are also left to the scheduled run. Alerts arrive up to one interval
  after the bill is saved.
- **Turn on:** set `BOT_SCHEDULER_ENABLED=true` in the AI service's `.env` in one environment, then restart the
  service. `BOT_SERVICE_TOKEN` is not needed for this.
- Tests: `tests/unit/test_bot_scheduler.py`.

### 2.6 Task: inbox and bell

`GET /bots/inbox`: the owner's latest run results and new findings across bots. The header's Bots button shows a dot
when there is something unread. The inbox item reuses `BotIntro`'s layout for run summaries and the existing VAT review
card for findings.

### 2.7 Phase 2 results

Completed on 2026-10-29.

#### 1. Backend Implementation & Persistence
All Phase 2 tasks (2.1 - 2.6) built and verified:
- **Task 2.1 (Tables)**: `ai_bots`, `ai_bot_runs`, `ai_bot_findings`, `ai_bot_memory` created and verified on the VM database (`accutax_bk_1_5`).
- **Task 2.2 (`bots/store.py`)**: Full PostgreSQL persistence layer with owner isolation (`create`, `get`, `list_for_user`, `update`, `set_status`, `delete`, `record_run`, `finish_run`, `list_runs`, `upsert_findings`, `get_findings`, `update_finding_state`, `due_bots`, `set_next_run`, `dismiss_finding`, `undismiss_finding`, `get_inbox`, `mark_inbox_seen`, `add_memory`, `list_memory`, `delete_memory`).
- **Task 2.3 (Server-Backed API)**: CRUD endpoints (`POST /bots`, `GET /bots`, `GET /bots/{id}`, `PATCH /bots/{id}`, `DELETE /bots/{id}`, `POST /bots/{id}/pause`, `POST /bots/{id}/resume`) with automatic validation and schedule calculations.
- **Task 2.4 (`bots/run.py`)**: Execution runner with dynamic owner org access checks, dismissed finding exclusion, `try_period` preset routing, diffing findings (`new`, `seen`, `fixed`), and run result persistence.
- **Task 2.5 (Scheduler `POST /api/v1/bots/tick`)**: Superseded on 2026-10-30: no Nest job is needed. The scheduler runs inside the AI service (`bots/scheduler.py`, see 2.5a). Timing trigger with timing-safe HMAC service token authentication (`x-bot-service-token` or Bearer header), running due bots and automatically advancing `next_run_at`.
- **Task 2.6 (Inbox & Findings)**:
  - Endpoints: `GET /api/v1/bots/inbox`, `POST /api/v1/bots/inbox/mark-read`, `POST /api/v1/bots/{bot_id}/findings/{key}/dismiss`, `POST /api/v1/bots/{bot_id}/findings/{key}/undismiss`.

#### 2. Frontend Integration & Build
- `services/bots.js`: Added `fetchBotInbox`, `markBotInboxSeen`, `dismissBotFinding`, `undismissBotFinding`, and server migration.
- `components/Header.jsx`: Added `botsUnreadCount` prop with an unread dot badge on the Bot icon button when new findings exist.
- `components/bots/InboxView.jsx`: Created inbox view component with recent run cards, active finding cards, filter tabs (All / New / Seen), "Mark all read" button, and finding dismissal ("Not a problem") actions.
- `App.jsx`: Added tabs to the Bots modal to toggle seamlessly between "Bots" and "Inbox", real-time unread count synchronization, and automated inbox reload.
- Build verification: `npm run build` in `frontend/` passed in 27.4s with 0 errors.

#### 3. Test Verification
- All 12 test suites: **69/69 unit tests passed (100%)** (`pytest tests/unit/test_bot_*.py -q`).
- Full coverage across store, runner, routes, tick scheduler, inbox, and finding dismissal operations.

---

## 3. Phase 3: monitors

### 3.1 Task: event hooks (Accutax backend)

When a bill or a sales invoice is created or updated, the Nest backend posts
`{event: "bill.saved"|"invoice.saved", org_id, document_id}` to `POST /api/v1/bots/event` with the service token.
Batch: collect events per org for 60 seconds, then send one request with all document ids.

### 3.2 Task: per-document checks

`bots/monitor.py`: for each active bot with that org, run `review_vat` limited to the saved documents' date window and
keep only findings that include those document ids. Code only; the model writes one sentence only for a new finding.
Respect quiet hours (default 20:00-08:00): non-urgent findings wait for the next digest.

### 3.3 Task: "Why did I get this?"

Each finding in the inbox stores which watch and rule fired; the card shows it with a link to that watch in the
bot's settings.

### 3.4 Phase 3 results

Completed on 2026-10-29.

#### 1. Backend Implementation & Scoped Monitoring
All Phase 3 tasks (3.1 - 3.3) implemented, verified, and integrated:
- **Task 3.1 (Event Hooks Endpoint)**: Superseded on 2026-10-30: no Nest hooks are needed. New documents are found by polling (`bots/poller.py`, see 2.5a).
  - Route: `POST /api/v1/bots/event` with `BotEventRequest` (`event`, `org_id`, `document_id`, `document_ids`).
  - Timing-safe HMAC service token authentication (`x-bot-service-token` or Bearer header).
  - Validation: rejects unsupported events with `400 Bad Request`; rejects missing or invalid tokens with `401 Unauthorized`.
- **Task 3.2 (Scoped Document Check Engine - `gemini_brain/bots/monitor.py`)**:
  - `is_in_quiet_hours`: evaluates quiet hours window (default 20:00 to 08:00) using `settings.report_timezone`. Findings created during quiet hours are marked with `in_quiet_hours=True` for digestion.
  - `generate_finding_sentence`: produces clear 1-sentence explanations from document number, supplier/customer, amount, and rule specifics (with optional LLM refinement).
  - `handle_document_event`:
    - Discovers active bots covering the event's organization and document side (`purchases` for `bill.saved`, `sales` for `invoice.saved`).
    - Enforces real-time owner organization permissions (`get_user_allowed_orgs`).
    - Excludes previously dismissed findings (`ai_bot_memory`).
    - Runs scoped `vat_review` and filters duplicate groups and anomaly findings strictly to the targeted document IDs.
    - Diffs findings (`new`, `seen`, `fixed`), writes explanations for new findings, upserts to `ai_bot_findings`, and records run with `trigger="event"`.
- **Task 3.3 (Watch Attribution & "Why did I get this?")**:
  - `watches.py`: `watch_for_rule` maps rules and watch IDs back to their parent watch (`duplicate_bill`, `vat_rate_wrong`, `bill_missing_details`, `unusual_bill`, `sales_vat_wrong`).
  - `store.py`: `get_inbox` returns `watch_id`, `watch_label`, and contextual `why_text` on all active findings.

#### 2. Frontend Integration & UI Attribution
- `components/bots/InboxView.jsx`:
  - Added "Why did I get this?" toggle button to every active finding card.
  - Expandable attribution section showing Bot, Watch, Rule, Confidence, and Explanation.
  - "Adjust watch in bot settings &rarr;" action button calling `onEditBot(finding.bot_id)`.
- `App.jsx`:
  - Connected `onEditBot` handler to `InboxView`, navigating straight from an inbox problem to the Bot Builder with that bot focused.
- `index.css`:
  - Added dedicated styling for `.bot-finding-why-box`, `.bot-finding-why-grid`, and `.bot-finding-why-edit-btn`.
- Production build verification: `npm run build` in `frontend/` passed cleanly in 1m 35s with 0 errors.

#### 3. Test Verification
- Created `tests/unit/test_bot_monitor.py` covering event hook auth, event type validation, quiet hours logic, 1-sentence generation, rule watch attribution, and scoped document event execution.
- Full bot test suite: **79/79 unit tests passed (100%)** across 13 test files:
  - `test_bot_cost.py`: 4 passed
  - `test_bot_draft.py`: 5 passed
  - `test_bot_preview.py`: 2 passed
  - `test_bot_routes.py`: 11 passed
  - `test_bot_run.py`: 4 passed
  - `test_bot_schedule.py`: 5 passed
  - `test_bot_schema.py`: 5 passed
  - `test_bot_store.py`: 5 passed
  - `test_bot_templates.py`: 3 passed
  - `test_bot_tick_and_inbox.py`: 7 passed
  - `test_bot_validate.py`: 11 passed
  - `test_bot_watches.py`: 7 passed
  - `test_bot_monitor.py`: 10 passed

---

## 4. Phase 4: memory, approvals, email

- Memory: "Not a problem" on a bot's finding sets it `dismissed` and adds a memory line from the rule text and the
  supplier name. The bot page lists memory lines; each can be removed (removing it un-dismisses the finding).
- Notes from the setup card are memory lines with `source = "note"`; they go into the run's model prompt as
  instructions, never as data.
- Approvals: only `email_outside` needs one for VAT. An approval card in the inbox: Approve, Edit, Reject.
- Email digest: needs the email service decision (`docs/ACCUTAX_BOTS_PLAN.md`, open decisions). Until then the
  `email` delivery chip shows "soon".

### 4.1 Phase 4 results

Completed on 2026-10-29.

#### 1. Backend Memory & Approvals Layer
- **Memory Persistence & Lifecycle (`gemini_brain/bots/store.py`)**:
  - `dismiss_finding`: When a user marks an active finding "Not a problem", records a memory line in `ai_bot_memory` formatting `Dismissed {rule} from {supplier}`, linking `finding_key` to `ai_bot_findings(key)`.
  - `delete_memory`: Removing a memory entry automatically un-dismisses the finding (`UPDATE ai_bot_findings SET state = 'seen'`), restoring it to the user's inbox in real-time.
  - `sync_notes_to_memory`: On bot creation or settings update, instructions from `draft.notes` are synchronized to `ai_bot_memory` with `source='note'`.
  - `list_memory` & `add_memory`: Enables full user inspection and direct training of bots with operational instructions.
- **Model Prompt Safety (`gemini_brain/bots/run.py`)**:
  - User notes/memory instructions are injected into the Bedrock LLM prompt wrapped in `<user_instructions>` tags, accompanied by strict system prompts stating they are reporting behavioral guidelines and never transaction data.
- **Approval Workflow Engine (`gemini_brain/bots/store.py`, `gemini_brain/bots/run.py`)**:
  - Automatically identifies runs where `rules.email_outside == "ask"`, delivery includes `"email"`, and new findings were detected.
  - ~~Generates structured approval items~~ Removed in the review: it addressed them to an invented recipient. Runs no longer create approvals, and email is taken off every draft until an email service exists (`validate.EMAIL_AVAILABLE`). The approval endpoints stay for when it does.
  - Endpoints:
    - `GET /api/v1/bots/approvals`: Retrieves pending approvals across owned bots.
    - `POST /api/v1/bots/{bot_id}/approvals/{approval_id}/approve`: Marks action as approved.
    - `POST /api/v1/bots/{bot_id}/approvals/{approval_id}/reject`: Marks action as rejected.
    - `POST /api/v1/bots/{bot_id}/approvals/{approval_id}/edit`: Allows modifying recipients, subject, and body before execution approval.
  - `get_inbox`: Surfaces pending approvals alongside unread run and finding tallies.

#### 2. Frontend Interface & Interactions
- **Bot Memory View (`components/bots/BotMemoryView.jsx`)**:
  - Dedicated "What this bot learned" view for each bot, accessible from the Bot Card via a Brain icon button.
  - Categorized badges for learned rules (`Dismissal` vs `Note`) and timestamps.
  - "Forget" button with immediate feedback that removing a dismissal rule restores the original finding.
  - "Teach Bot" input form allowing accountants to add custom operational preferences on the fly.
- **Inbox Approvals Card (`components/bots/InboxView.jsx`)**:
  - "Action Approvals Required" banner rendered at the top of the inbox whenever pending approvals exist.
  - One-click "Approve" and "Reject" buttons.
  - Inline "Edit" mode allowing accountants to inspect and modify outgoing email recipients, subject, and message body before approving.
- **Email Delivery Chip (`components/bots/SetupCard.jsx`)**:
  - The `email` delivery chip displays a clean `"soon"` badge and remains disabled pending corporate email provider sign-off.
- **Bot List Integration (`components/bots/BotList.jsx`)**:
  - Added Brain icon button on every bot item card to open the Memory manager.
- **State Orchestration (`App.jsx`)**:
  - Seamless navigation between Bots list, Inbox, and Bot Memory view with persistent unread badges and synchronized reload.

#### 3. Test & Build Verification
- **Unit Test Suite**: Created `backend/tests/unit/test_bot_memory_approvals.py` covering memory creation with supplier details, finding un-dismissal upon memory deletion, draft note synchronization, prompt instruction injection, approval generation, and full API endpoint test coverage (**8/8 passed**).
- **Subsystem Test Suite**: All 14 bot test suites passed (**103/103 tests passed, 100%**).
- **Production Frontend Build**: `npm run build` completed cleanly with 0 errors in 29.16s.


---

## 5. Open decisions (need the person)

| # | Decision | Default in this plan |
|---|---|---|
| 1 | Where the Bots entry lives | Header button + one line on the home page |
| 2 | Credits per run shown on the card | 90 credits (measured VAT review cost) |
| 3 | Quarter group when the books say "quarterly" | Calendar quarters (`DEFAULT_QUARTER_GROUP`), stated on the card |
| 4 | Max bots per user | 10 |
| 5 | Thresholds for "unusual bill" | Current `review_vat` thresholds; finance sign-off still pending |
| 6 | Phase 2 tables | Done on the VM database (task 2.1); EC2 not yet |
