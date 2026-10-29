"""validate.py — Validate and fix a BotDraft on the server.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

from gemini_brain.bots.schema import BotDraft, BotIssue, Schedule
from gemini_brain.bots.watches import all_watches, default_ids

#: Email delivery needs an email service (not chosen yet). Turn on when it exists.
EMAIL_AVAILABLE = False


def validate(
    draft: BotDraft,
    allowed_org_ids: Sequence[int],
    current_org_ids: Sequence[int],
) -> Tuple[BotDraft, List[BotIssue]]:
    """Validate a BotDraft against user permissions and constraints.

    Fixes what safely can be resolved automatically (fixed=True) and
    reports blocking issues that the user must fix (fixed=False).
    Returns a new BotDraft instance and the list of issues.
    """
    issues: List[BotIssue] = []
    questions: List[str] = list(draft.questions)

    # 1. Organization IDs emptiness
    if not draft.org_ids:
        if current_org_ids:
            org_ids = [int(o) for o in current_org_ids]
            issues.append(
                BotIssue(
                    field="org_ids",
                    message="Using your current organization",
                    fixed=True,
                )
            )
        else:
            org_ids = []
            issues.append(
                BotIssue(
                    field="org_ids",
                    message="Pick at least one organization",
                    fixed=False,
                )
            )
    else:
        org_ids = [int(o) for o in draft.org_ids]

    # 2. Check each org id in allowed_org_ids
    allowed_set = {int(o) for o in allowed_org_ids}
    valid_orgs: List[int] = []
    for org in org_ids:
        if org in allowed_set:
            valid_orgs.append(org)
        else:
            issues.append(
                BotIssue(
                    field="org_ids",
                    message=f"Organization {org} removed: you do not have access",
                    fixed=True,
                )
            )
    org_ids = valid_orgs
    if not org_ids and not any(iss.field == "org_ids" and not iss.fixed for iss in issues):
        issues.append(
            BotIssue(
                field="org_ids",
                message="Pick at least one organization",
                fixed=False,
            )
        )

    # 3 & 4. Watches check
    known_watch_ids = {w.id for w in all_watches()}
    valid_watches: List[str] = []
    for wid in draft.watches:
        if wid in known_watch_ids:
            valid_watches.append(wid)
        else:
            issues.append(
                BotIssue(
                    field="watches",
                    message=f"Unknown watch '{wid}' removed",
                    fixed=True,
                )
            )

    if not valid_watches:
        valid_watches = default_ids()
        issues.append(
            BotIssue(
                field="watches",
                message="Added default checks",
                fixed=True,
            )
        )

    # 5, 6, 7, 8. Schedules checks
    valid_schedules: List[Schedule] = []
    if not draft.schedules:
        issues.append(
            BotIssue(
                field="schedules",
                message="Choose when it runs",
                fixed=False,
            )
        )
        if "How often should it run?" not in questions:
            questions.append("How often should it run?")
    else:
        seen_schedules: List[Schedule] = []
        for i, s in enumerate(draft.schedules):
            s_dict = s.model_dump()
            has_error = False

            if s.kind == "weekly" and not s.day:
                issues.append(
                    BotIssue(
                        field=f"schedules[{i}].day",
                        message="Choose a day of the week",
                        fixed=False,
                    )
                )
                has_error = True

            elif s.kind == "monthly" and s.day_of_month is None:
                issues.append(
                    BotIssue(
                        field=f"schedules[{i}].day_of_month",
                        message="Choose a day of the month",
                        fixed=False,
                    )
                )
                has_error = True

            elif s.kind == "before_deadline":
                if not s.days_before:
                    issues.append(
                        BotIssue(
                            field=f"schedules[{i}].days_before",
                            message="Choose days before deadline",
                            fixed=False,
                        )
                    )
                    has_error = True
                else:
                    # 7. days_before values outside 1..60
                    valid_db = [d for d in s.days_before if 1 <= d <= 60]
                    if len(valid_db) != len(s.days_before):
                        issues.append(
                            BotIssue(
                                field=f"schedules[{i}].days_before",
                                message="Removed days_before values outside 1..60",
                                fixed=True,
                            )
                        )
                        s_dict["days_before"] = valid_db
                        if not valid_db:
                            issues.append(
                                BotIssue(
                                    field=f"schedules[{i}].days_before",
                                    message="Choose days before deadline",
                                    fixed=False,
                                )
                            )
                            has_error = True

            s_cand = Schedule(**s_dict)
            # 8. Duplicate schedules
            if s_cand in seen_schedules:
                issues.append(
                    BotIssue(
                        field=f"schedules[{i}]",
                        message="Removed duplicate schedule",
                        fixed=True,
                    )
                )
            else:
                seen_schedules.append(s_cand)
                valid_schedules.append(s_cand)

    # 9. Deliver: the inbox is always on; email is not available until an email service is chosen
    #    (docs/ACCUTAX_BOTS_PLAN.md, open decisions), so it is taken off rather than promised.
    deliver_list = list(draft.deliver)
    if "email" in deliver_list and not EMAIL_AVAILABLE:
        deliver_list = [d for d in deliver_list if d != "email"]
        issues.append(
            BotIssue(
                field="deliver",
                message="Email is not available yet; results go to your inbox",
                fixed=True,
            )
        )
    if "inbox" not in deliver_list:
        deliver_list.insert(0, "inbox")
        issues.append(
            BotIssue(
                field="deliver",
                message="Added inbox delivery",
                fixed=True,
            )
        )

    # 10. Name check
    name_str = draft.name.strip() if draft.name else ""
    if not name_str:
        name_str = "VAT Watch"
        issues.append(
            BotIssue(
                field="name",
                message="Name was empty; set to VAT Watch",
                fixed=True,
            )
        )

    new_draft = BotDraft(
        agent="vat",
        name=name_str,
        org_ids=org_ids,
        schedules=valid_schedules,
        watches=valid_watches,
        sensitivity=draft.sensitivity,
        deliver=deliver_list,
        rules=draft.rules,
        notes=draft.notes,
        questions=questions,
        source=draft.source,
    )

    return new_draft, issues
