import asyncio

from app.schemas.planner import CustomRequest
from app.services.mock_generator import MockPlanner, mock_providers
from app.services.pipeline import generation_stages, run_to_completion
from tests.conftest import request_model


class PlacesRequestPlanner(MockPlanner):
    async def parse_custom_request(self, context):
        return CustomRequest.empty().model_copy(update={"places": ["감천문화마을", "흰여울마을"]})


def test_places_written_in_extra_request_end_up_in_the_itinerary():
    request = request_model(preference={"extra_request": "감천문화마을이랑 흰여울마을은 꼭 가고 싶어요"})
    providers = mock_providers(request)
    providers.planner = PlacesRequestPlanner()

    itinerary = asyncio.run(run_to_completion(generation_stages(request, providers)))

    names = [item.place_name for day in itinerary.days for item in day.items]
    assert {"감천문화마을", "흰여울마을"} <= set(names)
