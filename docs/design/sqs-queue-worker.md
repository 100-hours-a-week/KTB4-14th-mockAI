# SQS 큐 워커 설계

기본값은 기존 실제 AI·순차 처리입니다. 환경변수로 `live/mock/off`와 프로세스당 동시 처리 수를 선택하는 현재 구성 및 테스트 환경 분리 절차는 [SQS Mock 부하 테스트 안내](../sqs-mock-load-test.md)를 참고하세요.

여행 생성 요청을 SQS로 받아 처리하고, 진행 상태와 결과를 SQS로 돌려주는 워커입니다.

```text
Backend ──SendMessage──▶ AI Job Queue ──Receive/Delete──▶ AI Worker (이 서버)
Backend ◀─Receive/Delete── AI Result Queue ◀──SendMessage── AI Worker (이 서버)
```

메시지 형식은 백엔드 계약 문서(`KTB4-14th-BE`의 `feature/sqs-integration` 브랜치, `docs/travel-generation-sqs-contract.md`)를 따릅니다.

## 결정 사항

| 항목 | 결정 |
|---|---|
| 실행 위치 | FastAPI 프로세스 안에서 lifespan 백그라운드 태스크로 실행합니다. 별도 컨테이너는 두지 않습니다. |
| 활성화 조건 | `AUDIGO_AI_JOB_QUEUE_URL`, `AUDIGO_AI_RESULT_QUEUE_URL`이 모두 있을 때만 워커를 켭니다. 별도 on/off 환경변수는 없습니다. |
| 동시성 | 한 번에 메시지 1개를 받고, 작업도 하나씩 순서대로 처리합니다. |
| 제한 시간 | 기존 `generation_timeout_seconds`(300초)를 그대로 씁니다. Job Queue Visibility Timeout이 360초라서 한 번 시도하고 결과를 발행할 시간이 충분합니다. Visibility 연장(`ChangeMessageVisibility`)은 하지 않습니다. |
| 재시도 | 생성은 재시도하지 않습니다. 한 번 실패하면 바로 `FAILED`를 발행합니다. |
| 중복 방지 | 프로세스 메모리에 `generation_job_id` 집합을 둡니다. AI 서버는 한 대만 운영합니다. |
| DLQ | 사용하지 않습니다. |
| SQS 클라이언트 | `boto3`를 쓰고, 동기 호출은 `asyncio.to_thread`로 감쌉니다. 자격 증명은 ECS Task Role(기본 자격 증명 체인)을 씁니다. |

## 환경 변수

운영 환경에는 이미 설정되어 있습니다.

| 환경 변수 | 설명 |
|---|---|
| `AWS_REGION` | 큐가 있는 리전 (`ap-northeast-2`) |
| `AUDIGO_AI_JOB_QUEUE_URL` | 요청을 받는 큐 |
| `AUDIGO_AI_RESULT_QUEUE_URL` | 결과를 보내는 큐 |

## 메시지

### Job 메시지 (수신)

```json
{
  "event_id": "4c3ee3b4-7fb7-4dd7-9f11-ded3455e70f0",
  "generation_job_id": 41,
  "message_version": "1",
  "occurred_at": "2026-10-16T01:00:00Z",
  "request": { "travel_plan_id": 10, "region_name": "서울특별시", "...": "TravelGenerationRequest와 같은 형식" }
}
```

`request`는 기존 `TravelGenerationRequest` 스키마로 검증합니다.

### Result 메시지 (발행)

공통 필드는 `event_id`(메시지마다 새 UUID), `generation_job_id`, `message_version: "1"`, `occurred_at`(UTC)입니다.

| `type` | 보내는 시점 | 채우는 값 |
|---|---|---|
| `PROGRESS` | 각 단계 시작·완료 | `stage`, `state` (`RUNNING` / `DONE`) |
| `COMPLETED` | 일정 생성 완료 | `stage: "ROUTE_OPTIMIZE"`, `state: "DONE"`, `payload`: `backend_result()` 결과 (`{title, days}`) |
| `FAILED` | 생성 실패 | `error_message`: 사용자에게 보여줄 문구 |

- 단계 이름은 SSE와 같습니다. `PLACES` → `PLACE_RECOMMEND`, `ACCOMMODATIONS` → `STAY_RECOMMEND`, `ROUTES` → `ROUTE_OPTIMIZE`.
- `ROUTE_OPTIMIZE`의 `DONE`은 따로 보내지 않고 `COMPLETED`로 대신합니다. SSE에서 `ROUTE_OPTIMIZE_DONE`에 최종 일정을 담는 것과 같습니다.
- `error_message`에는 `ApiError.guide`가 있으면 그 값을, 없으면 `ApiError.message`를 넣습니다. 백엔드가 이 문구를 실패 알림에 그대로 씁니다.

## 처리 흐름

1. Job Queue를 long polling(20초, 최대 1개)으로 조회합니다. 받은 메시지가 없으면 다시 조회합니다.
2. 메시지 바디를 검증합니다.
3. `generation_job_id`가 처리 기록에 있으면 AI를 호출하지 않고 메시지만 삭제합니다.
4. 처리 기록에 `generation_job_id`를 추가한 뒤 생성을 시작합니다. 처리 중에 같은 작업이 다시 들어와도 3번에서 걸러집니다.
5. `stage_events(generation_stages(request, providers), ...)`를 실행하면서 단계마다 `PROGRESS`를 발행합니다.
6. 끝나면 `COMPLETED` 또는 `FAILED`를 발행합니다.
7. 최종 결과 발행에 성공하면 Job 메시지를 삭제합니다.

## 실패 처리

| 상황 | 처리 |
|---|---|
| JSON이 아니거나 `generation_job_id`를 읽을 수 없음 | 결과를 보낼 대상이 없으므로 로그만 남기고 삭제합니다. 남겨 두면 보존 기간(4일) 동안 계속 다시 들어옵니다. |
| `message_version`이 `"1"`이 아님 / `request` 검증 실패 | `FAILED`를 발행하고 삭제합니다. |
| 생성 중 예외 (`ApiError`, 5분 초과 포함) | `FAILED`를 발행하고 삭제합니다. `ApiError`가 아닌 예외는 SSE와 같이 `internal_server_error`로 바꿉니다. |
| `PROGRESS` 발행 실패 | 로그만 남기고 생성을 계속합니다. |
| `COMPLETED` / `FAILED` 발행 실패 | 그 자리에서 짧은 간격으로 3번까지 다시 시도합니다. 그래도 실패하면 Job 메시지를 삭제하지 않습니다. 6분 뒤 다시 들어오면 처리 기록에 걸려 삭제되고, 백엔드는 watchdog으로 작업을 실패 처리합니다. |
| Job 메시지 삭제 실패 | 로그만 남깁니다. 다시 들어오면 처리 기록에 걸려 삭제됩니다. |
| SQS 조회 실패 | 로그를 남기고 잠시 기다린 뒤 다시 조회합니다. 워커 태스크는 죽지 않습니다. |

## 중복 방지

SQS Standard Queue는 같은 메시지를 두 번 이상 전달할 수 있습니다. 이 밖에도 백엔드 Outbox가 재발행하거나 워커가 메시지 삭제에 실패하면 같은 Job이 다시 들어옵니다. AI 서버는 백엔드 DB를 조회하지 않으므로, AI 호출 비용을 막으려면 처리한 작업을 직접 기억해야 합니다.

- 키는 `generation_job_id`입니다. 백엔드에서 재생성하면 새 작업 ID가 생기므로 정상적인 재생성은 막히지 않습니다.
- 최근 1,000건까지만 보관하고, 오래된 것부터 지웁니다.
- 생성 결과는 저장하지 않습니다. 작업 ID만 기록합니다.
- 서버를 재시작하면 기록이 사라집니다. 재배포 직후에 들어온 중복은 다시 생성될 수 있습니다.

## 종료

lifespan이 끝나면 조회와 진행 중인 생성을 취소합니다. 진행 중이던 Job 메시지는 삭제되지 않았으므로 6분 뒤 다시 보이게 되고, 새 프로세스가 처리합니다.

boto3 Long Polling은 스레드에서 돌기 때문에 취소해도 바로 끝나지 않습니다. 프로세스 종료가 최대 20초 늦어질 수 있습니다(ECS 기본 종료 대기 30초 이내). 그 사이에 받은 메시지는 처리되지 않고 6분 뒤 다시 보입니다.

## 파일 구성

| 파일 | 내용 |
|---|---|
| `app/clients/sqs.py` | `boto3` SQS 호출(receive / send / delete)을 async로 감싼 클라이언트 |
| `app/services/queue_worker.py` | 조회 루프, 중복 방지, Job 검증, 단계 → Result 메시지 변환, 발행 재시도 |
| `app/core/config.py` | 큐 URL, 리전 설정 추가 |
| `.env.example` | 큐 환경변수 추가 |
| `app/main.py` | lifespan에서 워커 시작·종료 |
| `pyproject.toml`, `uv.lock` | `boto3` 추가 |
| `tests/services/test_queue_worker.py` | 가짜 SQS 클라이언트로 워커 검증 |
| `tests/services/test_sqs.py` | botocore Stubber로 SQS 호출 인자가 AWS API 형식에 맞는지 검증 |
| `README.md` | 큐 연동 섹션 추가 |

## 테스트

가짜 SQS 클라이언트와 기존 가짜 Provider(`/mock`용)를 써서 외부 호출 없이 검증합니다.

- 정상 처리: `PROGRESS` 순서, `COMPLETED`의 `payload`, Job 메시지 삭제
- 중복: 같은 `generation_job_id`가 두 번 오면 생성은 한 번만 하고 두 메시지 모두 삭제
- 생성 실패, 5분 초과: `FAILED`의 `error_message`, 삭제
- 잘못된 메시지: 파싱 불가(삭제만), 버전 불일치·`request` 검증 실패(`FAILED` 후 삭제)
- 최종 결과 발행 실패: 3번 다시 시도한 뒤 삭제하지 않음
- 큐 URL이 없으면 워커가 시작되지 않음

## 범위 밖

- Visibility Timeout 연장, 생성 재시도
- DLQ
- 여러 대 사이의 중복 방지 (외부 저장소)
- 1MiB를 넘는 결과 처리 (S3)
- AI Task Role IAM 권한 설정, 백엔드 계약 문서 수정

## 참고

- 백엔드 계약 문서에는 Job Queue Visibility Timeout이 960초로 적혀 있지만, 실제 큐 설정(Cloud `deploy/parameters/sqs.json`)은 360초입니다. 이 설계는 360초를 기준으로 합니다.
