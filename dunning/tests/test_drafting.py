"""The model drafts; code checks the amount."""
from __future__ import annotations

from datetime import date, datetime, timezone

import drafting
import errors
from models import Case, Stage

UTC = timezone.utc


def case(balance=240.0) -> Case:
    return Case(case_id="C-1", tenant="t", customer_name="Acme Corp", customer_email="a@x",
                customer_tz="Europe/London", invoice_id="INV-1", invoice_due=date(2026, 11, 6),
                amount_usd=240.0, balance_usd=balance)


STAGE = Stage(name="first_notice", day=3, channel="email", tone="formal")


def test_check_amounts():
    assert drafting.check_amounts("Your balance of $240.00 is overdue.", 240.0)
    assert drafting.check_amounts("Please pay $1,250.00 today.", 1250.0)
    assert drafting.check_amounts("$120 remains", 120.0)
    assert not drafting.check_amounts("Your balance of $240.00 of the $300 invoice", 240.0)
    assert not drafting.check_amounts("Please pay what you owe.", 240.0)  # no amount at all
    assert not drafting.check_amounts("Please pay $24.00.", 240.0)


def test_when_phrases():
    c = case()
    assert drafting.facts(c, STAGE, datetime(2026, 11, 4, 9, tzinfo=UTC))["when"] == "due in 2 days"
    assert drafting.facts(c, STAGE, datetime(2026, 11, 5, 9, tzinfo=UTC))["when"] == "due in 1 day"
    assert drafting.facts(c, STAGE, datetime(2026, 11, 6, 9, tzinfo=UTC))["when"] == "due today"
    assert drafting.facts(c, STAGE, datetime(2026, 11, 7, 9, tzinfo=UTC))["when"] == "1 day overdue"
    assert drafting.facts(c, STAGE, datetime(2026, 11, 16, 9, tzinfo=UTC))["when"] == "10 days overdue"


def test_a_good_draft_passes(fake_model):
    text, by, usage, problem = drafting.draft(case(), STAGE, datetime(2026, 11, 9, 9, tzinfo=UTC))
    assert by == "model" and problem is None and "$240.00" in text.body and usage is not None


def test_a_wrong_amount_is_replaced_by_the_template(fake_model):
    fake_model.wrong_amount = True
    text, by, usage, problem = drafting.draft(case(120.0), STAGE, datetime(2026, 11, 9, 9, tzinfo=UTC))
    assert by == "template" and problem == errors.DRAFT_REJECTED
    assert "$120.00" in text.body and "Acme Corp" in text.body and "3 days overdue" in text.body


def test_a_model_failure_falls_back_to_the_template(fake_model):
    fake_model.fail_with = errors.LLM_TIMEOUT
    text, by, usage, problem = drafting.draft(case(), STAGE, datetime(2026, 11, 9, 9, tzinfo=UTC))
    assert by == "template" and problem == errors.LLM_TIMEOUT and usage is None
    assert text.subject == "Invoice INV-1 is overdue"


def test_templates_cover_every_tone_and_state_the_balance():
    for tone in ("friendly", "formal", "firm", "final"):
        stage = Stage(name="x", day=1, channel="email", tone=tone)
        draft = drafting.template_draft(case(95.5), stage, datetime(2026, 11, 9, 9, tzinfo=UTC))
        assert drafting.check_amounts(draft.body, 95.5), tone
        assert ("legal" in draft.body) == (tone == "final")
