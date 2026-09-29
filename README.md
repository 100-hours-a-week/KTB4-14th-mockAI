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
- 아침·점심·저녁을 08시·12시·18시 언저리에 추천합니다.
- 숙소: 그날과 다음날이 모두 자동차면 밤마다 동선 중간 지점에서 새로 고릅니다. 그 밖에는 편도 1시간 이내면 같은 숙소를 유지합니다.
- 모든 장소에 블로그 검색을 참고한 가격 문구(`price_info`)를 붙입니다. 확인되지 않으면 `null`입니다.
- 프롬프트는 [app/prompts/itinerary.py](app/prompts/itinerary.py)에서 수정합니다.

## 실행 (Docker)

로컬에 Python을 설치하지 않고 Docker로 실행합니다.

```bash
cp .env.example .env   # AUDIGO_API_TOKEN 32자 이상 입력

docker build -t audigo-mock-ai .
docker run --rm -p 8000:8000 --env-file .env audigo-mock-ai
```

- Swagger: http://localhost:8000/docs
- Health: http://localhost:8000/health

## 환경변수

| 이름 | 설명 |
|---|---|
| `AUDIGO_API_TOKEN` | 백엔드와 공유하는 Bearer 토큰 (32자 이상) |
| `OPEN_API_KEY` | OpenAI API 키 (실제 경로) |
| `OPENAI_MODEL` | 사용할 모델, 기본 `gpt-4o-mini` |
| `KAKAO_REST_API_KEY` | 카카오 장소 검색·길찾기·블로그 검색 키 (실제 경로) |
| `MOCK_STAGE_DELAY_SECONDS` | 목업 단계 이벤트 사이 대기 시간(초), 기본 1 |
| `MOCK_SCENARIO` | 목업 기본 시나리오, 기본 `success`. 허용 값 밖이면 서버가 시작되지 않습니다. |

## 테스트

Dockerfile의 `test` 단계에서 pytest를 실행합니다. 테스트가 실패하면 빌드도 실패합니다.

```bash
docker build --target test .
```

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
