# Audigo Mock AI API

Audigo 여행 일정 생성 AI 서버(FastAPI)입니다.

- **실제 AI 서버:** OpenAI와 카카오(장소 검색·길찾기)를 실제로 호출해 여행 일정을 만듭니다. 기존 `KTB4-14th-AI`의 일정 생성·스트리밍 기능을 기반으로 하며, 음악 추천은 제외합니다.
- **목업 데이터 전용 경로:** `/mock`으로 시작하는 경로는 외부 API를 호출하지 않고 가짜 일정을 반환합니다. 응답 형식은 실제 경로와 같아서, 백엔드·프론트 개발과 테스트에 그대로 쓸 수 있습니다.

## API

| 구분 | Method | 경로 | 설명 | 상태 |
|---|---|---|---|---|
| 공통 | GET | `/health` | 서버 상태 확인 | 완료 |
| 실제 | POST | `/internal/ai/itineraries/generate` | 일정 생성 (JSON 응답) | 완료 |
| 실제 | POST | `/api/ai/v1/itinerary-jobs/stream` | 일정 생성 (SSE 스트리밍) | 완료 |
| 목업 | POST | `/mock/api/ai/v1/itinerary-jobs/stream` | 가짜 일정 생성 (SSE 스트리밍) | 완료 |
| 매칭 | POST | `/api/ai/v1/matches/candidates` | 성별·속도 필터와 테마 점수 기반 후보 카드 추천 (JSON) | 구현 |

- `/health`를 뺀 모든 API는 `Authorization: Bearer <AUDIGO_API_TOKEN>` 헤더가 필요합니다.
- 백엔드는 환경변수 `AUDIGO_AI_SSE_PATH`만 바꾸면 실제 경로와 목업 경로를 전환할 수 있습니다.
- 오류 응답은 모두 `{"message": "<오류 코드>", "data": {...} | null}` 형식입니다.

### SSE 이벤트 순서

```
PLACE_RECOMMEND_STARTED → PLACE_RECOMMEND_DONE
→ STAY_RECOMMEND_STARTED → STAY_RECOMMEND_DONE
→ ROUTE_OPTIMIZE_STARTED → ROUTE_OPTIMIZE_DONE (result에 최종 일정 포함)
→ complete
```

실패하면 그 시점에 `error` 이벤트를 보내고 스트림을 종료합니다.

### 목업 실패 시나리오

`/mock` 경로에서는 실패 상황을 강제로 만들 수 있습니다. 요청 헤더 `X-Mock-Scenario`가 있으면 헤더 값을 쓰고, 없으면 환경변수 `MOCK_SCENARIO`를 씁니다.

| 값 | 동작 |
|---|---|
| `success` (기본값) | 정상 완료 |
| `fail_place` / `fail_stay` / `fail_route` | 해당 단계에서 `error` 이벤트 |
| `unavailable` | 503 `ai_service_unavailable` |
| `drop` | 결과 없이 스트림 종료 |

## 일정 생성 로직

자세한 설계는 [docs/design/itinerary-generation.md](docs/design/itinerary-generation.md)에 있습니다.

- 요청은 백엔드 형식(`AiTravelGenerationRequest`) JSON 하나만 받습니다.
- 우선순위: 사용자 커스텀 요청(`extra_request`) > 공통 필드(`preference`) > 서버 기본값
- 00:00~07:00은 무조건 휴식입니다. 첫날은 도착 + 1시간부터, 마지막 날은 출발 − 1시간까지 일정을 둡니다.
- 관광지 수(여유/균형/알차게): 최소 2/3/4, 최대 4/5/6. 짧은 날은 시간에 비례해 줄입니다.
- 아침·점심·저녁을 08시·12시·18시 언저리에 추천합니다. 식당은 영업시간, 정기 휴무 요일, 대표 메뉴(카카오 공식 검색 참고)를 고려합니다.
- 시장은 점심·저녁 자리에도 들어가며, 이때는 시장 안 식당(최상단 추천 가게)으로 표기합니다. 관광지로 넣는 시장은 하루 1곳까지입니다.
- 숙소: 그날과 다음날이 모두 자동차면 밤마다 동선 중간 지점에서 새로 고릅니다. 그 밖에는 편도 1시간 이내면 같은 숙소를 유지합니다.
- 모든 장소에 카카오 공식 검색(블로그·웹문서·카페)을 참고한 성인 1명 기준 가격(`price_info`)을 붙입니다. 관광 `성인 19,000원`, 식당 `1인분 평균 11,000원`, 숙소 `1박 120,000원`, 알 수 없으면 `확인 필요`입니다.
- 프롬프트는 [app/prompts/itinerary.py](app/prompts/itinerary.py)에서 수정합니다.

## 여행 동행 매칭

[BE 계약 반영 매칭 설계안](docs/design/audigo-matching-ai-score-spec-be-aligned.md)에 따른 **규칙 기반 추천**입니다. LLM·카카오·DB·큐를 사용하지 않으며, `AUDIGO_API_TOKEN`만 설정하면 외부 API 키 없이 사용할 수 있습니다. 여행 일정 파이프라인과 독립적으로 실행합니다.

- 최상위는 `request`, `candidates`, 선택 `limit`입니다. 요청자 ID·테마·속도·성별 선호·예산은 `request` 안에 전달합니다. 이전 최상위 조건 형식은 지원하지 않습니다.
- BE가 실제 후보 목록과 필수 닉네임·성별을 공급합니다. `profile_image_url`은 문자열 또는 null이며 생략하면 null입니다. AI는 입력 후보의 닉네임·이미지·전체 테마·속도를 복사하여 카드를 반환하며 프로필을 생성·조회하지 않습니다.
- `request.preferred_companion_gender`는 `ANY`, `FEMALE`, `MALE`입니다. 후보 `gender`는 `FEMALE` 또는 `MALE`이고, 요청 조건과 맞지 않으면 제외합니다. 성별은 점수에 가산하지 않습니다.
- 노출 동의·계정 상태·현재 채팅 상대 제외, 작업 관리·결과 저장·알림은 BE 책임입니다.
- 테마 점수는 `교집합 수 / 요청 테마 수`입니다. 후보의 추가 테마는 감점하지 않습니다.
- 속도가 같으면 1점, 인접하면 0.5점이며 `RELAXED ↔ PACKED` 조합은 제외합니다.
- `match_rate = floor(100 × (0.8 × 테마 점수 + 0.2 × 속도 점수) + 0.5)`로 계산합니다. 실제 구현은 정확한 정수 연산을 사용합니다.
- 본인을 제외하고 **정수 점수 내림차순 → Python 문자열 기준 닉네임 오름차순 → 사용자 ID 오름차순**으로 정렬합니다. 낮은 점수를 임의로 제외하지 않습니다.
- `limit`은 정수 1~3, 생략하면 3입니다. 후보 없음·모두 제외는 HTTP 200과 빈 목록입니다.
- ID와 limit은 JSON 정수만 허용합니다. 테마는 요청 1~3개·후보 1~4개이며 중복을 금지합니다. 테마 enum은 `NATURE`, `FOOD`, `CULTURE`, `REST`, `SNS`, `ACTIVITY`, 속도 enum은 `RELAXED`, `BALANCED`, `PACKED`입니다. 후보 ID 중복·공백뿐인 닉네임·성별 누락/null·알 수 없는 필드는 전체 요청 오류입니다.
- `request.budget_min`·`budget_max`는 생략 시 null이며 둘 다 null이거나 둘 다 JSON 정수여야 합니다. 범위는 0~3,000,000, min ≤ max입니다. **예산은 검증만 하며 점수·필터에는 사용하지 않습니다.** 한쪽만 값이 있으면 오류입니다.
- 매칭 입력 검증 오류는 HTTP **422**이며 기존 `message/data` 오류 형식을 사용합니다. 기존 일정 API의 입력 오류 HTTP 400은 유지합니다.
- `MATCHING_MAX_CANDIDATES` 초과 시 전체 요청을 거부하며 잘라서 평가하지 않습니다. 기본 1000은 **초기 구현용 임시 기준**이고 BE 연동 시 조정합니다. 80:20 가중치·정렬·JSON 계약도 연동 시 확정할 제안입니다.
- AI 작업 ID·SSE·상태 조회·취소 API는 제공하지 않습니다. 결과가 결정적이므로 재호출은 가능하지만 결과 저장·알림 중복 방지는 BE 책임입니다.

로컬 서버 실행 후 서버와 같은 토큰으로 호출합니다.

```bash
export AUDIGO_API_TOKEN='<서버에 설정한 32자 이상의 토큰>'
curl -sS http://localhost:8000/api/ai/v1/matches/candidates \
  -H "Authorization: Bearer $AUDIGO_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"request":{"matching_request_id":10,"user_id":1,"preferred_companion_gender":"FEMALE","theme":["NATURE","FOOD"],"pace":"RELAXED","budget_min":100000,"budget_max":800000},"candidates":[{"user_id":23,"nickname":"나나","profile_image_url":null,"gender":"FEMALE","theme":["NATURE","FOOD","REST"],"pace":"RELAXED"},{"user_id":24,"nickname":"다다","profile_image_url":null,"gender":"FEMALE","theme":["NATURE","CULTURE"],"pace":"BALANCED"},{"user_id":25,"nickname":"라라","gender":"MALE","theme":["NATURE","FOOD"],"pace":"RELAXED"}]}'
```

예상 응답은 `candidates/count` JSON입니다. 후보 25는 성별 조건으로 제외됩니다. FE용 `message/data` 래퍼와 요청 ID·계산 버전은 성공 응답에 추가하지 않습니다.

```json
{
  "candidates": [
    {"user_id":23,"nickname":"나나","profile_image_url":null,"theme":["NATURE","FOOD","REST"],"pace":"RELAXED","match_rate":100},
    {"user_id":24,"nickname":"다다","profile_image_url":null,"theme":["NATURE","CULTURE"],"pace":"BALANCED","match_rate":50}
  ],
  "count": 2
}
```

BE 후보 DTO에 닉네임·이미지 공급이 필요하므로, 이 구현만으로 실제 BE 연동이 완료된 것은 아닙니다. BE의 4xx·5xx 처리 때문에 AI의 422가 FE에 그대로 전달된다고 가정하지 않습니다. 예산 미반영 정책·후보 상한은 BE·제품 담당과 합의해야 합니다.

닉네임은 주변 공백을 포함한 **원문 그대로 정렬·반환**합니다. 공백뿐인 값만 거부하며 별도 대소문자·한글 정규화는 하지 않습니다. 이미지 문자열과 테마 순서도 그대로 보존합니다. `matching_request_id`, 입력·필터 후·반환 후보 수, 처리 시간, `ranking_version=theme-pace-v1`은 내부 로그에 기록하되 후보 개인정보·토큰은 기록하지 않습니다.

대용량 입력 방어를 위해 닉네임은 원문 기준 최대 500자, 본문은 기본 2MiB로 제한합니다. 본문은 청크 전송을 포함해 JSON 디코딩 **전** 바이트 수로 검사하고 초과 시 422를 반환합니다. 두 값은 초기 구현용 안전 상한이지 확정된 BE 프로필 계약은 아니며, 연동 시 조정합니다. 운영 프록시의 요청 크기·속도·읽기 시간 제한은 별도로 적용해야 합니다.

## 실행 (Docker)

로컬에 Python을 설치하지 않고 Docker로 실행합니다.

```bash
cp .env.example .env   # AUDIGO_API_TOKEN 32자 이상 입력

docker build -t audigo-mock-ai .
docker run --rm -p 8000:8000 --env-file .env audigo-mock-ai
```

- Swagger: http://localhost:8000/docs (`DOCS_USERNAME`/`DOCS_PASSWORD`로 로그인. 설정하지 않으면 문서 페이지가 열리지 않습니다)
- Health: http://localhost:8000/health

## 환경변수

| 이름 | 설명 |
|---|---|
| `AUDIGO_API_TOKEN` | 백엔드와 공유하는 Bearer 토큰 (32자 이상) |
| `OPEN_API_KEY` | OpenAI API 키 (실제 경로) |
| `OPENAI_MODEL` | 사용할 모델, 기본 `gpt-4o-mini` |
| `KAKAO_REST_API_KEY` | 카카오 장소 검색·길찾기·검색(블로그·웹문서·카페) 키 (실제 경로) |
| `DOCS_USERNAME` | Swagger·ReDoc·openapi.json 접근 아이디 |
| `DOCS_PASSWORD` | 위 비밀번호 (8자 이상). 아이디·비밀번호 중 하나라도 없으면 문서 페이지를 열지 않습니다 |
| `MOCK_STAGE_DELAY_SECONDS` | 목업 단계 이벤트 사이 대기 시간(초), 기본 1 |
| `MOCK_SCENARIO` | 목업 기본 시나리오, 기본 `success`. 허용 값 밖이면 서버가 시작되지 않습니다. |
| `MATCHING_MAX_CANDIDATES` | 매칭 후보 전달 상한. 양의 정수, 초기 구현 기본 1000. 초과하면 422이며 BE 연동 시 조정 |
| `MATCHING_MAX_BODY_BYTES` | 매칭 JSON 본문 최대 바이트 수. 양의 정수, 초기 기본 2097152(2MiB), 초과 시 422 |

## 테스트

Dockerfile의 `test` 단계에서 pytest를 실행합니다. 테스트가 실패하면 해당 테스트 빌드도 실패합니다. 일반 runtime 빌드에서는 테스트 단계가 실행되지 않으므로 아래 명령으로 명시적으로 검증합니다.

```bash
docker build --target test .
```

전체 검증 후 같은 이미지에서 매칭 테스트만 반복 실행할 수 있습니다.

```bash
docker build --target test -t audigo-matching-ai-test .
docker run --rm --entrypoint /app/.venv/bin/pytest audigo-matching-ai-test \
  tests/services/test_matching_scoring.py tests/services/test_matching.py tests/api/test_matching.py
```

테스트에는 모든 유효 테마 부분집합·속도 조합 20,664개의 독립 분수 계산 비교, 중첩 요청·성별 필터·예산 검증·원문 카드 복사·점수·동점 정렬·입력 상한·인증·개인정보 로그 방지·기존 API 회귀 검증을 포함합니다. 실제 BE 호출 검증은 별도 연동 단계입니다.

## 의존성 추가

`pyproject.toml`에 패키지를 추가한 뒤 `uv.lock`을 갱신해야 합니다. Docker 빌드는 `uv sync --frozen`을 쓰므로, lock 파일이 맞지 않으면 빌드가 실패합니다.

```bash
docker run --rm -v "$PWD":/app -w /app ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim uv lock
```

## 폴더 구조

```
app/
├── main.py            # 앱 생성, 미들웨어·예외 핸들러·라우터 등록
├── core/              # config, security, exceptions, logging, geo(거리·이동시간 추정)
├── api/
│   ├── deps.py        # 설정, 외부 의존성, X-Mock-Scenario
│   └── routes/        # health, itinerary(실제), mock(목업)
├── schemas/           # 요청·응답·LLM 입출력 Pydantic 모델
├── prompts/           # 모델 프롬프트 (커스텀은 여기서)
├── clients/           # OpenAI, 카카오(장소·길찾기·블로그), 목업용 가짜 카카오
├── services/          # scheduler, planner, accommodation, routes, pricing, pipeline, mock_generator, streaming/
└── docs/              # Swagger 예시
docs/design/           # 설계 문서
tests/
├── api/               # 엔드포인트 테스트
└── services/          # 로직 테스트
```
