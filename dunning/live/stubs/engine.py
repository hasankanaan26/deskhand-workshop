"""The engine: one entry point, five responsibilities.

    evaluate the case  -> what is true right now? (balance, status, policy version)
    choose             -> rules.choose_next_action, the single decision point
    carry out          -> draft (the model), check the amount (code), write the outbox
    wait               -> record when to wake up, and exit; nothing stays in memory
    escalate           -> to a person: final notices, disputes, anything odd

Already written, below the pass: record() (every audit line carries the
policy version), the doors (a payment, a reply, a person's decision: each
records what happened and then calls evaluate) and tick() (the scheduler's
question: which cases are due?).

THE PASS IS BUILT LIVE IN CLASS (build 2). This docstring is the spec;
tests/test_histories.py, test_idempotency.py and test_policy.py are the
acceptance tests. In the starter evaluate() is a placeholder that decides
nothing, so the doors still record what happens to a case and nothing
acts on it.

evaluate(case_id, trigger, db_path=None) is THE entry point. It holds
_lock_for(case_id) for the whole pass, takes now = clock.now(db_path), writes
obslog "pass.started" and "pass.finished" lines, and runs these steps. Every
audit line goes through record(). The actor is "system" unless noted.

1. Evaluate the case.
   - store.get_case; an unknown id raises KeyError.
   - Not open (settled, escalated): stop. decision = "nothing: case is <status>".
   - policy_module.get(case.tenant, db_path). On errors.DeskhandError: status
     escalated, next_due_at None, save, case.escalated {reason: the error's
     internal_error_code, detail}; stop, with "escalate" in the decision.
   - policy.version != case.policy_version: set it, save, policy.applied
     {from_version, to_version, tenant}.
   - Paused and now >= paused_until: status active, paused_until None, save,
     case.resumed {reason: "promise date passed"}.
2. Choose: rules.choose_next_action(case, policy, now, store.sent_times(case_id, db_path)).
   Each name in decision.skipped: stage_history[name] = "skipped", save,
   stage.skipped {stage, reason: "superseded by a later stage"}.
3. Carry the decision out.
   send     The text: when case.approved_stage is this stage and there is a
            pending_draft, a person approved that exact draft, so use it
            (drafted_by from the last approval.requested event's data,
            default "model"); if it no longer passes
            drafting.check_amounts (its subject and body joined by a
            newline, against balance_usd), use
            drafting.template_draft instead, drafted_by "template", problem
            errors.DRAFT_REJECTED. Otherwise drafting.draft(case, stage, now)
            returns (text, drafted_by, usage, problem).
            The outbox: key = f"{case_id}:{stage.name}:{case.policy_version}",
            store.insert_message(key, ...). False means this key was already
            sent: record reminder.skipped_duplicate {stage, message_key}
            instead of the two lines below, and carry on. True:
            draft.checked {stage, result "ok" or
            "replaced" (when problem is set), drafted_by, problem, cost_usd},
            then reminder.sent {stage, channel, tone, balance_usd,
            message_key, subject} with actor "model" when the model drafted
            it, else "system"; a stage with tone "final" also records
            case.flagged {flag: "pre_legal", stage}.
            Then stage_history[stage] = "sent", approved_stage, pending_stage
            and pending_draft None, save, and choose again (step 2): the next
            stage is not due yet, so a pass usually ends in a wait.
   defer,   next_due_at = decision.until, claimed_at None, save;
   wait     queues.from_env().schedule(case_id, until), or .cancel(case_id)
            when until is None. A defer records reminder.deferred {stage,
            reason, until as ISO}.
   park     drafting.draft for the stage; status awaiting_approval,
            pending_stage, pending_draft, next_due_at None, claimed_at None,
            save; cancel the queue entry; approval.requested {stage,
            balance_usd, drafted_by, subject, body, cost_usd}.
   settle   status settled; next_due_at, claimed_at, paused_until None; save;
            cancel; balance.settled {balance_usd}.
   escalate status escalated; next_due_at, claimed_at None; save; cancel;
            case.escalated {reason: decision.reason}.
   cost_usd is round(usage.cost_usd, 6) when there is a usage, else 0.0.
4. Return {case_id, trigger, now (ISO), decision, events_added, log}:
   decision is the describe() of the pass's last Decision (after a soft
   reminder is sent: "wait until 2026-11-09T00:00:00+00:00"), events_added
   counts the audit lines this pass wrote, log is a list of short lines.

A plain loop is enough. The reference build is a LangGraph StateGraph with a
node per step and no checkpointer: the case row is the durable thing.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import clock
import drafting
import errors
import inbound
import obslog
import policy as policy_module
import queues as queue_module
import rules
import store
from models import Case, Decision


# ---------------------------------------------------------------------------
# Writing the audit trail: one function, so every line carries the version
# ---------------------------------------------------------------------------

def record(case: Case, at: datetime, kind: str, actor: str, data: dict, db_path: Path | None) -> int:
    """An audit row in SQLite plus a mirror line in logs/runs.jsonl, with the
    policy version in force on both."""
    seq = store.append_event(case.case_id, at, kind, actor, case.policy_version, data, db_path)
    obslog.log_event(case.case_id, kind, at=at.isoformat(), actor=actor,
                     policy_version=case.policy_version, seq=seq, **data)
    return seq


# ---------------------------------------------------------------------------
# The pass: built live in class (build 2)
# ---------------------------------------------------------------------------

_case_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


def _lock_for(case_id: str) -> threading.Lock:
    with _locks_lock:
        return _case_locks.setdefault(case_id, threading.Lock())


def evaluate(case_id: str, trigger: str, db_path: Path | None = None) -> dict:
    """THE entry point. Every timer, event and decision ends up here.
    Not built yet: it records nothing and decides nothing."""
    return {"case_id": case_id, "trigger": trigger, "now": clock.now(db_path).isoformat(),
            "decision": "nothing: the engine is not built yet", "events_added": 0, "log": []}


# ---------------------------------------------------------------------------
# The other doors: every one of them records an event, then evaluates
# ---------------------------------------------------------------------------

def record_payment(case_id: str, amount_usd: float, db_path: Path | None = None) -> dict:
    """The ledger's webhook. No model anywhere near it."""
    if amount_usd <= 0:
        raise ValueError("a payment is a positive amount")
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        now = clock.now(db_path)
        case.balance_usd = round(case.balance_usd - amount_usd, 2)
        store.save_case(case, db_path)
        record(case, now, "payment.received", "customer",
               {"amount_usd": amount_usd, "balance_usd": case.balance_usd}, db_path)
    return evaluate(case_id, "payment", db_path)


def record_inbound(case_id: str, channel: str, text: str, db_path: Path | None = None) -> dict:
    """A customer's reply: the model classifies, code applies, evaluate runs."""
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        now = clock.now(db_path)
        intent, usage = inbound.classify(text, case, now)
        record(case, now, "inbound.classified", "model",
               {"channel": channel, "intent": intent.intent, "confidence": intent.confidence,
                "summary": intent.summary, "promised_date": intent.promised_date,
                "disputed_amount_usd": intent.disputed_amount_usd, "text": text[:500],
                "cost_usd": round(usage.cost_usd, 6)}, db_path)
        for kind, actor, data in inbound.apply(case, intent, now):
            store.save_case(case, db_path)
            record(case, now, kind, actor, data, db_path)
        store.save_case(case, db_path)
        if case.status == "paused" and case.next_due_at:
            queue_module.from_env().schedule(case.case_id, case.next_due_at)
        elif case.status in ("disputed", "escalated"):
            queue_module.from_env().cancel(case.case_id)
    result = evaluate(case_id, "inbound", db_path)
    result["intent"] = intent.model_dump()
    return result


def decide_approval(case_id: str, approved: bool, by: str, db_path: Path | None = None) -> dict:
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status != "awaiting_approval" or not case.pending_stage:
            raise PermissionError(f"case is {case.status}, not awaiting_approval")
        now = clock.now(db_path)
        record(case, now, "approval.decided", "person",
               {"stage": case.pending_stage, "approved": approved, "by": by}, db_path)
        if approved:
            case.approved_stage = case.pending_stage
            case.status = "active"
            case.owner = by
        else:
            case.status = "escalated"
            case.owner = by
            case.next_due_at = None
            store.save_case(case, db_path)
            record(case, now, "case.escalated", "person",
                   {"reason": f"{case.pending_stage} rejected", "by": by}, db_path)
        store.save_case(case, db_path)
    return evaluate(case_id, "approval", db_path)


def resolve_dispute(case_id: str, credit_usd: float, note: str, by: str, db_path: Path | None = None) -> dict:
    if credit_usd < 0:
        raise ValueError("a credit is zero or more")
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status != "disputed":
            raise PermissionError(f"case is {case.status}, not disputed")
        now = clock.now(db_path)
        case.balance_usd = round(case.balance_usd - credit_usd, 2)
        case.status = "active"
        case.owner = by
        store.save_case(case, db_path)
        record(case, now, "dispute.resolved", "person",
               {"credit_usd": credit_usd, "note": note, "by": by, "balance_usd": case.balance_usd}, db_path)
        record(case, now, "case.resumed", "person", {"reason": "dispute resolved"}, db_path)
    return evaluate(case_id, "dispute_resolved", db_path)


def resume_case(case_id: str, by: str, db_path: Path | None = None) -> dict:
    with _lock_for(case_id):
        case = store.get_case(case_id, db_path)
        if case is None:
            raise KeyError(case_id)
        if case.status not in ("escalated", "paused"):
            raise PermissionError(f"case is {case.status}; only escalated or paused cases resume")
        now = clock.now(db_path)
        case.status = "active"
        case.paused_until = None
        case.owner = by
        store.save_case(case, db_path)
        record(case, now, "case.resumed", "person", {"by": by}, db_path)
    return evaluate(case_id, "resumed", db_path)


def tick(db_path: Path | None = None) -> list[dict]:
    """One pass of the scheduler: wake every case whose time has come."""
    q = queue_module.from_env()
    now = clock.now(db_path)
    results = []
    for case_id in q.due(now, db_path):
        obslog.log_event(case_id, "worker.woke", at=now.isoformat())
        results.append(evaluate(case_id, "timer", db_path))
    return results


__all__ = ["record", "evaluate", "record_payment", "record_inbound", "decide_approval",
           "resolve_dispute", "resume_case", "tick"]
