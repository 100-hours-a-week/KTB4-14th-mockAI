"""복사해서 쓸 수 있는 Swagger 예시. 값은 설명용이며 실제 추천 결과가 아닙니다."""
from app.services.streaming.sse import encode_event


REQUEST_EXAMPLE = {
    "travel_plan_id": 10,
    "region_id": 1,
    "region_name": "제주특별자치도 서귀포시",
    "arrival_datetime": "2026-08-27T13:00:00",
    "departure_datetime": "2026-08-29T18:00:00",
    "headcount": 2,
    "companion_type": "COUPLE",
    "preference": {
        "pace_type": "RELAXED", "transport_type": "PUBLIC_TRANSPORT",
        "budget_min": 300000, "budget_max": 800000, "budget_type": "KRW",
        "distance_preference": 70, "themes": ["NATURE", "FOOD"],
        "foods": ["KOREAN", "SEAFOOD"], "extra_request": "둘째 날은 렌터카로 다닐게요.",
    },
    "required_places": [{
        "provider": "KAKAO", "provider_place_id": "26338954", "place_name": "성산일출봉",
        "address": "제주특별자치도 서귀포시 성산읍 성산리 1",
        "latitude": 33.458056, "longitude": 126.9425, "place_type": "TOURISM", "order": 1,
    }],
}
REQUEST_EXAMPLES = {"backend": {"summary": "백엔드 AiTravelGenerationRequest", "value": REQUEST_EXAMPLE}}

ERROR_EXAMPLES = {
    400: {"description": "요청 형식 오류", "value": {"message": "invalid_request", "data": {"error_message": "region_id: 필수 값입니다."}}},
    401: {"description": "Bearer 토큰 없음 또는 불일치", "value": {"message": "unauthorized", "data": None}},
    422: {"description": "조건에 맞는 일정을 만들지 못함",
          "value": {"message": "ai_itinerary_generation_failed", "data": {"travel_plan_id": 10, "error_message": "숙소를 찾지 못했습니다."}}},
    500: {"description": "서버 내부 오류", "value": {"message": "internal_server_error", "data": None}},
    503: {"description": "외부 API 장애, 키 없음, 시간 초과",
          "value": {"message": "ai_service_unavailable", "data": {"error_message": "잠시 후 다시 시도해주세요."}}},
}
ERROR_RESPONSES = {
    code: {"description": example["description"], "content": {"application/json": {"example": example["value"]}}}
    for code, example in ERROR_EXAMPLES.items()
}

STREAM_RESPONSE = {
    "description": (
        "PLACE_RECOMMEND → STAY_RECOMMEND → ROUTE_OPTIMIZE 단계별 STARTED/DONE 이벤트를 보냅니다. "
        "최종 일정은 ROUTE_OPTIMIZE_DONE의 result에 한 번만 담기고, 이어서 complete를 보냅니다. "
        "HTTP 200 이후에도 error 이벤트로 실패할 수 있습니다."
    ),
    "content": {"text/event-stream": {"schema": {"type": "string"}, "examples": {
        "started": {"value": encode_event("PLACE_RECOMMEND_STARTED", 1, {"stage": "PLACE_RECOMMEND", "status": "RUNNING"})},
        "done": {"value": encode_event("PLACE_RECOMMEND_DONE", 2, {"stage": "PLACE_RECOMMEND", "status": "DONE"})},
        "complete": {"value": encode_event("complete", 7, {"travel_plan_id": 10, "stage": "COMPLETE", "status": "COMPLETED"})},
        "error": {"value": encode_event("error", 4, {
            "travel_plan_id": 10, "stage": "STAY_RECOMMEND", "status": "FAILED",
            "message": "ai_itinerary_generation_failed", "data": {"error_message": "숙소를 찾지 못했습니다."},
        })},
    }}},
}
