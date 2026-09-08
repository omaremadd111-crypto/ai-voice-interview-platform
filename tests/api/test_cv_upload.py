"""CV file upload: real PDF/DOCX/TXT/MD parsing through the existing DocumentParser.

Covers the full requested flow -- upload a real file, have the backend extract
its text, and confirm Prepare Interview then uses that extracted CV together
with the position's job description.
"""
from io import BytesIO

import pytest
from docx import Document as DocxDocument
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")

CV_BODY = (
    "DEMO DATA - Priya Raman. Built a production RAG application using Python, SQL "
    "and a vector database. Designed a retrieval API and deployed it with Docker, "
    "reducing p95 latency by 35 percent. Two years of software engineering experience."
)


def _make_docx_bytes(text: str) -> bytes:
    document = DocxDocument()
    for paragraph in text.split("\n"):
        document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _create_position(client: TestClient, headers: dict) -> dict:
    resp = client.post(
        "/api/v1/positions",
        json={
            "company_name": "Northwind Labs",
            "title": "Junior AI Engineer",
            "description": "Build RAG systems with Python, SQL and vector databases.",
            "experience_level": "Junior",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_candidate(client: TestClient, headers: dict, position_id: int) -> dict:
    resp = client.post(
        "/api/v1/candidates",
        json={"position_id": position_id, "full_name": "Priya Raman"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture()
def candidate(client: TestClient, auth_headers: dict) -> dict:
    position = _create_position(client, auth_headers)
    return _create_candidate(client, auth_headers, position["id"])


def _upload(client: TestClient, headers: dict, candidate_id: int, filename: str, data: bytes):
    return client.post(
        f"/api/v1/candidates/{candidate_id}/cv",
        files={"file": (filename, data, "application/octet-stream")},
        headers=headers,
    )


def test_upload_txt_extracts_and_stores_text(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "priya_cv.txt", CV_BODY.encode("utf-8"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cv_filename"] == "priya_cv.txt"
    assert "production RAG application" in body["cv_text"]


def test_upload_markdown_is_supported(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "cv.md", f"# CV\n\n{CV_BODY}".encode())
    assert resp.status_code == 200, resp.text
    assert "production RAG application" in resp.json()["cv_text"]


def test_upload_real_pdf_extracts_text(
    client: TestClient, auth_headers: dict, candidate: dict, fixtures_dir,
) -> None:
    pdf_bytes = (fixtures_dir / "sample.pdf").read_bytes()
    resp = _upload(client, auth_headers, candidate["id"], "priya_cv.pdf", pdf_bytes)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cv_filename"] == "priya_cv.pdf"
    assert "PDF text extraction" in body["cv_text"]


def test_upload_real_docx_extracts_text(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "priya_cv.docx", _make_docx_bytes(CV_BODY))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cv_filename"] == "priya_cv.docx"
    assert "production RAG application" in body["cv_text"]


def test_upload_replaces_a_previously_stored_cv(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    _upload(client, auth_headers, candidate["id"], "first.txt", b"DEMO DATA - first version text.")
    resp = _upload(
        client, auth_headers, candidate["id"], "second.txt", b"DEMO DATA - second version text.",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cv_filename"] == "second.txt"
    assert "second version" in body["cv_text"]
    assert "first version" not in body["cv_text"]


def test_unsupported_extension_is_rejected(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "cv.exe", b"MZ\x90\x00binary")
    assert resp.status_code == 400
    assert "Unsupported file type" in resp.json()["detail"]


def test_corrupted_pdf_is_rejected_with_a_clear_message(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "broken.pdf", b"%PDF-1.4 not really a pdf")
    assert resp.status_code == 400
    assert "broken.pdf" in resp.json()["detail"]


def test_empty_file_is_rejected(client: TestClient, auth_headers: dict, candidate: dict) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "empty.txt", b"")
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"].lower()


def test_a_file_with_no_extractable_text_is_rejected(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    resp = _upload(client, auth_headers, candidate["id"], "blank.txt", b"   \n\n   ")
    assert resp.status_code == 400
    assert "no extractable text" in resp.json()["detail"]


def test_oversized_upload_is_rejected_with_413(
    client: TestClient, auth_headers: dict, candidate: dict,
) -> None:
    # Just over the 5 MB default in Settings.max_cv_upload_bytes.
    oversized = b"a" * (5 * 1024 * 1024 + 1)
    resp = _upload(client, auth_headers, candidate["id"], "huge.txt", oversized)
    assert resp.status_code == 413
    assert "larger than" in resp.json()["detail"]


def test_cross_user_upload_is_forbidden(
    client: TestClient, auth_headers: dict, candidate: dict, other_registered_user: dict,
) -> None:
    resp = _upload(
        client, other_registered_user["headers"], candidate["id"], "cv.txt", CV_BODY.encode(),
    )
    assert resp.status_code == 403


def test_upload_to_missing_candidate_is_404(client: TestClient, auth_headers: dict) -> None:
    resp = _upload(client, auth_headers, 999999999, "cv.txt", CV_BODY.encode())
    assert resp.status_code == 404


def test_upload_without_authentication_is_401(client: TestClient, candidate: dict) -> None:
    resp = client.post(
        f"/api/v1/candidates/{candidate['id']}/cv",
        files={"file": ("cv.txt", CV_BODY.encode(), "text/plain")},
    )
    assert resp.status_code == 401


def test_uploaded_cv_is_used_when_preparing_the_interview(
    client: TestClient, auth_headers: dict,
) -> None:
    """The end-to-end requirement: JD (from the position) + CV (from the uploaded
    file) both feed the analysis that produces the interview plan."""
    position = _create_position(client, auth_headers)
    candidate_row = _create_candidate(client, auth_headers, position["id"])
    client.post(
        f"/api/v1/positions/{position['id']}/questions",
        json={"category": "technical", "question": "Explain your RAG design.", "order": 0},
        headers=auth_headers,
    )

    upload = _upload(
        client, auth_headers, candidate_row["id"], "priya_cv.docx", _make_docx_bytes(CV_BODY),
    )
    assert upload.status_code == 200

    prepared = client.post(
        "/api/v1/interviews/prepare",
        json={"position_id": position["id"], "candidate_id": candidate_row["id"]},
        headers=auth_headers,
    )
    assert prepared.status_code == 201, prepared.text
    body = prepared.json()

    # The CV drove candidate analysis...
    analysis = body["candidate_analysis"]
    assert analysis["skills"] or analysis["technologies"]
    assert analysis["important_cv_claims"]
    # ...and the job description drove role analysis...
    assert body["job_analysis"]["required_skills"]
    # ...and with both present, the fit analysis is populated too.
    assert body["fit_analysis"]["strong_alignment_areas"]
