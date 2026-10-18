"""BE-aligned matching examples using only backend-supplied profile metadata."""
from app.schemas.common import ErrorResponse


REQUEST_EXAMPLE = {
    "request": {
        "matching_request_id": 10,
        "user_id": 1,
        "preferred_companion_gender": "FEMALE",
        "theme": ["NATURE", "FOOD"],
        "pace": "RELAXED",
        "budget_min": 100000,
        "budget_max": 800000,
    },
    "candidates": [
        {"user_id": 23, "nickname": "나나", "profile_image_url": None, "gender": "FEMALE",
         "theme": ["NATURE", "FOOD", "REST"], "pace": "RELAXED"},
        {"user_id": 24, "nickname": "다다", "profile_image_url": None, "gender": "FEMALE",
         "theme": ["NATURE", "CULTURE"], "pace": "BALANCED"},
        {"user_id": 25, "nickname": "라라", "profile_image_url": None, "gender": "MALE",
         "theme": ["NATURE", "FOOD"], "pace": "RELAXED"},
    ],
}
RESPONSE_EXAMPLE = {
    "candidates": [
        {"user_id": 23, "match_rate": 100},
        {"user_id": 24, "match_rate": 50},
    ],
    "count": 2,
}
ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "서비스 Bearer 토큰 없음 또는 불일치"},
    422: {
        "model": ErrorResponse,
        "description": "요청·성별·예산·프로필 검증 실패 또는 설정된 입력 상한 초과",
        "content": {"application/json": {"example": {
            "message": "invalid_request", "data": {"error_message": "limit: 3 이하여야 합니다."},
        }}},
    },
    500: {"model": ErrorResponse, "description": "예상하지 못한 서버 내부 오류"},
    503: {"model": ErrorResponse, "description": "서버의 서비스 인증 토큰 미설정"},
}
