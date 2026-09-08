"""render_template: pure template rendering, no database. The claim/send/
retry cycle (EmailDispatchService.process_next) is exercised against a real
outbox in tests/db/test_email_dispatch_service.py -- rendering has no
database dependency of its own, so it is tested here in isolation.
"""
import pytest

from application.email_dispatch_service import TemplateRenderError, render_template


def test_render_template_fills_every_placeholder() -> None:
    rendered = render_template("interview_invitation", {
        "candidate_first_name": "Jordan",
        "company_name": "Acme",
        "position_title": "Junior AI Engineer",
        "interview_url": "https://interviews.example/interview/tok123",
        "expires_at_display": "September 9, 2026",
    })

    assert rendered.subject == "Your interview for Junior AI Engineer at Acme"
    assert "Hi Jordan," in rendered.text_body
    assert "https://interviews.example/interview/tok123" in rendered.text_body
    assert "September 9, 2026" in rendered.text_body
    assert rendered.html_body is not None
    assert "https://interviews.example/interview/tok123" in rendered.html_body


def test_render_template_raises_for_an_unknown_template() -> None:
    with pytest.raises(TemplateRenderError):
        render_template("does_not_exist", {})


def test_render_template_raises_for_a_missing_payload_key_without_leaking_other_values() -> None:
    with pytest.raises(TemplateRenderError) as excinfo:
        render_template("interview_invitation", {
            "candidate_first_name": "Jordan",
            # company_name, position_title, interview_url, expires_at_display missing.
        })
    # Names which placeholder was missing -- never any of the payload's own
    # values, which is all the message could otherwise leak.
    message = str(excinfo.value)
    assert any(key in message for key in ("company_name", "position_title"))
    assert "Jordan" not in message
