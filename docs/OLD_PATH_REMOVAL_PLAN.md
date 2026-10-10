# Removing the old answer path (SQL fallback, endpoint selector, orchestrator)

Branch: `deploy/vm-3535`. Status: approved 10 Oct 2026 with the decisions in section 6. Nothing is committed, pushed or deployed until the user says go. Written 9 Oct 2026.

Since the 8 Oct cutover (f945ea1, VM `.env` `AGENT_MODE=primary`), every chat is answered by the agent in `agent/`, which reads figures from Cube only. The old path (router, endpoint selector, SQL fallback, finance/SQL agents, orchestrator, multi-org runner) still ships and still runs in tests. This plan removes it.

---

## 1. Review of the pasted plan

The shape is right: tag, measure, move shared code, delete, test, deploy. The import map below changes some of the details.

### Wrong or incomplete

| # | Pasted plan says | What the code shows | Effect if not fixed |
|---|---|---|---|
| 1 | Delete `sql_fallback/` | `sql_fallback/db_connection.get_connection` is the only Postgres pool. `api/auth.py` (login, users), `memory/schema.py` (table setup at startup) and `memory/session_memory.py` (chat threads and history, 15 call sites) all use it. | Login and chat history break. The API does not start. |
| 2 | Keep `fetch_accutax_accessible_orgs` / `accutax_client` "used by auth.py" | `fetch_accutax_accessible_orgs` is defined in `api/auth.py` itself and calls Accutax with `httpx`. `auth.py` does not import `accutax_client`. | Nothing to move. `accutax_client` is only used by the old path, `routes.py` (`active_auth_token`, old path only) and `app.py` shutdown. It becomes dead code (see wave 3). |
| 3 | Keep anything `reports/` still needs | `reports/` (1,481 lines) is imported only by the orchestrator. PDF/CSV/chart export lives in `artifacts/`, which does not import `reports/`. | `reports/` is itself dead. Delete it, do not move `sql_safety` for it. |
| 4 | Not mentioned | `tools/schemas.py` imports `router/dates.py`. But `tools/{chat_index,handlers,schemas,registry,context}` are kept alive only by `config/accutax_openapi.py`, the Accutax OpenAPI refresh that `api/app.py` starts at boot for the endpoint selector. | Deleting `router/` breaks API startup unless the OpenAPI refresh and those tool modules go too. |
| 5 | Not mentioned | `tests/unit/conftest.py` (uncommitted change) has an autouse fixture that imports `orchestrator.gemini_brain_runner`. | Every unit test errors at setup after the delete. |
| 6 | Not mentioned | `scripts/eval/replay_real_questions.py` imports the orchestrator, `multi_org`, `conversation_window` and `sql_fallback` at module top. Default is `--path current`. | The "after" eval run cannot start. |
| 7 | Not mentioned | `agent/shadow.py` is a hook of the old path (shadow mode runs the agent beside the old answer). `semantic/shadow.py` and `semantic/metric_table.py` are the Phase 1 Cube-shadow bridge for `multi_org`. | Dead after the delete. Remove them in the same change. |
| 8 | "Old tests (~100 files)" | 98 test files in total. 55 touch the old path. 43 are clean. Of the 55, about 12 test things that stay (tenant isolation, org access, session scope, DB pool, SQL safety, guide loader, VAT KB, policy verifier) and must be rewritten against the agent path, not deleted. | Deleting all 55 drops the only tests for tenant isolation and session scoping on the query routes. |
| 9 | Rollback is git revert + redeploy | Correct, but the revert only works if the VM `.env` still has what the old code needs (`AGENT_MODE`, Gemini key, service-account login). | Keep the VM `.env` keys untouched until the soak ends. Remove them in a later, separate change. |
| 10 | Not mentioned | The working tree has uncommitted changes from another session: `conftest.py`, 4 test files, `replay_real_questions.py` (relative-date tokens the golden set needs), and the untracked golden set (`golden_accounting.json`, `golden_sheet.py`, `golden_manual_test.xlsx`). | The rollback tag would not contain the eval harness the before/after comparison uses. |
| 11 | Not mentioned | `git pull` on the VM removes deleted `.py` files but leaves their `__pycache__/` folders. An empty `router/` with `__pycache__` imports as a namespace package. | Harmless but confusing. Clean `__pycache__` after the pull. |

### Confirmed

- `agent/law.py:55` imports `DIRECT_ANSWER_SYSTEM_PROMPT` from `orchestrator/gemini_brain_runner.py:363`. (Correction, 10 Oct: `scripts/eval/vat_kb_eval.py` imports `GeminiBrainRunner`, not the prompt; it drives the old path.)
- `routes.py` old-path parts: `run_query` and `stream_query` bodies after the agent branch, `run_query_all_models` (`POST /query/all`), `_org_meta`, `_multi_org_run_kwargs`, `agent_shadow` calls, `GeminiBrainRunner`/`run_multi_org`/`plan_query` imports.
- `agent/preview.py`: `primary()`, `allowed()`, `requested()`, `_allowlist()`, `MODEL_LABEL` exist only for the switch and the preview allowlist.
- Frontend: `PolicyPicker.jsx` (`ALL_MODELS`, model menu), `api.js` (`/query/all` call), `ResponseView.jsx` (`ModelAnswerCard`, `MultiModelResponseCard`), `App.jsx:1193` (`use_api`) and `App.jsx:1212` (`/query/all` branch), `QueryInput.jsx:144` (`picker_hidden`).

---

## 2. Import map (what stays alive after the delete)

Computed with an AST reachability walk from `api/app.py`, `api/auth.py`, `agent/*` and `semantic/*`, treating the five old packages as removed.

### Live code that imports an old package

| Importer | Imports | Action |
|---|---|---|
| `agent/law.py:55` | `orchestrator.gemini_brain_runner.DIRECT_ANSWER_SYSTEM_PROMPT` | Move the prompt to `vat_kb/prompts.py` |
| `api/auth.py:18` | `sql_fallback.db_connection.get_connection` | Move module to `db/connection.py` |
| `memory/schema.py:18` | same | same |
| `memory/session_memory.py:18` | same | same |
| `tools/formatters.py:932` | same (lazy) | same |
| `tools/handlers.py:37` | `sql_fallback.db_connection.execute_sql_function` | Delete `tools/handlers.py` (wave 2) |
| `tools/schemas.py:13` | `router.dates` | Delete `tools/schemas.py` (wave 2) |
| `semantic/shadow.py:74` | `orchestrator.multi_org_metrics` | Delete `semantic/shadow.py` (wave 2) |
| `api/routes.py:52-56` | `GeminiBrainRunner`, `run_multi_org*`, `plan_query` | Remove with the old-path routes |
| `gemini_brain/__init__.py:19` | `GeminiBrainRunner` (lazy re-export) | Remove the re-export |
| `scripts/eval/replay_real_questions.py` | orchestrator, multi_org, conversation_window, db_connection | Make agent-only |
| `scripts/eval/vat_kb_eval.py` | `GeminiBrainRunner` | Delete in commit D, or rewrite against `agent/law.py` |
| `scripts/eval/{cube_golden,cube_model_check,cube_reconcile,golden_sheet}.py` | `db_connection` | Repoint |
| `tests/unit/conftest.py` | `orchestrator.gemini_brain_runner` | Remove that fixture |

### Dead code that would break if left behind (goes with wave 1)

These are dead after the delete, but they import a deleted package. Left in place, they fail on import, and `api/app.py` imports the OpenAPI refresh at startup. So they go in the wave 1 commit. Rule for wave 1: when it is done, no file imports a deleted package.

- `reports/` (whole package; `reports/engine.py` imports `sql_fallback` at module top)
- `tools/chat_index.py`, `tools/handlers.py`, `tools/schemas.py`, `tools/registry.py`, `tools/context.py` (`tools/schemas.py` imports `router.dates`; keep `tools/formatters.py`, `artifacts/` uses it)
- `config/accutax_openapi.py` and the refresh hooks in `api/app.py:103` (it pulls in `tools/chat_index.py`)
- `semantic/shadow.py` (lazy import of `orchestrator.multi_org_metrics`)

### Code that becomes dead once the old path goes (wave 2)

Nothing live reaches these after wave 1, and none of them import a deleted package, so they still import cleanly in the meantime.

- `cache/`, `classification/`
- `config/api_catalog.py` (check with grep that only deleted modules used it)
- `reasoning/claude_reasoner.py`, `reasoning/complexity_judge.py`, `reasoning/model_selector.py`, `reasoning/gemini_adapter.py` (keep `bedrock_client.py`)
- `memory/conversation_window.py`, `memory/state_extractor.py`
- `policy/auto.py`, `policy/registry.py`, `policy/effort.py`, `policy/__init__.py` exports (keep `policy/verifier.py`; `agent/loop.py` uses it)
- `semantic/metric_table.py`
- `formatting/empty_answer.py` (keep `formatting/markdown.py`; `resilience/envelope.py` uses it)
- `agent/shadow.py`

### Wave 3 (later, its own change, after one week without the old path)

- `api_client/accutax_client.py`, `auth/service_token.py`, `auth/token_monitor.py`: the service-account Accutax login at startup. The agent makes no Accutax REST calls. Login org lists use `httpx` in `api/auth.py`. If nothing else needs a service token, remove these and the startup hooks in `api/app.py:79-129`.
- `health/model_health_checker.py`: pings Gemini, Accutax REST and both Bedrock models. Trim to what the agent uses (Bedrock agent model, Cube, Postgres).
- Gemini dependency: after wave 2, check whether `memory/session_memory.py` (`call_gemini` parameter, `GEMINI_MODEL`) still calls Gemini. If not, drop `google-genai` from `pyproject.toml`.

### Settings

Unused after waves 1–2 (remove from `config/settings.py` and `.env.example`; each setting goes in the commit that removes its last user):
`agent_mode`, `agent_preview_users`, `agent_shadow_log`, `multi_org_fetch_deadline_seconds`, `multi_org_plan_log`, `verify_enforce`, `accutax_org_id`, `metrics_backend`, `metrics_shadow_log`, `bedrock_model_id_fast` (after `model_selector` and health trim).

Stay: `db_name_allowlist` and `db_pool_*` (used by the moved `db/connection.py`), `multi_org_enabled`, `max_orgs_per_query`, `show_*_traces`, all `agent_*` except the three above, `vat_kb_*`, `cube_*`, `report_narrative_*`, `artifact_*`.

Wave 3 only: `gemini_api_key`, `accutax_auth_token`, `accutax_service_email`, `accutax_service_password`, `accutax_user_id`.

Do not edit the VM `.env` in this change. Unknown keys are ignored, and the rollback needs them.

---

## 3. Tests

98 files. 43 do not touch the old path and stay as they are. The 55 that do split as follows.

### Rewrite against the agent path (security and kept modules)

| File | Why it stays |
|---|---|
| `tests/test_tenant_isolation_api.py` | Route-level tenant isolation. Patch `agent_preview.answer` instead of `GeminiBrainRunner`. |
| `tests/unit/test_org_access.py` | Org allow-list on `/query` and `/query/stream`. Drop the `/query/all` cases. |
| `tests/unit/test_security_and_org.py` | Same. |
| `tests/unit/test_session_scope.py` | `_match_session_scope` (409 on a different org selection) stays in `routes.py`. |
| `tests/unit/test_api_routes.py`, `tests/unit/test_api_resilience.py` | Route behaviour and error envelopes. |
| `tests/unit/test_agent.py`, `tests/unit/test_agent_preview.py` | Remove `AGENT_MODE` / allowlist cases; keep the rest. |
| `tests/unit/test_db_pool.py`, `tests/unit/test_safety_and_token_monitor.py` | Repoint imports to `db/connection.py`. Drop SQL-safety cases if `sql_safety.py` goes. |
| `tests/unit/test_guide_loader.py` | `knowledge/guide_loader` is used by `agent/tools.py`. Drop the orchestrator import. |
| `tests/unit/test_vat_kb_integration.py` | Keep the knowledge-base cases. Move the law cases to `agent/law.py`. |
| `tests/unit/test_policy.py` | Keep the `verify_answer` cases only. |
| `tests/unit/test_observability.py`, `tests/unit/test_pii_pipeline.py` | Drop the classification / orchestrator parts, keep tracer and redaction. |

### Delete (they test removed code only)

`test_multi_turn_routing`, `test_accutax_openapi`, `test_chat_index`, `test_conversation_memory`, `test_dates`, `test_fast_router`, `test_finance_agent_ranking`, `test_finance_agent_soft_delete`, `test_force_org_params`, `test_keyword_fallback`, `test_llm_router`, `test_metric_table_and_shadow`, `test_multi_org` and the 13 `test_multi_org_*` files, `test_narration_budget`, `test_orchestrator_outcomes`, `test_org_ignored_reports`, `test_phase1`, `test_phase3`, `test_phase4`, `test_report_phase0`, `test_resilience_matrix`, `test_routing_consolidation`, `test_self_correction`, `test_sql_engine_cost_optimizations`, `test_sql_engine_forced_answer`, `test_sql_reports`, `test_window_widening`.

Also delete `tests/parity/run_parity_suite.py` and the old-path data files in `tests/data/` that only those tests read (`golden_routing_queries.json`, `multi_org_eval_cases.json`, `baseline_results.json`, `phase_b_results.json`, `phase_c_results.json`). Check each with grep first.

### Before the delete, record the baseline

Run the full unit suite on the tagged commit and save the pass/fail list. 44 failures are known in a clean worktree without `.env`; compare on the same machine with the same `.env` so new failures are real.

---

## 4. Step-by-step plan

Commit, push, tag push and every VM step happen only when the user says go. Local edits and local test runs do not need a go.

### Commit order

| # | Commit | Contents | When |
|---|---|---|---|
| A | Other session's test fixes | `conftest.py`, `test_force_org_params.py`, `test_pii_pipeline.py`, `test_security_and_org.py`, `test_window_widening.py` | Only if the full unit suite passes with them. If it fails, stash them instead. Never mixed into B–E. |
| B | Eval set and harness | `scripts/eval/replay_real_questions.py` (date tokens), `scripts/eval/golden_sheet.py`, `tests/data/golden_accounting.json`, `docs/golden_manual_test.xlsx`, this plan | Before the tag |
| — | Tag `pre-old-path-removal` | Annotated tag on the last of A/B | Before any move or delete |
| C | Move shared code | Step 3 | After the before-score |
| D | Delete wave 1 | Step 4 | After C passes all checks |
| E | Delete wave 2 | Step 5 | After D passes all checks |

C, D and E stay separate so each can be reverted alone.

### Step 0 — Clean the working tree

1. Run the full unit suite with the other session's edits in place (decision 1). Save the pass/fail list.
2. Passes: commit A. Fails: `git stash push -- <the 5 files>` and record the stash name here.
3. Commit B.
Result on 10 Oct, at 66e2db2 with the five edits in place: 2514 passed, 27 skipped, 9 errors, in 6 min 10 s. All 9 errors are in `tests/test_tenant_isolation_api.py`: its fixture calls `GeminiBrainRunner._resolve_organization`, a method that no commit in `src` has ever defined. That file sits outside `tests/unit/`, so the edited `conftest.py` does not apply to it, and the errors do not come from the edits. It is on the rewrite list in section 3. Until it is rewritten, route-level tenant isolation is covered only by `test_org_access.py`, `test_security_and_org.py` and `test_session_scope.py`.

4. `git status` must show none of the user's files as changed or untracked. Untracked files that belong to other work (`docs/*.docx`, `docs/AI queries.xlsx`, blueprint docs, `docs/USAGE_LIMITS_DESIGN.md`) are left as they are unless the user says otherwise.

### Step 1 — Tag the rollback point

```bash
git tag -a pre-old-path-removal -m "Last commit with the old answer path (router, SQL fallback, orchestrator)"
git push origin pre-old-path-removal
git ls-remote --tags origin
```

The last command must list `refs/tags/pre-old-path-removal`. Push the branch too (`git push origin deploy/vm-3535`), so the VM can fetch commits A and B.

On the VM, record the deployed commit: `git -C /opt/accutax-ai rev-parse HEAD`.

### Step 2 — Before score on the VM (currently deployed code, before any delete)

1. Build the eval copy in `/tmp/agent_eval/backend` from the deployed source as before (geminibrain user, `/opt/accutax-ai/venv`, `.env` symlink). Add the harness and golden set from commit B; the deployed commit does not have them. The live service stays untouched.
2. Smoke the golden set through the harness first: `--path agent --cases tests/data/golden_accounting.json --only <2 ids>`. The golden cases have the same core keys (`id`, `question`, `orgs`, `expect`, `context`, `kind`) but no `asked`/`old_answer`. A read of the harness (10 Oct) shows it needs only `id`, `kind`, `orgs`, `question` and reads the rest with `.get`, so it should work as is. The smoke run checks the `expect` reference specs. If the harness needs a fix, put it in commit B before tagging.
3. One run each, `--path agent`: 89 real questions, 76 golden questions. Do not run `--path current`; it is not what users get and it hits Gemini quota limits.
4. Grade by hand, as for earlier runs. Save reports and grades in the scratchpad. Cost is about $3.50 for both sets at $0.021 per question.

The agent code does not change in this plan, so after the delete the answers should match within known variance (VAT-law answers r128 and r131 flip between runs). Compare case by case, not only the totals.

Before-score, 10 Oct 2026 (VM on 66e2db2, eval copy in `/tmp/agent_eval_removal/before`, graded by hand):

| Set | Pass | Partial | Fail | p50 | p95 | Cost |
|---|---|---|---|---|---|---|
| Real (89) | 80 | 9 | 0 | 8.2 s | 22.8 s | $2.13 |
| Golden (76) | 67 | 7 | 2 | 9.0 s | 22.1 s | $1.54 |

Golden fails: APS-02 and DOC-FIN-C03. Six golden cases that expect a refusal (EXP-L03, JE-L01, INS-01, CF-01, REVF-01, CF-08) now have data behind them and were graded on correctness. The after-score uses the same grades file and the same rules.

### Step 3 — Move the shared code (commit C, behaviour unchanged)

1. Create `gemini_brain/db/__init__.py` and `gemini_brain/db/connection.py`. Move the whole of `sql_fallback/db_connection.py` (pool, `get_connection`, `organization_exists`, `execute_sql_function`, `close_pools`). Leave a one-line re-export in `sql_fallback/db_connection.py` so the old code still runs until commit D.
2. Repoint `api/auth.py`, `memory/schema.py`, `memory/session_memory.py`, `tools/formatters.py`, `scripts/eval/{cube_golden,cube_model_check,cube_reconcile,golden_sheet,replay_real_questions}.py` and `tests/unit/test_db_pool.py` to `gemini_brain.db.connection`.
3. Move `DIRECT_ANSWER_SYSTEM_PROMPT` into `vat_kb/prompts.py`. Import it from there in `agent/law.py` and (until commit D) `gemini_brain_runner.py`.

As done (10 Oct): the module was moved with `git mv`, and `sql_fallback/db_connection.py` became a shim that puts `gemini_brain.db.connection` in `sys.modules` under the old name. Both paths are the same module object, so a test that patches either path patches the same functions. The moved prompt was checked to be the identical string.
4. Checks: full unit suite equals the step 0 baseline; node semantic tests pass; `python -c "import gemini_brain.api.app"` works.

### Step 4 — Delete wave 1 (commit D)

Backend:

1. `api/routes.py`
   - `run_query` and `stream_query`: always call the agent. Keep `_query_orgs` (org authorization, session scope, multi-org flag). Wrap `_preview_events` and the sync call in the same `ValueError` → 400 and `classify_exception` handling the old path had.
   - Delete `run_query_all_models` (`POST /query/all`), `_org_meta`, `_multi_org_run_kwargs`, `_preview_scope`, the `agent_shadow` calls, and the imports of `GeminiBrainRunner`, `run_multi_org`, `run_multi_org_stream`, `plan_query`, `choose_policy`, `list_models`, `EFFORT_*`, `active_auth_token`.
   - `GET /models`: keep for one release (decision 4). It always returns the single agent entry with `picker_hidden=True`.
   - Remove `fetch_organizations_from_db` and `Counter` imports if `_org_meta` was their last user.
2. `agent/preview.py`: delete `primary()`, `allowed()`, `requested()`, `_allowlist()`, `MODEL_LABEL`. No rename.
3. `api/models.py`: drop `MultiModelQueryResponse`. Keep `model`, `effort`, `use_api`, `selected_model_key` on `QueryRequest` and keep `EffortInfo` for one release (decision 4).
4. `gemini_brain/__init__.py`: remove the `GeminiBrainRunner` re-export.
5. Delete `router/`, `sql_fallback/`, `endpoints/`, `agents/`, `orchestrator/`.
6. Delete the "would break if left behind" list in section 2: `reports/`, the five `tools/` modules, `config/accutax_openapi.py` with its hooks in `api/app.py`, `semantic/shadow.py`.
7. `config/settings.py` and `.env.example`: remove `agent_mode`, `agent_preview_users`, `multi_org_fetch_deadline_seconds`, `multi_org_plan_log`, `verify_enforce`, `accutax_org_id`, `metrics_backend`, `metrics_shadow_log`. `agent_shadow_log` goes in commit E with `agent/shadow.py`. Grep each one first.
8. `tests/unit/conftest.py`: remove the `organization_exists` fixture.
9. Tests: rewrite and delete per section 3.
10. `scripts/eval/vat_kb_eval.py`: delete, or rewrite against `agent/law.py`. `scripts/eval/replay_real_questions.py`: remove `--path current` and the old-path imports; the in-memory session store stays. Delete scripts that only drive the old path: `evaluate_routing_harness.py`, `multi_org_eval.py`, `multi_org_plan_report.py`, `shadow_review.py`. Grep each first.
11. Delete every `__pycache__/` under `backend/`.
12. Leftover grep. Must return nothing outside `docs/` and `scripts/archive/`:

```bash
grep -rnE "gemini_brain\.(router|sql_fallback|endpoints|agents|orchestrator)|GeminiBrainRunner|AGENT_MODE|agent_mode|agent_preview_users|query/all" backend/src backend/tests backend/scripts frontend/src
```

Frontend:

1. `PolicyPicker.jsx`: delete the file, or reduce it to nothing that `QueryInput.jsx` imports. Remove `ModelMenu` from `QueryInput.jsx:3` and `:144`.
2. `services/api.js`: delete the `/query/all` function.
3. `ResponseView.jsx`: delete `ModelAnswerCard` and `MultiModelResponseCard`.
4. `App.jsx`: delete the `/query/all` branch (`:1212`) and the "All Models" handling. The `model`, `effort`, `use_api` request fields may stay until the backend fields go.
5. `AnswerProvenance.jsx`: remove the model and effort labels if they only came from the picker.
6. `index.css`: remove the picker and multi-model styles.
7. `npm run build` passes with no missing-import warnings.

Checks for commit D (all must pass before commit E starts):

1. Full unit suite: `pytest tests -q` from `backend/`. Compare with the step 0 baseline. Every difference must be explained.
2. `node --test semantic/tests/*.test.js` (tenant guard in `cube.js`).
3. `python -c "import gemini_brain.api.app"` in the project venv, to catch a missing import that tests patch over.
4. Start the API locally and check `/api/v1/health`, login, `/tenants`, `/sessions`, `/models`, one `/query/stream`.

### Step 5 — Delete wave 2 (commit E)

1. Delete the wave 2 list in section 2, and `agent_shadow_log` from settings.
2. Keep `bedrock_model_id_fast`; the health checker still reads it. Its removal belongs to the wave 3 health trim.
3. Rewrite `tests/unit/test_policy.py` to the `verify_answer` cases if not already done in D.
4. Same checks as commit D.

### Step 6 — Commit and push

Only when the user says go.

### Step 7 — Deploy to the VM

Backups first, as root:

```bash
cp /opt/accutax-ai/.env /opt/accutax-ai/.env.bak-pre-removal
cp -a /opt/accutax-ai/frontend/dist /opt/accutax-ai-frontend-dist.bak-pre-removal
cp -a ~cube/semantic ~cube/semantic.bak-pre-removal
```

Do not edit `.env` in this change. `AGENT_MODE` and the old keys stay; unknown keys are ignored, and the rollback needs them.

Then:

1. `cd /opt/accutax-ai && git pull`
2. `find backend -name __pycache__ -type d -prune -exec rm -rf {} +`
3. `pip install -e "backend[vat_kb]"` (venv), then `cd frontend && npm run build`
4. `chown -R geminibrain /opt/accutax-ai`, then `chown root:root semantic/cube.env && chmod 600 semantic/cube.env`
5. `systemctl restart accutax-ai-api`
6. `journalctl -u accutax-ai-api -n 100 --no-pager`: no `ImportError` or `ModuleNotFoundError` at startup.

Deploy D and E one at a time; that gives a cleaner signal if a smoke test fails.

### Step 8 — Smoke test on the VM

| Check | Pass when |
|---|---|
| `/api/v1/health` | 200 |
| Login, org list | Orgs match the Accutax switcher |
| Revenue ("revenue this month") | Figure matches Cube for the org |
| Invoice list with links | Rows link to the Accutax invoice pages |
| VAT law ("is VAT charged on exports?") | Knowledge-base answer with citations |
| How-to ("how do I create a credit note?") | Guide answer with an app link |
| PDF export ("revenue by month as PDF") | File downloads and opens |
| Multi-org (two orgs selected) | Both orgs answered, labelled by name |
| Thread history | Reopen the chat; earlier turns load |
| No model picker | Composer shows no picker |

Then the after-score: the step 2 eval (`--path agent`, 89 real + 76 golden, one run each) on the deployed commit. Compare case by case with the before-score.

### Step 9 — After the soak (separate changes)

- Remove `GET /models` and the ignored `QueryRequest` fields, backend and frontend together.
- Remove `AGENT_MODE`, `AGENT_PREVIEW_USERS`, `AGENT_SHADOW_LOG` and old-path keys from the VM `.env` (back it up first). After this, rolling back to the tag also needs the `.env` backup.
- Wave 3, one week after the old path is gone (decision 3).
- Update `docs/PHASE2_AGENT.md` and the README architecture section.

---

## 5. Rollback

### Emergency rollback (VM, as root)

```bash
cd /opt/accutax-ai   && git fetch --tags   && git checkout pre-old-path-removal   && find backend -name __pycache__ -type d -prune -exec rm -rf {} +   && rm -rf frontend/dist && cp -a /opt/accutax-ai-frontend-dist.bak-pre-removal frontend/dist   && chown -R geminibrain /opt/accutax-ai   && chown root:root semantic/cube.env && chmod 600 semantic/cube.env   && systemctl restart accutax-ai-api   && git log -1 --oneline
```

Every step is joined with `&&`, so a failed fetch or checkout (for example, local changes on the VM) stops the command before the restart. The service never restarts on the new code while looking rolled back. The frontend comes back from the dist backup taken in step 7, so no build is needed. The last line prints the commit now checked out; it must be the tag's commit.

Then:

1. `journalctl -u accutax-ai-api -n 100 --no-pager`: no import errors at startup.
2. `.env` is not edited in this change, so no restore is needed. If it ever is: `cp /opt/accutax-ai/.env.bak-pre-removal /opt/accutax-ai/.env`.
3. Cube is not changed by this plan. `~cube/semantic.bak-pre-removal` is there if it is ever needed.
4. Fix the branch properly: `git revert` the delete commits (E, then D, and C if needed) on `deploy/vm-3535`, push, and move the VM back onto the branch (`git checkout deploy/vm-3535 && git pull`, then the step 7 deploy).

If the dist backup is missing, rebuild instead of the `rm`/`cp` line: `(cd frontend && npm run build)`.

### Normal rollback

`git revert` the delete commits, push, redeploy as in step 7. The VM `.env` still has `AGENT_MODE=primary`, so the reverted code behaves as it does today.

---

## 6. Decisions (made 10 Oct 2026)

1. Other session's uncommitted test edits: run the full unit suite with them. Pass: commit them as their own commit (A) before the tag. Fail: stash them. Never mix them into the delete commits. Outcome: committed as A; the 9 errors in `tests/test_tenant_isolation_api.py` predate the edits.
2. Wave 2 goes in its own commit (E), after wave 1 passes all checks.
3. Wave 3 later, as its own change, after one week without the old path.
4. Keep `GET /models` and the old `QueryRequest` fields for one release.
