- 추후 구체적인 사유 추가에 대한 요청 md
    
    # 여행 생성 실패 사유(reason) 안내 — 구현 계획
    
    > 상태: **계획만 작성됨 (미구현)**
    > 
    
    ## 목적
    
    여행 생성이 실패하면 사용자에게 **왜 실패했는지와 무엇을 바꿔 보면 되는지**를 한 문장으로 알려준다.
    지금 내려가는 `error_message`는 그대로 두고, 응답에 `reason` 필드를 추가해 안내 문구를 넣는다.
    
    ## 응답 형식
    
    ```json
    {
      "message": "ai_itinerary_generation_failed",
      "data": {
        "travel_plan_id": 10,
        "error_message": "필수 장소가 너무 많습니다.",
        "reason": "선택한 일정 강도로는 필수 방문지를 모두 넣을 수 없습니다. 일정 강도를 조정해주세요."
      }
    }
    ```
    
    - `error_message`: 지금 값을 바꾸지 않는다.
    - `reason`: 아래 표의 고정 문구 중 하나. 해당하는 문구가 없으면 `null`이다. `region_not_found`, `ambiguous_region`, `required_hotel_unfit`, `required_place_details_missing`, 503 등이 여기에 해당한다.
    - SSE `error` 이벤트의 `data`에도 똑같이 `reason`을 넣는다 (`app/services/streaming/backend_adapter.py`).
    - `walking_limit` 같은 내부 reason 코드는 그대로 두고 로그에만 쓴다. 응답의 `reason`에는 문구만 들어간다.
    
    ## 고정 문구와 발생 조건
    
    | # | `reason` 문구 | 내부 코드 | 발생 조건 |
    | --- | --- | --- | --- |
    | 1 | 선택한 지역과 테마로 추천할 장소를 찾지 못했습니다. 테마를 줄이거나 다른 테마를 선택해 보세요. | `no_place_candidates` | 기존 지점 그대로 (`app/clients/kakao_places.py` 217행) |
    | 2 | 일정 주변에서 식사할 식당을 찾지 못했습니다. 선호 음식을 다르게 선택하거나 이동 범위를 넓혀 보세요. | `no_restaurant_candidates` | 기존 지점 그대로 (`app/services/planner.py` 165행) |
    | 3 | 도보 여행에서 적절한 일정을 제작할 수 없습니다. 대중교통이나 자동차 이동을 선택해 보세요. | `walking_limit` / `accommodation_unavailable` / `place_selection_failed` | ① 하루 도보 거리가 상한을 넘을 때 (`app/services/routes.py` 79행)<br>② 도보로 이동하는 날 숙소를 찾지 못할 때 (`app/services/accommodation.py` 109행)<br>③ 도보로 이동하는 날 장소 선택이 마지막 재시도에서 도보 상한 때문에 실패할 때 |
    | 4 | 일정 동선 근처에서 적절한 숙소를 찾지 못했습니다. 필수 방문지를 줄이거나 숙소를 직접 지정해주세요. | `accommodation_unavailable` | 도보가 아닌 날 숙소를 찾지 못할 때 |
    | 5 | 입력한 요청사항으로는 일정을 만들 수 없습니다. 추가 요청사항을 수정해 주세요. | `model_refusal` | 모델이 응답을 거부할 때 (`app/clients/openai_client.py` 45행) |
    | 6 | 여행 기간에 비해 방문할 장소가 너무 많아 일정을 모두 소화할 수 없습니다. 필수 방문지를 줄이거나 여행 기간을 늘려 보세요. | `too_many_required_places` / `place_selection_failed` | ① 일정 강도를 PACKED로 올려도 필수 방문지가 들어가지 않을 때<br>② 장소 선택이 마지막 재시도에서 시간 부족(`TimeShortage`)으로 실패할 때 |
    | 7 | 선택한 일정 강도로는 필수 방문지를 모두 넣을 수 없습니다. 일정 강도를 조정해주세요. | `too_many_required_places` | 지금 강도로는 들어가지 않지만 PACKED로 올리면 들어갈 때 |
    | 8 | 추가 요청사항('{요약}')에 따른 일정 소화가 불가능합니다. 요청사항을 완화해 주세요. | `too_many_required_places` / `insufficient_trip_time` | 6·7·9번에 해당하지만 추가 요청사항을 빼고 다시 계산하면 통과할 때 |
    | 9 | 여행 시간이 너무 짧아 일정을 만들 수 없습니다. 도착·출발 시각을 확인해 주세요. | `insufficient_trip_time` | 기존 지점 그대로 (`app/services/scheduler.py` 202행) |
    
    ### 판별 규칙
    
    **6·7·8·9번 (`validate_generation_window`)**
    
    - LLM을 다시 부르지 않고 `build_day_plans`로 일정을 다시 계산해 판별한다.
    - 판별 순서
        1. 추가 요청사항을 빼고(`CustomRequest.empty()`) 다시 계산해서 통과하면 → **8번**
        2. 그렇지 않고 `pace_type`을 `PACKED`로 바꿔 다시 계산해서 통과하면 → **7번**
        3. 둘 다 아니면 → 수용량이 0이면 **9번**, 아니면 **6번**
    - 이 판별에는 request가 필요하다. `validate_generation_window(plans, required)`에 `request`와 `custom`을 인자로 추가해야 한다 (`app/services/pipeline.py`에서 호출).
    
    **8번 `{요약}`**
    
    - `preference.extra_request` 원문을 20자에서 자르고, 잘렸으면 끝에 `…`를 붙인다.
    
    **3번·6번 ② (장소 선택 최종 실패)**
    
    - `recommend_places`가 마지막 재시도의 `InvalidModelOutput`을 보관한다.
    - 판별
        - `TimeShortage`이면 → 6번
        - 도보 상한 위반(`check_walking`)이고 그날 이동수단이 `WALK`이면 → 3번
        - 그 밖의 경우 → `reason`은 `null`
    - 도보 상한 위반을 문자열 대신 타입으로 구분할 수 있게 `WalkingLimitExceeded(InvalidModelOutput)` 예외를 추가하는 방안을 권장한다.
    
    **3번 ②와 4번 (숙소 실패)**
    
    - `assign_accommodations`에서 `mode == "WALK"`이면 3번, 아니면 4번이다.
        - `mode`는 그날과 다음 날 이동수단 중 더 느린 쪽이다.
    
    ## 변경 파일
    
    | 파일 | 변경 내용 |
    | --- | --- |
    | `app/core/exceptions.py` | `ApiError`/`GenerationFailed`에 `guide: str | None` 속성 추가. 고정 문구 상수 9개 정의. 422 응답 `data`에 `"reason": exc.guide` 추가 |
    | `app/services/streaming/backend_adapter.py` | SSE error 이벤트 `data`에 `reason` 추가 |
    | `app/services/scheduler.py` | `validate_generation_window` 판별 로직 추가 (6·7·8·9번). 선택 사항: `WalkingLimitExceeded` 예외 추가 |
    | `app/services/pipeline.py` | `validate_generation_window` 호출 인자 변경 |
    | `app/services/planner.py` | 마지막 실패 원인에 따라 `guide` 지정 (3번·6번) |
    | `app/services/accommodation.py` | 도보 여부에 따라 3번/4번 `guide` 지정 |
    | `app/services/routes.py` | `walking_limit` 실패에 3번 `guide` 지정 |
    | `app/clients/kakao_places.py`, `app/clients/openai_client.py`, `app/services/planner.py` (165행) | 1·2·5번 `guide` 지정 |
    | `app/services/mock_generator.py` | 목업 실패 시나리오가 실제 문구를 내도록 변경 (`fail_place`→1번, `fail_stay`→4번, `fail_route`→3번) |
    | `app/docs/openapi_examples.py` | 422·SSE 예시에 `reason` 추가 |
    | `docs/design/itinerary-generation.md` | 실패 응답 형식과 문구 표 반영 |
    
    ## 테스트
    
    - `tests/services/test_scheduler.py`
        - 6·7·8·9번이 판별 순서대로 골라지는지
        - 8번 `{요약}`이 20자에서 잘리는지
    - `tests/services/test_accommodation.py`: 도보 날은 3번, 도보가 아닌 날은 4번
    - `tests/services/test_planner.py`: 마지막 실패가 `TimeShortage`이면 6번, 도보 상한 위반이면 3번, 그 밖이면 `null`
    - `tests/api/test_itinerary.py`
        - 422 응답에 `error_message`가 그대로 있고 `reason`이 추가되는지
        - 해당 문구가 없는 실패에서는 `reason`이 `null`인지
    - `tests/api/test_mock_stream.py`: SSE error 이벤트 `data.reason`
    
    ## 미결 사항
    
    - 5번(`model_refusal`)은 추가 요청사항이 없을 때도 같은 문구가 나간다. 추가 요청사항이 있을 때만 5번을 쓰고 없으면 `null`로 둘지 결정해야 한다.
    - 결정 사항 : 추가 요청사항이 없어도 해당 문구로 예외 처리 진행