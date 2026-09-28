# Audigo Mock AI API

Audigo 여행 일정 생성 AI 서버(FastAPI)입니다.

- **실제 AI 서버:** OpenAI와 카카오(장소 검색·길찾기)를 실제로 호출해 여행 일정을 만듭니다. 기존 `KTB4-14th-AI`의 일정 생성·스트리밍 기능을 기반으로 하며, 음악 추천은 제외합니다.
- **목업 데이터 전용 경로:** `/mock`으로 시작하는 경로는 외부 API를 호출하지 않고 가짜 일정을 반환합니다. 응답 형식은 실제 경로와 같아서, 백엔드·프론트 개발과 테스트에 그대로 쓸 수 있습니다.

## API

| 구분 | Method | 경로 | 설명 | 상태 |
|---|---|---|---|---|
| 공통 | GET | `/health` | 서버 상태 확인 | 완료 |
| 실제 | POST | `/internal/ai/itineraries/generate` | 일정 생성 (JSON 응답) | 예정 |
| 실제 | POST | `/api/ai/v1/itinerary-jobs/stream` | 일정 생성 (SSE 스트리밍) | 예정 |
| 목업 | POST | `/mock/api/ai/v1/itinerary-jobs/stream` | 가짜 일정 생성 (SSE 스트리밍) | 예정 |

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
| `OPEN_API_KEY` | OpenAI API 키 (실제 경로, 예정) |
| `OPENAI_MODEL` | 사용할 모델, 기본 `gpt-4o-mini` (예정) |
| `KAKAO_REST_API_KEY` | 카카오 장소 검색·길찾기 키 (실제 경로, 예정) |
| `MOCK_STAGE_DELAY_SECONDS` | 목업 단계 이벤트 사이 대기 시간(초) (예정) |
| `MOCK_SCENARIO` | 목업 기본 실패 시나리오 (예정) |

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
├── core/              # 공통 설정
│   ├── config.py      # .env / 환경변수 로딩
│   ├── security.py    # Bearer 토큰 검증
│   └── exceptions.py  # 예외 핸들러, 오류 응답 형식
├── api/
│   └── routes/        # 엔드포인트 (health, 실제 일정, 목업 일정)
├── schemas/           # 요청·응답 Pydantic 모델
├── prompts/           # 모델 프롬프트 (커스텀은 여기서)
├── clients/           # 외부 API 클라이언트 (OpenAI, 카카오)
└── services/          # 일정 생성 로직, 목업 데이터 생성, SSE
tests/
├── api/               # 엔드포인트 테스트
└── services/          # 로직 테스트
```
