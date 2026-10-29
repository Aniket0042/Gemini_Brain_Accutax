"""schedule.py — Next run dates for a bot draft and its filing frequencies.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from typing import Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.config.settings import settings
from gemini_brain.vat_return import periods as tax_periods

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class RunTime:
    at: dt.datetime          # timezone-aware, settings.report_timezone
    schedule_index: int      # which Schedule in draft.schedules
    reason: str              # "weekly", "monthly", "14 days before the VAT return due 28 Nov", "3 days after Q3 ends"


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def _generate_periods(
    today: dt.date,
    frequency: str,
    quarter_group: int = tax_periods.DEFAULT_QUARTER_GROUP,
    periods_count: int = 5,
) -> List[tuple[dt.date, dt.date]]:
    """Generate consecutive tax periods starting from the period prior to `today`."""
    cur_start, cur_end = tax_periods.period_containing(today, frequency, quarter_group)
    prev_start, prev_end = tax_periods.previous_period(cur_start, frequency, quarter_group)

    periods_list = [(prev_start, prev_end), (cur_start, cur_end)]
    ref_day = cur_end + dt.timedelta(days=1)
    for _ in range(periods_count):
        nxt_start, nxt_end = tax_periods.period_containing(ref_day, frequency, quarter_group)
        periods_list.append((nxt_start, nxt_end))
        ref_day = nxt_end + dt.timedelta(days=1)
    return periods_list


def next_runs(
    draft: BotDraft,
    now: dt.datetime,
    frequencies: Dict[int, str],
    count: int = 3,
) -> List[RunTime]:
    """Compute the next `count` run dates strictly after `now`."""
    if not draft.schedules or count <= 0:
        return []

    tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        tz = now.tzinfo

    candidates: List[RunTime] = []
    org_list = draft.org_ids if draft.org_ids else [0]

    for s_idx, s in enumerate(draft.schedules):
        try:
            hour_str, min_str = s.time.split(":")
            shour, smin = int(hour_str), int(min_str)
        except (ValueError, AttributeError):
            shour, smin = 8, 0

        if s.kind == "weekly":
            if not s.day or s.day not in _WEEKDAYS:
                continue
            target_weekday = _WEEKDAYS[s.day]
            found = 0
            for day_offset in range(7 * (count + 2)):
                cand_date = now.date() + dt.timedelta(days=day_offset)
                if cand_date.weekday() == target_weekday:
                    cand_dt = dt.datetime(
                        cand_date.year, cand_date.month, cand_date.day, shour, smin, tzinfo=tz
                    )
                    if cand_dt > now:
                        candidates.append(
                            RunTime(at=cand_dt, schedule_index=s_idx, reason="weekly")
                        )
                        found += 1
                        if found >= count + 1:
                            break

        elif s.kind == "monthly":
            if s.day_of_month is None:
                continue
            dom = s.day_of_month
            found = 0
            for m_offset in range(count + 2):
                y, m = _add_months(now.year, now.month, m_offset)
                try:
                    cand_date = dt.date(y, m, dom)
                except ValueError:
                    continue
                cand_dt = dt.datetime(
                    cand_date.year, cand_date.month, cand_date.day, shour, smin, tzinfo=tz
                )
                if cand_dt > now:
                    candidates.append(
                        RunTime(at=cand_dt, schedule_index=s_idx, reason="monthly")
                    )
                    found += 1
                    if found >= count + 1:
                        break

        elif s.kind == "before_deadline":
            if not s.days_before:
                continue
            # Collect periods for each org
            for org in org_list:
                freq = frequencies.get(org, "quarterly")
                p_list = _generate_periods(now.date(), freq, tax_periods.DEFAULT_QUARTER_GROUP, count + 2)
                for _start, end in p_list:
                    d_date = tax_periods.due_date(end)
                    for db in s.days_before:
                        run_date = d_date - dt.timedelta(days=db)
                        cand_dt = dt.datetime(
                            run_date.year, run_date.month, run_date.day, shour, smin, tzinfo=tz
                        )
                        if cand_dt > now:
                            month_abbr = d_date.strftime("%b")
                            reason = f"{db} days before the VAT return due {d_date.day} {month_abbr}"
                            candidates.append(
                                RunTime(at=cand_dt, schedule_index=s_idx, reason=reason)
                            )

        elif s.kind == "after_period_end":
            days_after = s.days_after if s.days_after is not None else 0
            for org in org_list:
                freq = frequencies.get(org, "quarterly")
                p_list = _generate_periods(now.date(), freq, tax_periods.DEFAULT_QUARTER_GROUP, count + 2)
                for _start, end in p_list:
                    run_date = end + dt.timedelta(days=days_after)
                    cand_dt = dt.datetime(
                        run_date.year, run_date.month, run_date.day, shour, smin, tzinfo=tz
                    )
                    if cand_dt > now:
                        if freq == "monthly":
                            period_label = end.strftime("%b")
                        else:
                            q_num = ((end.month - 1) // 3) + 1
                            period_label = f"Q{q_num}"
                        reason = f"{days_after} days after {period_label} ends"
                        candidates.append(
                            RunTime(at=cand_dt, schedule_index=s_idx, reason=reason)
                        )

    if not candidates:
        return []

    # Deduplicate by `at` timestamp
    # Group by candidate.at: keep unique run times, combining reasons if needed
    merged_by_at: Dict[dt.datetime, RunTime] = {}
    for cand in sorted(candidates, key=lambda r: (r.at, r.schedule_index)):
        if cand.at not in merged_by_at:
            merged_by_at[cand.at] = cand
        else:
            existing = merged_by_at[cand.at]
            if cand.reason != existing.reason and cand.reason not in existing.reason:
                merged_reason = f"{existing.reason}; {cand.reason}"
                merged_by_at[cand.at] = RunTime(
                    at=cand.at,
                    schedule_index=existing.schedule_index,
                    reason=merged_reason,
                )

    sorted_runs = sorted(merged_by_at.values(), key=lambda r: r.at)
    return sorted_runs[:count]
