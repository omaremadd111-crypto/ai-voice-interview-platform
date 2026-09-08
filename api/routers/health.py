"""Unversioned health endpoint, outside the /api/v1 prefix (standard practice:
infrastructure health probes shouldn't depend on API versioning)."""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
