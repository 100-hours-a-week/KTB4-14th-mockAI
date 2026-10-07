from app.services.places import select_candidates
from tests.conftest import place


def test_breakfast_friendly_restaurants_are_kept_first_for_each_day():
    pool = [place(f"r{i}", "식당") for i in range(20)] + [place("h1", "식당").model_copy(update={"place_name": "원조 해장국"}),
                                                        place("h2", "식당").model_copy(update={"place_name": "본죽"})]

    chosen = [p.provider_place_id for p in select_candidates(pool, [], days=2, distance_preference=50) if p.category == "식당"]

    assert chosen[:2] == ["h1", "h2"]
    assert len(chosen) == 10


import asyncio

import pytest

from app.core.exceptions import GenerationFailed
from app.services.places import requested_places


class FakeFinder:
    def __init__(self, found):
        self.found, self.calls = found, []

    async def find_places(self, region_name, names):
        self.calls.append((region_name, names))
        return [self.found.get(name) for name in names]


def test_requested_places_become_required_without_order_and_skip_duplicates():
    finder = FakeFinder({"해운대": place("k1", "관광"), "광안리": place("k2", "관광")})
    required = [place("k2", "관광", is_required=True, required_order=1)]

    found = asyncio.run(requested_places(finder, "부산광역시", [" 해운대 ", "광안리", "해운대"], required))

    assert finder.calls == [("부산광역시", ["해운대", "광안리"])]
    assert [(p.provider_place_id, p.is_required, p.required_order) for p in found] == [("k1", True, None)]


@pytest.mark.parametrize("name, message", [
    ("감천문화마을", "요청사항의 장소 '감천문화마을'을 찾지 못했습니다. 필수 장소에서 선택해주세요."),
    ("해운대", "요청사항의 장소 '해운대'를 찾지 못했습니다. 필수 장소에서 선택해주세요."),
])
def test_requested_place_that_cannot_be_found_fails_generation(name, message):
    with pytest.raises(GenerationFailed) as error:
        asyncio.run(requested_places(FakeFinder({}), "부산광역시", [name], []))

    assert error.value.message == message
    assert error.value.reason == "requested_place_not_found"
