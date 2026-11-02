"""The model drafts the reminder. Code checks the amount. A person approves
the stages the policy marks.

One model call per message, through llm.structured_completion (the same
boundary as checkpoint 02), returning a Draft with a subject and a body.
Then the one rule Session 5 wrote on the whiteboard for this exact step:
"code checks the amount". Every dollar figure in the text must be the ledger
balance. A draft that fails is replaced by the template, and the audit line
says so (drafted_by = template, DRAFT_REJECTED). The model proposes; the
ledger decides what the number is.
"""
from __future__ import annotations

import re
from datetime import datetime

import errors
from models import Case, Draft, Stage

SYSTEM_PROMPT = (
    "You draft collections reminders for Nimbus Electronics. Write a {tone} "
    "{channel} message to {customer_name} about invoice {invoice_id}: balance "
    "${balance:.2f}, due {invoice_due}, {when}. Under 90 words for email, under "
    "40 for sms. State the exact balance once, as ${balance:.2f}, and no other "
    "amount. Promise nothing beyond these facts: no discounts, no new deadlines. "
    "Mention legal referral only when the tone is final, and then only as 'may "
    "be referred to our legal team'. Return a subject and a body."
)

# What goes out when the model's draft fails the check (or the model fails).
TEMPLATES = {
    "friendly": ("A note about invoice {invoice_id}",
                 "Hello {customer_name}, a friendly reminder that invoice {invoice_id} for "
                 "${balance:.2f} is {when}. If it is already on its way, thank you."),
    "formal": ("Invoice {invoice_id} is overdue",
               "Dear {customer_name}, invoice {invoice_id} for ${balance:.2f} was due on "
               "{invoice_due} and is {when}. Please arrange payment at your earliest convenience."),
    "firm": ("Overdue: invoice {invoice_id}",
             "Dear {customer_name}, invoice {invoice_id} for ${balance:.2f} is {when}. "
             "Please settle it now, or contact us if you believe this is in error."),
    "final": ("Final notice: invoice {invoice_id}",
              "Dear {customer_name}, this is a final notice. Invoice {invoice_id} for "
              "${balance:.2f} is {when}. Without payment or contact it may be referred to "
              "our legal team."),
}

_AMOUNT = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def facts(case: Case, stage: Stage, now: datetime) -> dict:
    days = (now.date() - case.invoice_due).days
    if days < 0:
        when = f"due in {-days} day{'s' if days != -1 else ''}"
    elif days == 0:
        when = "due today"
    else:
        when = f"{days} day{'s' if days != 1 else ''} overdue"
    return {
        "tone": stage.tone, "channel": stage.channel, "customer_name": case.customer_name,
        "invoice_id": case.invoice_id, "balance": case.balance_usd,
        "invoice_due": case.invoice_due.isoformat(), "when": when,
    }


def template_draft(case: Case, stage: Stage, now: datetime) -> Draft:
    subject, body = TEMPLATES[stage.tone]
    f = facts(case, stage, now)
    return Draft(subject=subject.format(**f), body=body.format(**f))


def check_amounts(text: str, balance: float) -> bool:
    """True when the text states the balance, and no other amount."""
    amounts = [float(a.replace(",", "")) for a in _AMOUNT.findall(text)]
    return bool(amounts) and all(abs(a - balance) < 0.005 for a in amounts)


def draft(case: Case, stage: Stage, now: datetime) -> tuple[Draft, str, llm.Usage | None, str | None]:
    """(draft, drafted_by, usage, problem). drafted_by is "model" when the
    model's text passed the amount check, "template" otherwise."""
    # Imported here, not at the top: the provider SDKs take seconds to load, and
    # steps.py (which only needs the templates) should start at once.
    import llm

    f = facts(case, stage, now)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(**f)},
        {"role": "user", "content": f"Write the {stage.name.replace('_', ' ')} now."},
    ]
    try:
        proposed, usage = llm.structured_completion(messages, Draft, temperature=0.4, max_output_tokens=400)
    except errors.DeskhandError as exc:
        return template_draft(case, stage, now), "template", None, exc.internal_error_code
    if check_amounts(f"{proposed.subject}\n{proposed.body}", case.balance_usd):
        return proposed, "model", usage, None
    return template_draft(case, stage, now), "template", usage, errors.DRAFT_REJECTED
