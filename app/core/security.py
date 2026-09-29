from __future__ import annotations

import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False)


def require_api_token(expected_token: str | None):
    async def dependency(
        credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    ) -> None:
        if not expected_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AUDIGO_API_TOKEN is not configured",
            )
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not hmac.compare_digest(
                credentials.credentials.encode(), expected_token.encode()
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing Bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return dependency


basic = HTTPBasic(auto_error=False, realm="Audigo API Docs")


def require_docs_credentials(username: str, password: str):
    """Swagger·ReDoc·openapi.json 보호용 HTTP Basic 인증. 브라우저가 로그인 창을 띄웁니다."""
    async def dependency(credentials: HTTPBasicCredentials | None = Security(basic)) -> None:
        # 아이디가 틀려도 비밀번호 비교까지 수행해 응답 시간으로 정보가 새지 않게 합니다.
        valid = credentials is not None
        user_ok = hmac.compare_digest((credentials.username if valid else "").encode(), username.encode())
        password_ok = hmac.compare_digest((credentials.password if valid else "").encode(), password.encode())
        if not (valid and user_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing docs credentials",
                headers={"WWW-Authenticate": 'Basic realm="Audigo API Docs"'},
            )

    return dependency
