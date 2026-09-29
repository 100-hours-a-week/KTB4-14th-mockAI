# 여행 일정 생성 설계

실제 AI 서버(OpenAI + 카카오 호출)와 목업 데이터 전용 경로를 함께 제공하는 일정 생성 서버의 설계 문서입니다.
기존 `KTB4-14th-AI`의 일정 생성·스트리밍 로직을 기반으로 하되, 숙소 단계에서 시간이 부족해 생성이 실패하던 문제를 고치기 위해 시간 계산과 숙소 배치 로직을 새로 설계했습니다.

## 1. 범위

| 구분 | Method | 경로 | 응답 |
|---|---|---|---|
| 공통 | GET | `/health` | 서버 상태 |
| 실제 | POST | `/internal/ai/itineraries/generate` | 완성 일정 JSON |
| 실제 | POST | `/api/ai/v1/itinerary-jobs/stream` | SSE |
| 목업 | POST | `/mock/api/ai/v1/itinerary-jobs/stream` | SSE (외부 호출 없음, 형식은 실제와 동일) |

- 음악 추천은 API·스트림 단계·응답 필드·코드에서 모두 제외합니다.
- `/health`를 제외한 모든 API는 `Authorization: Bearer <AUDIGO_API_TOKEN>`이 필요합니다.
- Swagger 문서(`/docs`, `/redoc`, `/openapi.json`)는 HTTP Basic 인증(`DOCS_USERNAME`, `DOCS_PASSWORD`)으로 보호합니다. 둘 중 하나라도 설정되지 않으면 문서 페이지를 열지 않습니다.
- 백엔드는 `AUDIGO_AI_SSE_PATH`만 바꾸면 실제 경로와 목업 경로를 전환할 수 있습니다.

## 2. 요청·응답 형식

요청은 **백엔드 형식(`AiTravelGenerationRequest`) JSON 하나로 통일**합니다. 기존 중첩 형식(`generation_job_id`, `region{}`, `duration{}`)은 받지 않습니다.

```json
{
  "travel_plan_id": 10,
  "region_id": 1,
  "region_name": "제주특별자치도 서귀포시",
  "arrival_datetime": "2026-08-27T13:00:00",
  "departure_datetime": "2026-08-29T18:00:00",
  "headcount": 2,
  "companion_type": "COUPLE",
  "preference": {
    "pace_type": "RELAXED",
    "transport_type": "PUBLIC_TRANSPORT",
    "budget_min": 300000, "budget_max": 800000, "budget_type": "KRW",
    "distance_preference": 70,
    "themes": ["NATURE", "FOOD"],
    "foods": ["KOREAN"],
    "extra_request": "둘째 날은 렌터카로 다닐게요"
  },
  "required_places": [{
    "provider": "KAKAO", "provider_place_id": "26338954", "place_name": "성산일출봉",
    "address": "제주특별자치도 서귀포시 성산읍 성산리 1",
    "latitude": 33.458056, "longitude": 126.9425, "place_type": "TOURISM", "order": 1
  }]
}
```

응답 일정의 각 항목에 다음 필드를 추가합니다.

| 필드 | 설명 |
|---|---|
| `meal_type` | `BREAKFAST` / `LUNCH` / `DINNER` / `null` (식당 항목의 끼니) |
| `price_info` | 블로그 검색을 참고한 가격 문구. 확인되지 않으면 `null` |

> 백엔드가 `meal_type`, `price_info`를 저장·표시하려면 백엔드에 필드 추가가 필요합니다. 추가하지 않아도 기존 저장은 그대로 동작합니다.

## 3. 우선순위 규칙

```
사용자 커스텀 요청(extra_request) > 공통 필드(preference) > 서버 기본값
```

- `extra_request`가 있으면 LLM이 먼저 구조화된 JSON(날짜별 이동수단·속도, 출발·귀가 시각, 추가 테마·음식, 피할 것, 기타 메모)으로 바꾸고, 그 값으로 공통 필드를 덮어씁니다.
- 커스텀 요청으로도 바꿀 수 없는 규칙
  - 00:00~07:00 휴식
  - 필수 장소 포함과 순서
  - 카카오 후보 밖 장소 금지
  - 확인되지 않은 정보(영업시간·휴무일·가격) 추측 금지

## 4. 전체 흐름

```
[0] 요청 검사 (백엔드 형식)              ─ 실패: 400
[1] 커스텀 요청 해석 (LLM, 있을 때만)     → 날짜별 조건 확정
[2] 날짜별 활동 시간 계산
[3] PLACE_RECOMMEND  관광 + 아침·점심·저녁
[4] STAY_RECOMMEND   숙소 배치
[5] ROUTE_OPTIMIZE   실제 경로 조회 → 시간표 확정
[6] 가격 참고 정보   블로그 검색 (ROUTE_OPTIMIZE 안에서 처리)
[7] 응답 (JSON / SSE)
```

### [2] 날짜별 활동 시간

| 구분 | 규칙 | 강제 여부 |
|---|---|---|
| 휴식 | 00:00~07:00에는 어떤 일정도 두지 않음 | 반드시 지킴 |
| 첫날 시작 | 도착 시각 + 1시간 (도착 장소 이동 여유) | 반드시 지킴 |
| 마지막 날 종료 | 출발 시각 − 1시간 (출발 장소 이동 여유), 숙소 없음 | 반드시 지킴 |
| 숙소 출발 | 09:00 전후 | 권장 |
| 숙소 도착 | 22:00~23:00 | 권장 |

- 권장 시간 안에 일정이 들어가지 않으면 먼저 저녁을 23:59까지, 그다음 아침을 07:00까지 늘려서 배치합니다.
- 커스텀 요청에 출발·귀가 시각이 있으면 권장값을 그 시각으로 바꿉니다.

### [3] 관광·식사 (PLACE_RECOMMEND)

하루를 다 쓰는 날 기준 관광지 수입니다.

| pace | 최소 | 최대 |
|---|---|---|
| RELAXED (여유) | 2 | 4 |
| BALANCED (균형) | 3 | 5 |
| PACKED (알차게) | 4 | 6 |

- 첫날·마지막 날처럼 시간이 짧은 날은 쓸 수 있는 시간에 비례해 최소·최대 개수를 줄입니다.
- 식사: 아침·점심·저녁을 반드시 추천합니다. 목표 시각은 08시, 12시, 18시 언저리이며 동선에 따라 움직여도 됩니다.
  - 아침은 그날 첫 항목으로 두고, 숙소 출발 직후에 배치합니다.
  - 그날 활동 시간에 걸치는 끼니만 넣습니다 (끼니 시간대와 60분 이상 겹칠 때).
- 도보인 날은 그날 도보 이동 합이 **50km 이하**여야 합니다.
- 숙소 이동 시간은 1단계에서 숙소 위치를 모르므로, 이동수단별 **숙소 이동 기준 거리**(도보 1km, 대중교통 3km, 자동차 5km)로 추정해 미리 비워 둡니다. 고정 20분은 쓰지 않습니다.
- 흐름: 카카오 후보 검색 → 모델이 날짜별 방문 순서와 끼니를 선택 → 서버 검증. 검증에 실패하면 사유를 붙여 1번 더 요청하고, 그래도 실패하면 422입니다.

### [4] 숙소 배치 (STAY_RECOMMEND)

```
그날과 다음날이 모두 자동차인가?
├─ 예    → 밤마다 [그날 마지막 장소 ↔ 다음날 첫 장소]의 중간 지점 근처에서 새로 고름
└─ 아니오 → 전날 숙소가 마지막 장소 → 숙소, 숙소 → 다음날 첫 장소 모두 편도 1시간 이하면 같은 숙소 유지
            아니면 중간 지점 근처에서 새로 고름
```

- 검색 반경: 도보 1.5km, 대중교통 5km, 자동차 10km (그날·다음날 중 느린 이동수단 기준). 들어가는 숙소를 못 찾으면(결과 없음 또는 모두 탈락) 반경을 1.5배로 넓혀 1번 더 찾고, 그래도 없으면 422입니다.
- 후보는 [마지막 장소 → 숙소] + [숙소 → 다음날 첫 장소] 이동 합이 작은 순서로 시도하고, 앞뒤 날짜 시간표가 모두 들어가는 첫 후보를 고릅니다.
- 체크인 시각은 제한하지 않습니다.
- 사용자가 지정한 숙소는 순서대로 밤에 먼저 배정합니다.
- 숙소 항목의 시간은 `start_time = 도착 시각`, `end_time = "23:59"`입니다.

### [5] 경로 확정 (ROUTE_OPTIMIZE)

- 카카오 길찾기로 방문지 사이 실제 이동시간을 조회합니다. 전날 숙소 → 다음날 첫 장소도 포함합니다.
- 자동차는 카카오 REST에 경로 API가 없어 좌표 기반 추정치를 씁니다.
- 실제 이동시간으로 시간표를 다시 계산하고 [2]의 필수 규칙과 도보 50km를 다시 검증합니다. 들어가지 않으면 422입니다.
- 대중교통 경로가 없는 짧은 거리는 도보로 대체합니다.

### [6] 가격 참고 정보

- 최종 일정의 모든 장소를 카카오 블로그 검색으로 찾습니다. 검색어는 장소명 + 유형별 키워드(관광: 입장료, 식당: 가격·메뉴, 숙소: 숙박비)입니다.
- 검색 요약문을 LLM에 한 번에 넘겨 장소별 가격 문구를 만듭니다. 요약문에 가격이 없으면 `null`이며 추측하지 않습니다. 출처는 적지 않습니다.
- 가격 정보는 장소 선택에 쓰지 않고 결과에만 붙입니다.
- 이 단계가 실패해도 일정 생성은 실패하지 않고, 가격만 `null`이 됩니다.

## 5. SSE 이벤트

실제 경로와 목업 경로가 같은 코드를 씁니다.

```
id:1 PLACE_RECOMMEND_STARTED   {stage, status:"RUNNING"}
id:2 PLACE_RECOMMEND_DONE      {stage, status:"DONE"}
id:3 STAY_RECOMMEND_STARTED
id:4 STAY_RECOMMEND_DONE
id:5 ROUTE_OPTIMIZE_STARTED
id:6 ROUTE_OPTIMIZE_DONE       {travel_plan_id, stage, status:"DONE", result:{title, days[{items, routes}]}}
id:7 complete                  {travel_plan_id, stage:"COMPLETE", status:"COMPLETED"}
```

- 최종 일정은 `ROUTE_OPTIMIZE_DONE`에 한 번만 담습니다.
- 10초마다 `: keep-alive`, 300초가 지나면 `error`를 보냅니다.
- 실패하면 `error {travel_plan_id, stage, status:"FAILED", message, data:{error_message}}`를 보내고 종료합니다.

## 6. 목업 경로

- 실제 파이프라인을 그대로 쓰고, 외부 클라이언트(카카오·OpenAI)만 가짜 구현으로 바꿉니다. 시간 계산·숙소 배치·검증 로직이 실제와 같습니다.
- 같은 요청에는 항상 같은 결과를 줍니다 (`travel_plan_id`로 난수 고정).
- 단계 사이에 `MOCK_STAGE_DELAY_SECONDS`(기본 1초)만큼 기다립니다.
- 실패 시나리오: 헤더 `X-Mock-Scenario`를 먼저 보고, 없으면 환경변수 `MOCK_SCENARIO`를 씁니다.

| 값 | 동작 |
|---|---|
| `success` (기본값) | 정상 완료 |
| `fail_place` / `fail_stay` / `fail_route` | 해당 단계에서 `error` 이벤트 |
| `unavailable` | 503 `ai_service_unavailable` |
| `drop` | 결과 없이 스트림 종료 |

## 7. 오류 응답

형식은 `{"message": "<코드>", "data": {...} | null}`입니다.

| 코드 | HTTP | 언제 |
|---|---|---|
| `invalid_request` | 400 | 요청 형식 오류. `필드: 이유` 형식으로 최대 5개 (예: `departure_datetime: arrival_datetime보다 늦어야 합니다.`). 입력 값은 담지 않음 |
| `unauthorized` | 401 | 토큰 없음 또는 불일치 |
| `ai_itinerary_generation_failed` | 422 | 시간 부족, 후보 없음, 숙소 없음, 배치 불가 (`data`에 `travel_plan_id` 포함) |
| `ai_service_unavailable` | 503 | 외부 API 장애, 키 없음, 타임아웃 |
| `routing_service_unavailable` | 503 | 길찾기 장애 |
| `internal_server_error` | 500 | 예상하지 못한 오류 |

## 8. 로그

모든 로그에 `request_id`를 붙이고, 요청 본문과 API 키는 남기지 않습니다.

| 이벤트 | 내용 |
|---|---|
| `generation_stage` | 단계, 상태, 전체·단계 소요 시간(ms) |
| `custom_request_parsed` | 커스텀 요청에서 덮어쓴 항목 이름 |
| `place_collection` | 지역, 반경, 후보 수, 걸러진 사유별 개수 |
| `place_selection_retry` | 1단계 재요청 사유 |
| `day_spare` | 1단계 후 날짜별 남은 시간(분), 비워 둔 숙소 이동 시간(분) |
| `accommodation_selected` | 날짜, 기준점, 반경, 반경 확대·재사용 여부, 숙소까지 이동 시간 |
| `accommodation_unavailable` | 날짜, 후보 수, 후보별 탈락 사유와 부족한 시간(분) |
| `price_lookup` | 조회 장소 수, 가격을 찾은 수, 실패 여부 |
| `pipeline_failed` / `request_failed` | 오류 코드, 사유, 발생 위치 |

## 9. 폴더 구조

```
app/
├── main.py                    # 앱 생성, 미들웨어·예외 핸들러·라우터 등록
├── core/                      # config, security, exceptions, logging, geo(거리·이동시간 추정)
├── api/
│   ├── deps.py                # 설정, 파이프라인 의존성, X-Mock-Scenario
│   └── routes/                # health, itinerary(실제), mock(목업)
├── schemas/                   # common, itinerary, route, planner
├── prompts/                   # 프롬프트 (커스텀은 여기서)
├── clients/                   # openai_client, kakao_places, kakao_routes, kakao_search, mock(목업용 가짜 카카오)
├── services/
│   ├── scheduler.py           # 날짜별 활동 시간, 시간 배정, 검증
│   ├── planner.py             # 커스텀 요청 해석, 장소 선택 (LLM)
│   ├── places.py              # 후보 정리
│   ├── accommodation.py       # 숙소 배치
│   ├── routes.py              # 실제 경로로 시간표 확정
│   ├── pricing.py             # 가격 참고 정보
│   ├── pipeline.py            # 단계 실행
│   ├── mock_generator.py      # 목업 플래너(LLM 대신), 목업 시나리오
│   └── streaming/             # sse, backend_adapter
└── docs/openapi_examples.py   # Swagger 예시
```

## 10. 테스트

`docker build --target test .`으로 실행합니다.

- 시간: 00~07시 휴식, 첫날 도착 + 1시간, 마지막 날 출발 − 1시간, 권장 시간을 넘길 때 확장
- 개수: pace별 관광 최소·최대, 짧은 날 비례 축소, 끼니 포함 규칙, 아침이 첫 항목
- 숙소: 자동차 연속 날의 밤별 선택, 편도 1시간 이내 재사용, 반경 확대 재검색, 모두 탈락 시 422 + 로그
- 도보 하루 50km 상한
- 커스텀 요청이 공통 필드를 덮어쓰는지
- 가격: 요약문에 없으면 null, 검색 실패해도 일정 성공
- API: 401, 400, JSON 응답, SSE 이벤트 순서, `result` 1회, `music` 없음
- 목업: 시나리오 6가지, 헤더 우선, 같은 요청에 같은 결과
