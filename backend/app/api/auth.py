from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin import authenticate_admin, get_admin_email
from app.core.config import settings
from app.core.security import create_session_token, verify_session_token
from app.db.session import get_db
from app.schemas.auth import AdminProfile, LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


async def require_admin(request: Request, db: AsyncSession = Depends(get_db)) -> str:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    email = verify_session_token(token)
    admin_email = await get_admin_email(db)
    if email is None or admin_email is None or email != admin_email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    return email


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    email = await authenticate_admin(db, payload.email, payload.password)
    if email is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(email),
        httponly=True,
        secure=is_secure_request(request),
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )
    return LoginResponse(email=email)


@router.get("/me", response_model=AdminProfile)
async def me(email: str = Depends(require_admin)) -> AdminProfile:
    return AdminProfile(email=email)


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    response.delete_cookie(key=settings.session_cookie_name, path="/", secure=is_secure_request(request), samesite="lax")
    return {"status": "ok"}


def is_secure_request(request: Request) -> bool:
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return forwarded_proto == "https" or request.url.scheme == "https"
