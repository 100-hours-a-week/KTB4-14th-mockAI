# AUDIGO v2 매칭 AI 설계안 — 점수 기반 추천

최초 작성: 2026-10-10  
수정: 2026-10-11  
범위: 최신 BE 요청 구조에 맞춘 규칙 기반 추천 API  
상태: BE가 후보 닉네임을 전달하고 AI가 `user_id`·`match_rate`만 반환하는 연동 계약.

## 1. 요약

AI는 BE가 전달한 **request와 candidates**를 받아 성별·여행 속도를 필터링하고, 테마 80%·속도 20%로 점수를 계산해 상위 최대 3명의 후보 ID와 점수를 반환한다.

- HTTP 경로는 기존 POST /api/ai/v1/matches/candidates를 유지한다.
- 요청 조건은 최상위가 아니라 request 내부에서 읽는다.
- 후보의 gender로 요청한 동행자 성별 조건을 검사한다.
- 여유롭게 ↔ 알차게 후보는 제외한다.
- 점수 내림차순 → 닉네임 오름차순 → 사용자 ID 오름차순으로 정렬한다.
- limit은 선택값으로 두고 생략 시 3명을 반환한다.
- AI 응답은 최종 후보의 `user_id`와 `match_rate`만 포함한다. 닉네임·프로필 이미지·테마·속도 등 화면용 정보 조립은 BE가 담당한다.
- 닉네임은 실제 공급값을 정렬에 사용한다. AI가 생성하거나 사용자 DB에서 조회하지 않는다.
- 예산은 요청 검증만 하고 필터·점수에는 사용하지 않는다. 제품에 이 지원 범위를 명시하도록 합의한다.
- 요청 상태·백그라운드 실행·결과 저장·취소·알림·채팅은 BE 범위다.
- LLM, 임베딩, 학습 모델, AI 전용 DB·작업 상태 API는 추가하지 않는다.

BE 후보 DTO에는 동점 정렬에 필요한 `nickname`이 필요하다. `profile_image_url`은 AI 응답에 사용하지 않으므로 AI 계약상 필수값이 아니다.

## 2. 기존 설계에서 변경한 내용

| 항목 | 이전 설계 | 수정 설계 |
|---|---|---|
| 요청 조건 위치 | 최상위 | request 내부 |
| 성별 조건 | BE 필터 담당으로 합의 | AI가 실제 후보 gender로 필터 |
| 후보 nickname | 필수 | 동점 정렬에 사용 |
| 후보 이미지 | 전달하지 않음 | AI 점수 응답에는 사용하지 않음 |
| limit | 기본 3, 선택값 | 유지. 현재 BE가 생략하므로 기본 3 적용 |
| 응답 | 요청 ID·버전·ID·점수 | candidates/count 안에 user_id·match_rate만 반환 |
| 계산 버전 | 응답 ranking_version | 내부 로그의 ranking_version |
| 예산 | AI 입력에서 제외 | request에 받아 검증하되 비교에는 미사용 |
| 테마·속도 계산 | 80:20 | 유지 |
| 속도 제외·동점 | 정반대 제외, 닉네임순 | 유지 |

필터나 모델 고도화 범위를 늘리지 않는다. 실제 후보 성별이 BE에 추가되어 성별 조건 검사만 AI가 맡는다.

## 3. 최신 BE에서 확인한 계약

확인 기준: BE development 커밋 d49474aea0b1319e864ba98e09315f74c6ecd933.

현재 요청 예시:

```json
{
  "request": {
    "matching_request_id": 10,
    "user_id": 1,
    "preferred_companion_gender": "FEMALE",
    "theme": ["NATURE", "FOOD"],
    "pace": "RELAXED",
    "budget_min": 100000,
    "budget_max": 800000
  },
  "candidates": [
    {
      "user_id": 23,
      "nickname": "나나",
      "gender": "FEMALE",
      "theme": ["NATURE", "FOOD"],
      "pace": "RELAXED"
    }
  ]
}
```

BE가 현재 수행하는 후보 선정:
- matching_profiles.is_active=true
- 본인 제외
- gender·pace·theme이 완성된 프로필

현재 후보 선정에 계정 활성·현재 채팅 상대·성별·속도 필터·전달 상한은 적용되지 않았다. AI는 계정 상태나 채팅 정보를 전달받지 않으므로 이를 검증할 수 없다. 이 조건은 BE의 후보 공급 단계에서 처리해야 한다.

현재 BE는 AI가 반환한 사용자 ID와 점수로 닉네임·프로필 이미지·테마·속도 등 화면용 후보 응답을 조립한다.

근거:
- [BE PR #115](https://github.com/100-hours-a-week/KTB4-14th-BE/pull/115)
- [BE PR #114](https://github.com/100-hours-a-week/KTB4-14th-BE/pull/114)
- [AI 요청 DTO](https://github.com/100-hours-a-week/KTB4-14th-BE/blob/d49474aea0b1319e864ba98e09315f74c6ecd933/src/main/java/com/audigo/domain/matching/dto/MatchingCandidateAiRequest.java)
- [후보 조회 서비스](https://github.com/100-hours-a-week/KTB4-14th-BE/blob/d49474aea0b1319e864ba98e09315f74c6ecd933/src/main/java/com/audigo/domain/matching/service/MatchingCandidateService.java)
- [후보 Repository](https://github.com/100-hours-a-week/KTB4-14th-BE/blob/d49474aea0b1319e864ba98e09315f74c6ecd933/src/main/java/com/audigo/domain/matching/repository/MatchingProfileRepository.java)
- [연동 문서](https://github.com/100-hours-a-week/KTB4-14th-BE/blob/d49474aea0b1319e864ba98e09315f74c6ecd933/docs/matching-ai-candidates-integration.md)

## 4. 역할 분담

| AI | BE |
|---|---|
| 서비스 인증·입력 검증 | 로그인 사용자·요청 관리 |
| 본인 방어적 제외 | 실제 공개·완성 프로필 공급 |
| 후보 gender로 성별 필터 | 후보 계정 활성·현재 채팅 상대 제외 |
| 여행 속도 허용 범위 필터 | 실제 닉네임 공급·화면용 이미지 조회 |
| 테마·속도 점수 계산 | 전달 상한·후보 공급 품질 관리 |
| 닉네임 동점 정렬·최대 3명 선택 | 응답 후보 검증·결과 보관 |
| 후보 ID와 점수 반환 | 상태·재시도·취소·알림·채팅 관리 |

여행 매칭 요청자의 조건과 후보의 마이페이지 기본 프로필을 비교한다. 후보가 매칭 요청을 직접 만들었을 필요는 없다.

## 5. HTTP 계약 — 수정안

### 경로와 인증

POST /api/ai/v1/matches/candidates

헤더:
- Content-Type: application/json
- Authorization: Bearer <서비스 간 토큰>

기존 AI 서버 인증·오류 응답 방식을 재사용한다. 실제 토큰 값은 문서·로그에 남기지 않는다.

### 구현 목표 요청

아래 nickname은 동점 정렬을 위해 필요하다. profile_image_url은 AI 점수 응답에 사용하지 않으며 생략 가능하다.

```json
{
  "request": {
    "matching_request_id": 10,
    "user_id": 1,
    "preferred_companion_gender": "FEMALE",
    "theme": ["NATURE", "FOOD"],
    "pace": "RELAXED",
    "budget_min": 100000,
    "budget_max": 800000
  },
  "candidates": [
    {
      "user_id": 23,
      "nickname": "나나",
      "profile_image_url": null,
      "gender": "FEMALE",
      "theme": ["NATURE", "FOOD", "REST"],
      "pace": "RELAXED"
    },
    {
      "user_id": 24,
      "nickname": "다다",
      "profile_image_url": null,
      "gender": "FEMALE",
      "theme": ["NATURE", "CULTURE"],
      "pace": "BALANCED"
    },
    {
      "user_id": 25,
      "nickname": "라라",
      "profile_image_url": null,
      "gender": "MALE",
      "theme": ["NATURE", "FOOD"],
      "pace": "RELAXED"
    }
  ]
}
```

limit은 생략 가능하다. 기본 3을 사용한다. 전달할 경우 최상위 limit에 정수 1~3만 허용한다.

### 입력 규칙

| 필드 | 규칙 |
|---|---|
| request | 필수 객체 |
| request.matching_request_id | 양의 정수 |
| request.user_id | 요청자 ID, 양의 정수 |
| request.preferred_companion_gender | ANY / FEMALE / MALE |
| request.theme | 테마 1~3개, 중복 없음 |
| request.pace | RELAXED / BALANCED / PACKED |
| request.budget_min·budget_max | 둘 다 null 또는 둘 다 정수. 0~3,000,000, min ≤ max |
| candidates | 필수 배열, 빈 배열 허용 |
| candidates[].user_id | 양의 정수, 후보 ID 중복 불가 |
| candidates[].nickname | 공백만으로 구성되지 않은 필수 문자열 |
| candidates[].profile_image_url | 선택 문자열 또는 null, 생략 가능. AI 응답에는 포함하지 않음 |
| candidates[].gender | FEMALE / MALE |
| candidates[].theme | 테마 1~4개, 중복 없음 |
| candidates[].pace | RELAXED / BALANCED / PACKED |
| limit | 선택, 기본 3, 정수 1~3 |

테마 enum: NATURE, FOOD, CULTURE, REST, SNS, ACTIVITY.

필수값 누락, null 성별, 중복 ID, 잘못된 enum은 422로 반환한다. 후보를 조용히 버리거나 닉네임을 임의 생성하지 않는다. 현재 BE가 보내는 nickname 없는 후보는 검증에서 실패하므로 최소 BE 변경 후 사용해야 한다.

닉네임은 원문 문자열로 정렬에만 사용한다. 공백 문자열 검증을 제외하고 임의 변환하지 않으며 응답에는 포함하지 않는다.

후보 배열 상한은 BE와 합의해 설정값으로 관리한다. 상한을 넘으면 422로 반환하고 임의로 목록을 자르지 않는다. BE가 제한 없이 전송하는 현재 상태에서 AI만 낮은 상한을 설정하면 연동이 실패할 수 있다.

### 성공 응답

```json
{
  "candidates": [
    {
      "user_id": 23,
      "match_rate": 100
    },
    {
      "user_id": 24,
      "match_rate": 50
    }
  ],
  "count": 2
}
```

- HTTP 200.
- 최상위 candidates/count를 유지한다. FE용 message/data 래퍼를 만들지 않는다.
- 요청 ID와 계산 버전은 이 응답에 추가하지 않고 내부 로그로 기록한다.
- 닉네임·이미지·테마·속도는 응답하지 않는다. 화면용 정보는 BE가 사용자 ID로 조회해 조립한다.
- 요청에서 전달받은 사용자 ID와 계산된 점수만 반환한다.
- match_rate는 정수 0~100이다.
- count는 실제 반환 배열 길이와 같다.
- 후보 25는 성별 조건 때문에 제외된다.
- 예산을 반영한 점수라는 의미로 표시하지 않는다.

### 후보 없음

```json
{
  "candidates": [],
  "count": 0
}
```

빈 입력 또는 필터 후 후보가 없으면 HTTP 200이다. 입력 오류·시스템 오류를 빈 목록으로 숨기지 않는다.

## 6. 필터와 계산 규칙

### 성별

- ANY: FEMALE·MALE 모두 허용.
- FEMALE: 후보 gender=FEMALE만 허용.
- MALE: 후보 gender=MALE만 허용.
- 성별은 필터이고 점수 가산 항목이 아니다.

### 본인

후보 user_id가 request.user_id와 같으면 제외한다. BE가 이미 본인을 제외하지만 AI에서도 방어적으로 적용한다.

### 여행 속도

| 요청 | 허용 후보 | 속도 점수 P |
|---|---|---|
| RELAXED | RELAXED, BALANCED | 동일 1, 인접 0.5 |
| BALANCED | RELAXED, BALANCED, PACKED | 동일 1, 인접 0.5 |
| PACKED | BALANCED, PACKED | 동일 1, 인접 0.5 |

RELAXED ↔ PACKED 조합은 점수 계산 전에 제외한다.

### 테마·최종 점수

T = 겹치는 테마 개수 / 요청 테마 개수  
match_rate = floor(100 × (0.8 × T + 0.2 × P) + 0.5)

후보의 추가 테마는 감점하지 않는다. 테마가 하나도 겹치지 않아도 필터를 통과했다면 계산 대상이다. 최소 추천 점수는 두지 않는다.

정렬:
1. 정수 match_rate 내림차순
2. nickname 오름차순
3. user_id 오름차순

Python 문자열 순서를 첫 버전 기준으로 제안한다. 혼합 문자 정렬을 DB와 동일하게 해야 한다면 별도로 기준을 통일한다.

점수 비중·반올림 방식은 피그마 확정값이 아닌 합의할 초기 계산 기준이다. 점수는 취향 유사도이며 여행 성공 확률은 아니다.

### 예산

후보 기본 프로필에는 예산이 없으므로 예산 적합도를 계산할 수 없다. 현재 BE가 전달하는 budget_min·budget_max는 구조·범위만 검증한다.

이 첫 버전은 성별 필터와 테마·속도 추천을 지원한다. 예산이 추천에 적용되지 않는다는 점을 FE·제품 담당에게 명시하고 합의한다. 비용 선택 UI를 그대로 제공할지는 제품 범위 결정이며, AI가 근거 없는 후보 예산을 생성해서 맞추지 않는다.

## 7. 코드 구조와 처리

| 구성 | 역할 |
|---|---|
| API router | 인증, 입력 검증, 서비스 호출, HTTP 응답 |
| schemas | 중첩 RequestPayload·CandidateProfile·전체 요청·점수 응답 |
| service | 성별·본인·속도 필터, 계산 호출, 정렬·limit |
| scoring | 테마·속도 점수 계산 순수 함수 |
| response mapper | 선택된 입력 후보를 카드 형식으로 복사 |
| tests | 규칙·계약·오류 검증 |

권장 호출 형태:

```python
recommend(
    request_conditions=payload.request,
    candidates=payload.candidates,
    limit=payload.limit,
)
```

기존 AI 서버에 API를 추가하고 점수 함수는 HTTP·DB에 의존하지 않게 유지한다.

입력 검증 → 성별·본인·속도 필터 → 점수 계산 → 정렬 → 상위 limit → user_id·match_rate 응답.

AI는 한 번의 HTTP 요청에 결과를 바로 반환한다. 상태·단계 진행 이벤트·SSE·취소 API를 만들지 않는다. BE가 호출 전후로 진행 상태를 변경하고 결과를 저장한다.

## 8. 오류·로그

| 상황 | 처리 |
|---|---|
| 정상 추천·후보 없음 | 200 |
| 요청 구조 오류·닉네임 누락·잘못된 enum·예산 범위·limit | 422 |
| 인증 실패 | 기존 AI 인증 규약, 기본 제안 401 |
| 내부 계산 오류 | 500 |

기존 AI 오류 body 형식을 재사용한다. AI 내부 재시도는 추가하지 않는다.

최신 BE Client는 AI 4xx·5xx를 external_api_error로 처리한다. 422가 FE로 그대로 전달된다고 가정하지 않는다.

로그: request.matching_request_id, 입력 후보 수, 필터 후 후보 수, 반환 수, 처리 시간, ranking_version=theme-pace-v1, 오류 종류. 토큰·전체 후보 개인정보는 기록하지 않는다.

## 9. 이미 구현한 AI에서 바꿀 부분

1. 내 조건을 최상위 필드 대신 payload.request에서 읽도록 스키마·라우터 수정.
2. 후보에 gender를 추가하고 preferred_companion_gender 필터 구현.
3. 후보 profile_image_url은 선택값으로 허용하되 응답에는 포함하지 않는다.
4. limit은 기존 기본 3을 유지하여 BE의 생략 허용.
5. AI 응답은 ID·점수만 반환하고, BE가 화면용 정보를 조립한다.
6. 응답의 matching_request_id·ranking_version은 제거하고 로그로 유지.
7. 예산 입력 검증 추가. 예산 비교는 구현하지 않음.
8. 요청 fixture·응답 fixture·API 테스트 변경.
9. 점수 계산, 속도 필터, 동점 정렬은 기존 구현 유지.

실제 AI 구현 코드는 이번 문서 수정에서 검토하거나 변경하지 않았다.

## 10. 최소 테스트와 남은 합의

테스트:
- 중첩 request 입력과 ID·점수 응답.
- ANY는 두 성별 허용, FEMALE·MALE은 해당 성별만 허용.
- 테마 모두 일치·속도 동일 100, 인접 90.
- 요청 테마 절반 일치·동일 속도 60, 인접 50.
- RELAXED ↔ PACKED 제외, BALANCED는 모든 단계 허용.
- 동점 nickname순, 닉네임도 같으면 ID순.
- limit 생략은 최대 3명, limit=1은 최대 1명.
- 본인 제외·입력 후보 ID만 반환·메타데이터 원문 복사.
- 후보 없음은 200, 필수 nickname 누락은 422.
- 예산 양쪽 null 허용, 한쪽 null·역전·범위 초과는 422.
- 같은 입력에는 같은 결과, count와 실제 배열 길이 일치.
- BE 실제 JSON·인증으로 HTTP 연동 확인.

남은 BE 최소 변경:
- 후보 DTO에 nickname 추가.
- 후보 DTO의 profile_image_url은 선택값이며 AI 응답에는 필요하지 않다.
- 공개된 후보라도 비활성 계정이나 현재 채팅 상대라면 제외.
- 후보 상한 합의 및 AI 응답 검증.

후보 상태 관리·결과 보관 등 전체 UX 변경은 AI 계산 계약과 별도로 BE가 진행한다.

완료 기준: **최신 BE 요청 구조와 최소 메타데이터 확장을 받아 성별·속도 조건을 만족하는 실제 후보 카드 최대 3명을 반환하고, BE가 결과를 파싱하는 것.**

