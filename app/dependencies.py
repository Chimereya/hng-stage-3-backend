from fastapi import Depends, HTTPException, Header, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User
from .auth import verify_token


# ----------------------------------------------------------------
# AUTH SCHEME
# ----------------------------------------------------------------

bearer_scheme = HTTPBearer(auto_error=False)
# auto_error=False allows fallback to cookies (for web)


# ----------------------------------------------------------------
# CURRENT USER
# ----------------------------------------------------------------

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Supports BOTH:
    - CLI → Authorization: Bearer <token>
    - Web → HTTP-only cookies
    """

    token = None

    # Try Authorization header (CLI)
    if credentials:
        token = credentials.credentials

    # Fallback to cookies (Web)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Authentication required"}
        )

    # Verify token
    payload = verify_token(token, token_type="access")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "Invalid token payload"}
        )

    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "message": "User not found"}
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Account is deactivated"}
        )

    return user


# ----------------------------------------------------------------
# ROLE-BASED ACCESS CONTROL
# ----------------------------------------------------------------

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Admin access required"}
        )
    return current_user


def require_analyst(current_user: User = Depends(get_current_user)) -> User:
    """
    Allows both admin and analyst users.
    """
    if current_user.role not in ("admin", "analyst"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"status": "error", "message": "Access denied"}
        )
    return current_user



def require_api_version(x_api_version: str = Header(None)) -> None:
    """
    Enforces: X-API-Version: 1
    """
    if x_api_version != "1":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"status": "error", "message": "API version header required"}
        )