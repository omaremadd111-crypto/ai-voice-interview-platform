"""End-to-end proof that candidate data cannot cross session boundaries."""
from pathlib import Path

from application.dto import PrepareInterviewRequest
from application.interview_agent_service import InterviewAgentService
from config.settings import Settings
from services.session_service import InMemorySessionStore

JD_TEXT = "Acme needs an engineer with Python, SQL, testing, API development, and communication skills."
CV_ALPHA = "Built a Python API for the Alpha-only inventory project and improved latency by 30 percent."
CV_BETA = "Built a SQL testing workflow for the Beta-only billing project and reduced failures by 25 percent."
ANSWER_ALPHA = "I built the Alpha-only Python API because latency mattered and improved it by 30 percent."
ANSWER_BETA = "I built the Beta-only SQL testing workflow because reliability mattered and reduced failures by 25 percent."


def _request(candidate_name: str, cv_text: str) -> PrepareInterviewRequest:
    return PrepareInterviewRequest(
        company_name="Acme",
        job_title="Software Engineer",
        job_description_text=JD_TEXT,
        candidate_name=candidate_name,
        candidate_cv_text=cv_text,
        num_questions=4,
    )


def test_two_complete_sessions_never_share_candidate_or_transcript_state(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    settings = Settings(mock_mode=True, reports_dir=tmp_path / "reports")
    service = InterviewAgentService(settings=settings, session_store=store)

    alpha = service.prepare_interview(_request("Candidate Alpha", CV_ALPHA))
    beta = service.prepare_interview(_request("Candidate Beta", CV_BETA))
    assert alpha.session_id != beta.session_id

    service.approve_plan(alpha.session_id)
    service.approve_plan(beta.session_id)
    service.start_interview(alpha.session_id)
    service.start_interview(beta.session_id)
    service.submit_answer(alpha.session_id, ANSWER_ALPHA)
    service.submit_answer(beta.session_id, ANSWER_BETA)

    stored_alpha = store.get(alpha.session_id)
    stored_beta = store.get(beta.session_id)
    assert stored_alpha.candidate_input.full_name == "Candidate Alpha"
    assert stored_beta.candidate_input.full_name == "Candidate Beta"
    assert "Alpha-only" in stored_alpha.candidate_input.cv_text
    assert "Beta-only" not in stored_alpha.candidate_input.cv_text
    assert "Beta-only" in stored_beta.candidate_input.cv_text
    assert "Alpha-only" not in stored_beta.candidate_input.cv_text
    assert [turn.answer for turn in stored_alpha.transcript.turns] == [ANSWER_ALPHA]
    assert [turn.answer for turn in stored_beta.transcript.turns] == [ANSWER_BETA]

    stored_alpha.candidate_input.cv_text = "Mutated detached copy"
    stored_alpha.transcript.turns[0].answer = "Mutated detached answer"
    assert "Alpha-only" in store.get(alpha.session_id).candidate_input.cv_text
    assert store.get(alpha.session_id).transcript.turns[0].answer == ANSWER_ALPHA
    assert store.get(beta.session_id).transcript.turns[0].answer == ANSWER_BETA

    service.end_interview(alpha.session_id)
    service.end_interview(beta.session_id)
    service.evaluate_interview(alpha.session_id)
    service.evaluate_interview(beta.session_id)
    alpha_report = service.generate_report(alpha.session_id)
    beta_report = service.generate_report(beta.session_id)

    assert alpha_report.candidate_name == "Candidate Alpha"
    assert beta_report.candidate_name == "Candidate Beta"
    assert all("Beta-only" not in turn.answer for turn in alpha_report.full_transcript)
    assert all("Alpha-only" not in turn.answer for turn in beta_report.full_transcript)
    assert (settings.reports_dir / f"{alpha.session_id}.md").exists()
    assert (settings.reports_dir / f"{beta.session_id}.md").exists()
