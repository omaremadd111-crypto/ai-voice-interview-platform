"""Public LiveKit token boundary: signed invitations are mandatory."""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("pg_engine")


def test_candidate_token_endpoint_is_public_but_rejects_tampering(client: TestClient) -> None:
    response = client.post(
        "/api/v1/voice/token",
        json={"invitation": "not-a-valid-signed-invitation"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "The voice invitation is invalid"}


def test_recruiter_invitation_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.post("/api/v1/queues/1/items/1/voice-invite")

    assert response.status_code == 401
