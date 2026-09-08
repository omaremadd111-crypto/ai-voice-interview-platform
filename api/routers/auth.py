"""Auth endpoints: thin transport only. Credential checking and token issuance
use cases live in application/auth_service.py and api/security.py."""
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm

from api.dependencies import get_api_settings, get_auth_service
from api.schemas.auth import RegisterRequest, TokenResponse, UserResponse
from api.security import create_access_token
from api.settings import APISettings
from application.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=201)
def register(payload: RegisterRequest, auth_service: AuthService = Depends(get_auth_service)) -> UserResponse:
    user = auth_service.register(email=payload.email, password=payload.password, full_name=payload.full_name)
    return UserResponse(id=user.id, email=user.email, full_name=user.full_name, role=user.role, is_active=user.is_active)


@router.post("/login", response_model=TokenResponse)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_service: AuthService = Depends(get_auth_service),
    api_settings: APISettings = Depends(get_api_settings),
) -> TokenResponse:
    # OAuth2PasswordRequestForm's "username" field carries the email -- this is
    # the standard field name FastAPI's /docs "Authorize" button expects.
    user = auth_service.authenticate(email=form_data.username, password=form_data.password)
    token = create_access_token(user.id, api_settings)
    return TokenResponse(access_token=token)
