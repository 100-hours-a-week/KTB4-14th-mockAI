"""백엔드(feature-travel)의 SSE 파서와 저장 로직에 맞춘 전송 형식."""
from __future__ import annotations

from app.schemas.itinerary import ItineraryResponse
from app.services.streaming.sse import KEEP_ALIVE, encode_event, stage_events


STAGE_NAMES = {"PLACES": "PLACE_RECOMMEND", "ACCOMMODATIONS": "STAY_RECOMMEND", "ROUTES": "ROUTE_OPTIMIZE"}
PLACE_TYPES = {"TOUR": "TOURISM", "RESTAURANT": "RESTAURANT", "ACCOMMODATION": "ACCOMMODATION"}
STOP_KEYS = ("provider", "provider_place_id", "place_name", "address", "latitude", "longitude",
             "sequence", "start_time", "end_time", "meal_type", "price_info")


def backend_result(itinerary: ItineraryResponse) -> dict:
    days = []
    for day in itinerary.model_dump(mode="json")["days"]:
        items, routes, previous = [], [], None
        for item in day["items"]:
            stop = {key: item[key] for key in STOP_KEYS}
            stop["place_type"] = PLACE_TYPES[item["item_type"]]
            route = item.get("route_from_previous")
            if route is not None and previous is not None:
                routes.append({"from_sequence": previous["sequence"], "to_sequence": item["sequence"],
                               **route, "order": len(routes) + 1})
            elif route is not None:
                # 백엔드 PendingRoute는 날을 넘는 구간(전날 숙소 → 첫 장소)을 표현하지 못해 항목에 따로 담습니다.
                stop["route_from_previous"] = route
            items.append(stop)
            previous = item
        days.append({"day_number": day["day_number"], "travel_date": day["travel_date"], "items": items, "routes": routes})
    return {"title": itinerary.title, "days": days}


async def backend_stream(generator, settings, request_id: str, travel_plan_id: int):
    """최종 일정은 ROUTE_OPTIMIZE_DONE에 한 번만 담습니다. 백엔드는 이 이벤트를 받으면 바로 저장합니다."""
    identity = {"travel_plan_id": travel_plan_id}
    sequence = 0
    async for stage, status, result in stage_events(generator, settings, request_id):
        if stage == "tick":
            yield KEEP_ALIVE
            continue
        sequence += 1
        if stage == "error":
            failed_stage, error = status, result
            yield encode_event("error", sequence, {
                **identity, "stage": STAGE_NAMES.get(failed_stage, failed_stage), "status": "FAILED",
                "message": error.code, "data": {"error_message": error.message},
            })
            return
        if stage == "ROUTES" and status == "COMPLETED":
            sequence -= 1
            continue
        if stage == "COMPLETE":
            yield encode_event("ROUTE_OPTIMIZE_DONE", sequence, {
                **identity, "stage": "ROUTE_OPTIMIZE", "status": "DONE", "result": backend_result(result),
            })
            sequence += 1
            yield encode_event("complete", sequence, {**identity, "stage": "COMPLETE", "status": "COMPLETED"})
            continue
        name = STAGE_NAMES[stage]
        yield encode_event(f"{name}_{'STARTED' if status == 'STARTED' else 'DONE'}", sequence, {
            "stage": name, "status": "RUNNING" if status == "STARTED" else "DONE",
        })
