from app.services.places import select_candidates
from tests.conftest import place


def test_breakfast_friendly_restaurants_are_kept_first_for_each_day():
    pool = [place(f"r{i}", "식당") for i in range(20)] + [place("h1", "식당").model_copy(update={"place_name": "원조 해장국"}),
                                                        place("h2", "식당").model_copy(update={"place_name": "본죽"})]

    chosen = [p.provider_place_id for p in select_candidates(pool, [], days=2, distance_preference=50) if p.category == "식당"]

    assert chosen[:2] == ["h1", "h2"]
    assert len(chosen) == 10
