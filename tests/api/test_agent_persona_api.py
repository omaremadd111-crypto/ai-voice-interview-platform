"""Agent configuration persona persisted through the existing agent-config API."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")

PERSONA = {
    "agent_name": "Aimy",
    "company_name": "FlairsTech",
    "ai_role_title": "AI screening assistant",
    "language": "English",
    "tone": "professional and warm",
    "opening_script": (
        "Hi {candidate_first_name}, I'm Aimy, FlairsTech's AI screening assistant. "
        "I'll be conducting your initial interview today."
    ),
    "closing_script": "Thank you for your time. A recruiter will be in touch.",
    "off_topic_redirection": "Acknowledge briefly, then return to the question.",
    "follow_up_style": "One focused follow-up when an answer is thin.",
    "candidate_question_handling": "Answer role questions; defer decisions to a recruiter.",
    "conversational_style": {
        "use_candidate_name": True,
        "brief_acknowledgements": True,
        "allow_question_rephrasing": True,
        "natural_pauses": True,
        "allow_interruptions": True,
    },
}


def test_persona_template_is_a_valid_starting_point(client: TestClient, auth_headers: dict) -> None:
    resp = client.get(
        "/api/v1/agent-configs/persona-template",
        params={"agent_name": "Aimy", "company_name": "FlairsTech"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    template = resp.json()
    assert template["agent_name"] == "Aimy"
    assert "AI" in template["opening_script"]

    # The template must itself be savable without edits.
    created = client.post(
        "/api/v1/agent-configs",
        json={"name": "From template", "config": template},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text


def test_full_persona_round_trips(client: TestClient, auth_headers: dict) -> None:
    resp = client.post(
        "/api/v1/agent-configs",
        json={"name": "Aimy — FlairsTech", "config": PERSONA},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    stored = resp.json()["config"]
    assert stored["agent_name"] == "Aimy"
    assert stored["company_name"] == "FlairsTech"
    assert stored["conversational_style"]["allow_interruptions"] is True
    assert "{candidate_first_name}" in stored["opening_script"]


def test_opening_without_ai_disclosure_is_rejected(client: TestClient, auth_headers: dict) -> None:
    bad = {**PERSONA, "opening_script": "Hi, I'm Aimy from FlairsTech. Let's get started."}
    resp = client.post(
        "/api/v1/agent-configs", json={"name": "No disclosure", "config": bad}, headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "disclose" in resp.json()["detail"].lower()


def test_prohibited_inference_setting_is_rejected(client: TestClient, auth_headers: dict) -> None:
    bad = {**PERSONA, "tone": "warm, and use emotion detection to gauge confidence"}
    resp = client.post(
        "/api/v1/agent-configs", json={"name": "Prohibited", "config": bad}, headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "prohibited" in resp.json()["detail"].lower()


def test_updating_to_an_invalid_persona_is_rejected(client: TestClient, auth_headers: dict) -> None:
    created = client.post(
        "/api/v1/agent-configs", json={"name": "Aimy", "config": PERSONA}, headers=auth_headers,
    ).json()

    resp = client.patch(
        f"/api/v1/agent-configs/{created['id']}",
        json={"config": {**PERSONA, "opening_script": "Hi, I'm Aimy. Let's begin."}},
        headers=auth_headers,
    )
    assert resp.status_code == 400

    # The stored persona is untouched by the rejected update.
    current = client.get(f"/api/v1/agent-configs/{created['id']}", headers=auth_headers).json()
    assert "AI screening assistant" in current["config"]["opening_script"]


def test_non_persona_config_blobs_still_work(client: TestClient, auth_headers: dict) -> None:
    """Validation only applies to configs that declare themselves as personas, so
    unrelated settings blobs are unaffected."""
    resp = client.post(
        "/api/v1/agent-configs",
        json={"name": "Plain settings", "config": {"note": "anything goes here"}},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    assert resp.json()["config"] == {"note": "anything goes here"}
