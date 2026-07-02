# Nocturne MVP 상세 기획서

> agent가 매일 밤 선택한 Notion 페이지와 하위 페이지를 점검해 사실 오류/누락/모순을 찾아내고 사용자가 승인한 제안만 원문에 부분 반영하는 web application MVP

MVP 설명서 (Minimum Viable Product Specification)  
작성일: 2026-06-04 / 개정일: 2026-06-30 / 버전: v0.2 (MVP)

---

## 1. 제품 개요

### 1.1 한 줄 정의

Nocturne MVP는 Notion 사용자가 연결한 페이지를 매일 밤 자동 점검한다. 수정 제안은 Notion 수정함에 모아 두고 Slack 또는 이메일로 아침 알림을 보낸 뒤, 사용자가 승인한 항목만 agent가 원문에 반영한다.

### 1.2 목표

- 사용자가 선택한 Notion 페이지와 하위 페이지의 품질을 매일 자동 점검한다.
- 최근 수정된 페이지만 증분 검사해 API 호출량과 비용을 줄인다.
- 사실 오류, 누락, 모순을 탐지하고 근거와 위치가 있는 수정 제안을 만든다.
- 원본 페이지는 사용자가 승인한 제안에 한해서만 수정한다.
- Slack webhook 또는 이메일 알림으로 매일 결과를 알려준다.
- 사용자가 직접 연결한 OpenRouter API 키로 LLM 비용을 부담하는 BYOK 구조를 제공한다.
- Docker 단일 컨테이너로 패키징하고 Coolify 서버에 배포 가능하게 만든다.

### 1.3 MVP 범위

포함한다:

- Web application으로 제공하는 온보딩 및 설정 화면
- Notion OAuth 연결
- OpenRouter API 키 등록
- Slack webhook URL 등록
- 이메일 수신 주소 등록
- 점검할 Notion 페이지/데이터베이스 선택
- 선택한 페이지의 하위 페이지 기본 포함
- 매일 밤 agent harness로 실행하는 점검
- 전체 웹 검색으로 확인하는 사실 검증
- Notion 수정함 데이터베이스 자동 생성
- Slack 또는 이메일 아침 알림
- 승인된 제안만 원문에 부분 반영
- SQLite 로컬 데이터 저장
- Docker 단일 컨테이너 + Coolify 배포

포함하지 않는다:

- Obsidian/Logseq 연동
- 지식 그래프 공백 탐지
- 외부 자료로 신규 노트 자동 생성
- 사용자의 OpenRouter 모델 직접 선택
- 승인 없는 자동 원문 수정
- Slack OAuth 앱 설치
- 사용자 SMTP 직접 연결
- 다중 컨테이너 분리 배포

### 1.4 확정된 의사결정

| 항목 | 결정 |
|---|---|
| Hermes agent 구현체 | 별도 구현체 없음. 구현체 복사가 아니라 agent 구조 패턴만 참고 |
| Agent 범위 | 최근 수정 페이지 검사부터 승인 항목 원문 반영까지 맡음. 단, 원문 수정은 승인 항목만 |
| 원문 수정 경계 | 사용자가 Notion 수정함에서 승인한 항목만 반영 |
| OpenRouter 모델 | 서비스 기본 모델 사용. 사용자는 API 키만 등록 |
| LLM 비용 | 사용자의 OpenRouter API 키로 과금 |
| 웹 사실 확인 | 전체 웹 검색 허용 |
| 최근 수정 기준 | `last_edited_time > 마지막 성공 실행 시각` |
| Nocturne 수정으로 인한 재검사 | Nocturne이 수정한 페이지는 다음 실행의 최근 수정 대상에서 제외 |
| 하위 페이지 | 선택한 페이지의 하위 페이지를 기본 포함 |
| Slack | webhook URL 방식 |
| 이메일 | 사용자가 수신 주소만 등록, 서비스가 발송 |
| 0건 알림 | 발견 항목이 없어도 "문제 없음" 알림 발송 |
| 수정함 DB | 사용자 워크스페이스에 자동 생성 |
| 배포 | 같은 Docker 컨테이너에서 web app, scheduler, worker 실행 |
| DB | SQLite |
| Web app 화면 | 온보딩/연결, 점검 대상 선택, 알림 설정, 실행 로그, 계정/API 키 관리 |

---

## 2. 핵심 사용 흐름

```
[1] 사용자가 web application에 접속
       │
       ▼
[2] Notion, OpenRouter, Slack/이메일 연결
       │  Notion OAuth + OpenRouter API 키 + Slack webhook 또는 이메일 주소
       ▼
[3] 점검할 Notion 페이지/데이터베이스 선택
       │  선택 대상의 하위 페이지는 기본 포함
       ▼
[4] 매일 밤 agent harness 실행
       │  마지막 성공 실행 이후 수정된 페이지만 증분 수집
       ▼
[5] agent가 오류/누락/모순 탐지
       │  Notion 블록 분석 + 전체 웹 검색 + LLM 판단 + 결과 검증
       ▼
[6] agent가 Notion 수정함에 제안 저장
       │  원본 위치, 문제 유형, 원문, 제안문, 근거, 확신도, 상태=대기
       ▼
[7] agent가 연결 채널로 아침 알림 발송
       │  Slack webhook 또는 이메일, 0건이어도 문제 없음 알림
       ▼
[8] 사용자가 Notion 수정함에서 항목 승인/거절
       │
       ▼
[9] agent가 승인된 항목만 원문에 부분 반영
       │  대상 블록의 해당 문장만 교체하고 상태=반영됨
       ▼
[10] 다음 실행에서 Nocturne이 수정한 페이지는 재검사 루프에서 제외
```

핵심 원칙은 "agent가 실행하되 사용자가 승인하기 전에는 원본을 수정하지 않는다"이다. agent는 수집, 분석, 제안 저장, 알림, 승인 항목 반영까지 맡지만 원본 Notion 페이지 쓰기는 승인 상태 레코드에만 허용된다.

---

## 3. 제품 화면

### 3.1 온보딩/연결 화면

사용자가 처음 접속하면 필요한 연결 상태를 순서대로 보여준다.

필수 연결:

- Notion OAuth
- OpenRouter API 키
- 최소 1개 알림 채널

알림 채널:

- Slack webhook URL
- 이메일 수신 주소

동작:

- Notion 연결 버튼을 누르면 OAuth 승인 화면으로 이동한다.
- OpenRouter API 키 입력 시 서버는 키 유효성을 확인한다.
- Slack webhook URL 입력 시 테스트 메시지를 발송한다.
- 이메일 주소 입력 시 인증 코드를 보내고 사용자가 코드를 입력하면 연결을 완료한다.
- 연결 해제 시 관련 토큰, webhook URL, 이메일 주소, 캐시를 삭제하거나 비활성화한다.

### 3.2 점검 대상 선택 화면

사용자는 Notion에서 점검할 페이지 또는 데이터베이스를 선택한다.

규칙:

- 선택한 페이지의 하위 페이지는 기본 포함한다.
- 선택한 데이터베이스 안의 페이지도 점검 대상에 포함한다.
- 사용자가 명시적으로 제외한 페이지는 하위에 있어도 점검하지 않는다.
- 선택 목록은 추가와 삭제를 지원한다.
- 처음 선택 직후에는 전체 선택 범위를 기준선으로 1회 검사한다.

표시 정보:

- 페이지/데이터베이스 제목
- Notion URL
- 하위 페이지 포함 여부
- 제외 페이지 수
- 마지막 점검 시각
- 마지막 점검 결과

### 3.3 알림 설정 화면

사용자는 아침 알림 채널과 시간을 설정한다.

설정 항목:

- 기본 알림 채널: Slack, 이메일, 둘 다
- 알림 발송 시각
- 타임존
- 0건 알림 발송 여부: 기본 ON

MVP 기본값:

- 발견 항목이 0개여도 "문제 없음" 알림을 보낸다.
- Slack과 이메일이 모두 연결된 경우 둘 다 보낼 수 있다.
- 둘 중 하나만 연결된 경우 해당 채널로만 보낸다.

### 3.4 실행 로그 화면

사용자는 agent 실행 내역을 확인한다.

표시 정보:

- run_id
- 실행 시작/종료 시각
- 상태: 대기, 실행 중, 성공, 부분 성공, 실패
- 스캔한 페이지 수
- 변경된 페이지 수
- 생성된 제안 수
- 오류/누락/모순 건수
- 보류된 항목 수
- 알림 발송 결과
- 승인 반영 결과
- 실패 원인

### 3.5 계정/API 키 관리 화면

사용자는 연결된 계정과 키를 관리한다.

관리 항목:

- Notion 연결 상태
- OpenRouter API 키 등록/교체/삭제
- Slack webhook URL 등록/교체/삭제
- 이메일 수신 주소 등록/변경/삭제
- 데이터 삭제 요청

보안 표시:

- OpenRouter API 키와 webhook URL은 전체 값을 다시 보여주지 않는다.
- 마지막 4자리 또는 연결 상태만 표시한다.
- 키 교체 시 기존 키는 즉시 폐기한다.

---

## 4. Agent Harness 상세

### 4.1 역할

agent harness는 야간 점검과 승인 반영을 맡는 실행 엔진이다. 별도 Hermes agent 구현체는 없으므로 Hermes식 agent 구조인 계획, 도구 호출, 검증, 기록, 재시도 패턴을 참고해 구현한다.

agent가 맡는 단계:

- 최근 수정 페이지 수집
- Notion 블록 텍스트화
- 사실 오류/누락/모순 탐지
- 필요한 경우 전체 웹 검색
- LLM 판단 및 수정 제안 생성
- 제안 품질 검증
- Notion 수정함 저장
- Slack/이메일 알림 발송
- 승인된 항목 원문 반영
- 실행 로그 기록

### 4.2 구성 요소

| 구성 요소 | 책임 |
|---|---|
| Scheduler | 매일 밤 정해진 시각에 run 생성 |
| Run Manager | run_id 발급, 상태 전이, 실행 로그 관리 |
| Planner | 이번 실행에서 해야 할 작업 목록 생성 |
| Notion Tool Adapter | 페이지 검색, 블록 조회, 수정함 생성, 제안 저장, 블록 수정 |
| Web Search Tool Adapter | 전체 웹 검색 실행 및 출처 URL 수집 |
| LLM Tool Adapter | OpenRouter API로 서비스 기본 모델 호출 |
| Proposal Validator | 제안 중복 제거, 원문 위치 재검증, 최소 수정 원칙 검사 |
| Notification Adapter | Slack webhook 및 이메일 발송 |
| Approval Applier | 승인된 수정함 항목만 원문에 부분 반영 |
| Audit Logger | 모든 주요 판단과 외부 호출 결과 기록 |

### 4.3 실행 상태

run 상태:

- `pending`: 실행 대기
- `running`: 실행 중
- `success`: 모든 단계 성공
- `partial_success`: 일부 페이지/알림 실패, 핵심 결과는 저장됨
- `failed`: 실행 실패

proposal 상태:

- `대기`: 사용자가 검토해야 하는 제안
- `승인`: 사용자가 반영을 허용한 제안
- `거절`: 사용자가 반영하지 않기로 한 제안
- `보류`: 확신도가 낮거나 검증이 부족해 자동 제안에서 제외된 항목
- `반영됨`: 원문 반영 완료
- `반영 실패`: 승인되었지만 원문 위치 불일치 등으로 반영 실패

### 4.4 Agent 실행 원칙

- 원본 Notion 페이지 수정은 `승인` 상태의 제안에만 허용한다.
- LLM 출력은 바로 신뢰하지 않고 validator를 통과해야 한다.
- 제안은 항상 원본 블록 ID와 원문 문장을 포함해야 한다.
- 원문 문장이 현재 블록에 더 이상 존재하지 않으면 반영하지 않고 `반영 실패`로 표시한다.
- 동일한 원문/제안/블록 조합은 중복 생성하지 않는다.
- Nocturne이 직접 수정한 페이지는 다음 야간 실행에서 최근 수정 페이지로 다시 잡히지 않도록 제외한다.
- 실패한 페이지가 있어도 나머지 페이지는 계속 진행한다.
- 외부 API 호출은 재시도와 백오프를 적용한다.

---

## 5. 야간 점검 상세

### 5.1 실행 트리거

스케줄러가 매일 밤 사용자의 타임존 기준 지정 시각에 agent run을 생성한다. MVP에서는 같은 Docker 컨테이너 안에서 web app과 scheduler/worker가 함께 실행된다.

기본 실행 순서:

1. 사용자별 활성 설정 조회
2. Notion, OpenRouter, 알림 채널 연결 상태 확인
3. run_id 생성
4. 점검 대상 목록 확장
5. 최근 수정 페이지 필터링
6. 페이지별 분석 실행
7. 수정함에 제안 저장
8. 아침 알림 예약 또는 즉시 발송
9. run 상태 저장

### 5.2 최근 수정 페이지 기준

최근 수정 페이지는 다음 조건으로 판단한다.

```
page.last_edited_time > user.last_successful_scan_at
```

예외:

- `last_edited_time`이 Nocturne의 승인 반영 때문에 갱신된 경우 다음 점검 대상에서 제외한다.
- 제외 여부 판단을 위해 Nocturne이 수정한 페이지 ID와 반영 시각을 로컬 DB에 기록한다.
- 처음 연결한 직후에는 `last_successful_scan_at`이 없으므로 선택 범위 전체를 기준선으로 검사한다.

### 5.3 하위 페이지 확장

선택한 페이지는 하위 페이지까지 기본 포함한다.

확장 규칙:

- 페이지 블록을 순회하며 child_page 블록을 찾는다.
- 데이터베이스가 포함되어 있으면 해당 데이터베이스의 페이지도 포함한다.
- 순환 참조와 중복 페이지는 page_id 기준으로 제거한다.
- 접근 권한이 없는 하위 페이지는 로그에 남기고 건너뛴다.

### 5.4 Notion 블록 텍스트화

분석 전 Notion 블록을 LLM이 읽기 쉬운 구조로 변환한다.

포함 정보:

- page_id
- page_title
- page_url
- block_id
- block_type
- plain_text
- rich_text 원본
- parent_block_id
- heading 계층
- last_edited_time

제외 또는 제한:

- 이미지, 파일, 임베드는 MVP에서 텍스트 캡션이나 URL만 사용한다.
- 테이블은 가능한 경우 행 단위 텍스트로 펼친다.
- 너무 긴 페이지는 섹션 단위로 나누어 다룬다.

---

## 6. 분석 및 제안 생성

### 6.1 분석 유형

agent는 세 가지 문제 유형을 찾는다.

| 유형 | 설명 | 예시 |
|---|---|---|
| 사실 오류 | 날짜, 수치, 이름, 인과 관계 등 검증 가능한 주장이 틀렸거나 의심되는 경우 | "2025년에 출시"라고 했지만 실제 출시일이 다른 경우 |
| 누락 | 문맥상 필요한 정보가 빠져 있거나 TODO/빈 섹션이 남아 있는 경우 | 결론 섹션이 비어 있거나 참조 대상 설명이 없는 경우 |
| 모순 | 같은 페이지 또는 선택 범위 안에서 서로 충돌하는 진술 | 한 곳에서는 가격이 10달러, 다른 곳에서는 20달러라고 쓰인 경우 |

### 6.2 전체 웹 검색

사실 오류 검증에는 전체 웹 검색을 쓸 수 있다.

검색 원칙:

- 검증 가능한 주장만 검색한다.
- 검색 결과 URL, 제목, 요약, 접근 시각을 저장한다.
- 출처가 불명확하거나 신뢰하기 어려우면 확신도를 낮춘다.
- 검색으로 확인할 수 없는 항목은 단정하지 않는다.
- 웹 검색이 실패해도 누락/모순 분석은 계속한다.

MVP는 web search provider를 adapter 뒤에 둔다. 실제 provider는 환경변수로 고르고 agent 내부에서는 `search_web(query)` 인터페이스만 사용한다.

### 6.3 LLM 호출

LLM은 OpenRouter API로 호출한다.

규칙:

- 사용자는 OpenRouter API 키만 등록한다.
- 모델은 서비스 기본값을 사용한다.
- 사용자가 모델을 직접 선택하는 기능은 MVP에서 제외한다.
- 비용은 사용자의 OpenRouter 계정에 과금된다.
- 키가 없거나 유효하지 않으면 분석 run을 시작하지 않고 연결 오류를 표시한다.

LLM 입력:

- 페이지/섹션 텍스트
- 블록 ID와 위치 정보
- 이전 제안 중복 방지 정보
- 웹 검색 결과
- 출력 JSON 스키마

LLM 출력:

- 문제 유형
- 원본 페이지 ID
- 블록 ID
- 원문 문장
- 제안 문장
- 근거
- 출처 URL
- 확신도
- 반영 방식: 교체 또는 추가

### 6.4 제안 검증

LLM 출력은 저장 전에 validator를 통과해야 한다.

검증 항목:

- block_id가 실제 존재하는가
- 원문 문장이 해당 블록에 존재하는가
- 제안 문장이 원문보다 과도하게 넓은 범위를 바꾸지 않는가
- 동일 제안이 이미 수정함에 존재하지 않는가
- 사실 오류 제안에 근거 URL 또는 명확한 내부 근거가 있는가
- 확신도가 최소 기준 이상인가

기본 동작:

- 검증 통과: 수정함에 `대기` 상태로 저장
- 확신도 낮음: `보류` 상태로 저장하거나 알림 요약에만 포함
- 위치 불일치: 저장하지 않고 run 로그에 기록
- 중복: 새 레코드를 만들지 않음

---

## 7. Notion 수정함

### 7.1 생성 방식

Nocturne은 사용자의 Notion 워크스페이스에 전용 데이터베이스 `Nocturne 수정함`을 자동 생성한다.

생성 시점:

- Notion 연결 후 최초 점검 대상 설정 완료 시
- 이미 생성된 수정함이 있으면 재사용
- 삭제되었거나 접근 권한이 사라졌으면 재생성 안내

### 7.2 데이터 모델

| 속성 | 타입 | 설명 |
|---|---|---|
| 제목 | title | 제안 요약 |
| 원본 페이지 | URL | 제안이 가리키는 Notion 페이지 |
| 원본 페이지 ID | rich_text | Notion page_id |
| 블록 ID | rich_text | 수정 대상 block_id |
| 문제 유형 | select | 오류 / 누락 / 모순 |
| 원문 문장 | rich_text | 현재 문서에 있는 문장 |
| 제안 문장 | rich_text | 교체 또는 추가할 문장 |
| 반영 방식 | select | 교체 / 추가 |
| 근거 | rich_text | 판단 근거 요약 |
| 출처 URL | URL | 웹 검색 또는 참고 출처 |
| 확신도 | number | 0~1 |
| 상태 | select | 대기 / 승인 / 거절 / 보류 / 반영됨 / 반영 실패 |
| 실행 ID | rich_text | agent run_id |
| 생성 시각 | date | 제안 생성 시점 |
| 반영 시각 | date | 원문 반영 완료 시점 |
| 반영 오류 | rich_text | 실패 시 오류 메시지 |

### 7.3 사용자 검토 방식

사용자는 Notion 수정함에서 각 제안의 상태를 바꾼다.

- `승인`: agent가 다음 승인 반영 실행에서 원문에 반영
- `거절`: 반영하지 않으며 향후 오탐 방지 신호로 사용
- `대기`: 아직 검토하지 않은 상태
- `보류`: Nocturne이 낮은 확신도로 표시한 상태

MVP에서는 Notion 수정함 자체가 리뷰 UI다. web application 안에 별도 리뷰 화면을 만들지 않는다.

---

## 8. 승인 항목 반영

### 8.1 반영 트리거

agent는 승인된 항목을 주기적으로 확인하거나, 사용자가 web application에서 "승인 항목 반영"을 누르면 실행한다. MVP 기본 구현은 야간 run의 마지막 단계와 수동 실행 버튼을 모두 지원한다.

야간 run에서는 이전 실행에서 생성되어 사용자가 이미 승인한 항목을 먼저 반영한 뒤, 새로운 최근 수정 페이지 검사를 시작한다. 이렇게 하면 사용자가 아침 알림을 보고 낮에 승인한 제안이 다음 야간 실행에서 자동 반영된다.

### 8.2 반영 규칙

agent는 `상태=승인`인 수정함 레코드만 다룬다.

교체 방식:

- block_id로 현재 블록을 다시 조회한다.
- rich_text에서 `원문 문장`이 아직 존재하는지 확인한다.
- 존재하면 해당 문장만 `제안 문장`으로 교체한다.
- 나머지 rich_text와 블록 구조는 보존한다.

추가 방식:

- 누락 제안의 경우 지정된 블록 뒤 또는 섹션 끝에 새 문장을 추가한다.
- 추가 위치가 불명확하면 반영하지 않고 `반영 실패`로 표시한다.

반영 후:

- 수정함 상태를 `반영됨`으로 변경한다.
- 반영 시각을 기록한다.
- 로컬 DB에 Nocturne 수정 이력을 저장한다.
- 해당 페이지는 다음 최근 수정 검사에서 제외한다.

### 8.3 실패 처리

반영 실패 조건:

- 원본 블록이 삭제됨
- 원문 문장이 현재 블록에 없음
- Notion API 쓰기 권한 없음
- 제안 문장이 비어 있음
- 반영 방식이 불명확함

실패 시:

- 원본 페이지는 수정하지 않는다.
- 수정함 상태를 `반영 실패`로 변경한다.
- 반영 오류 필드에 이유를 기록한다.
- 알림 요약에 실패 건수를 포함한다.

---

## 9. 알림

### 9.1 채널

MVP 알림 채널은 두 가지다.

- Slack webhook
- 이메일

사용자는 둘 중 하나만 연결해도 되고 둘 다 연결해도 된다.

### 9.2 Slack

Slack은 webhook URL 방식으로 구현한다.

설정:

- 사용자가 Slack incoming webhook URL을 입력한다.
- 저장 전 테스트 메시지를 보낸다.
- 실패하면 연결을 완료하지 않는다.

메시지 내용:

- 날짜
- 점검한 페이지 수
- 변경된 페이지 수
- 발견된 제안 수
- 오류/누락/모순별 건수
- 보류 항목 수
- 반영 성공/실패 건수
- 수정함 링크
- 0건이면 "오늘은 문제 없음" 메시지

### 9.3 이메일

이메일은 사용자가 수신 주소만 등록하고 발송은 서비스가 맡는다.

설정:

- 사용자가 이메일 주소를 입력한다.
- 서비스가 인증 코드를 발송한다.
- 사용자가 코드를 입력하면 연결을 완료한다.

발송:

- SendGrid, Postmark 또는 SMTP 중 운영자가 설정한 provider 사용
- 사용자 SMTP 직접 연결은 MVP 범위에서 제외
- 0건이어도 문제 없음 알림 발송

### 9.4 알림 실패

알림 발송 실패는 run 전체 실패로 보지 않는다.

- 제안 저장까지 성공했으면 run은 `partial_success`로 기록한다.
- 실패한 채널과 오류 메시지를 로그에 남긴다.
- 다른 연결 채널이 있으면 계속 발송한다.

---

## 10. 데이터 저장

### 10.1 SQLite 사용

MVP는 SQLite를 사용한다. Coolify 배포 시 Docker volume을 연결해 DB 파일을 보존한다.

저장 위치 예:

```
/app/data/nocturne.sqlite3
```

### 10.2 주요 테이블

`users`

- id
- email
- created_at
- timezone

`connections`

- id
- user_id
- notion_access_token_encrypted
- notion_workspace_id
- openrouter_api_key_encrypted
- slack_webhook_url_encrypted
- notification_email
- notification_email_verified
- created_at
- updated_at

`scan_targets`

- id
- user_id
- notion_object_id
- notion_object_type: page 또는 database
- title
- url
- include_children: 기본 true
- excluded_page_ids
- active
- created_at

`runs`

- id
- user_id
- run_id
- status
- started_at
- finished_at
- last_successful_scan_at_before_run
- scanned_page_count
- changed_page_count
- proposal_count
- error_count
- omission_count
- contradiction_count
- held_count
- applied_count
- apply_failed_count
- notification_status
- error_message

`proposals_cache`

- id
- user_id
- run_id
- notion_proposal_page_id
- source_page_id
- block_id
- original_sentence_hash
- suggested_sentence_hash
- status
- created_at

`nocturne_edits`

- id
- user_id
- source_page_id
- block_id
- proposal_id
- applied_at
- before_text_hash
- after_text_hash

### 10.3 암호화 대상

다음 값은 DB에 평문으로 저장하지 않는다.

- Notion access token
- OpenRouter API key
- Slack webhook URL

암호화 키는 환경변수로 제공한다.

```
NOCTURNE_ENCRYPTION_KEY=...
```

---

## 11. 기술 스택

| 구분 | 선택 |
|---|---|
| 제품 형태 | Web application |
| 백엔드 | Python, FastAPI |
| 스케줄러 | APScheduler 또는 cron으로 구동하는 내부 스케줄러 |
| Agent | 자체 agent harness |
| LLM | OpenRouter API, 서비스 기본 모델 |
| Notion | Notion OAuth + Notion API |
| Slack | Incoming webhook |
| 이메일 | 운영자 설정 provider(SendGrid/Postmark/SMTP) |
| 웹 검색 | 전체 웹 검색 adapter |
| DB | SQLite |
| 배포 | Docker 단일 컨테이너 |
| 서버 운영 | Coolify |
| 설정 | 환경변수 + Coolify secrets |

### 11.1 Docker 프로세스 구성

MVP에서는 같은 컨테이너에서 web app, scheduler, worker를 함께 실행한다.

예시 프로세스:

- FastAPI web server
- background scheduler
- agent worker loop

운영 단순성을 위해 컨테이너는 하나로 유지한다. 단, 코드 구조는 추후 web/worker 분리가 가능하도록 모듈을 나눈다.

### 11.2 Coolify 배포

Coolify 배포 조건:

- Git repository 또는 Docker image 배포
- persistent volume 연결
- 환경변수 등록
- HTTPS 도메인 연결
- 컨테이너 재시작 정책 설정

필수 환경변수:

```
APP_URL=
DATABASE_URL=sqlite:////app/data/nocturne.sqlite3
NOCTURNE_ENCRYPTION_KEY=
NOTION_CLIENT_ID=
NOTION_CLIENT_SECRET=
NOTION_REDIRECT_URI=
OPENROUTER_DEFAULT_MODEL=
OPENROUTER_API_KEY=
OPENROUTER_WEB_SEARCH_ENABLED=
OPENROUTER_WEB_SEARCH_ENGINE=
OPENROUTER_WEB_SEARCH_MAX_RESULTS=
EMAIL_PROVIDER=
EMAIL_FROM=
EMAIL_API_KEY=
WEB_SEARCH_PROVIDER=
WEB_SEARCH_API_KEY=
```

OpenRouter API 키는 서버 환경변수로 관리하고, 사용자별 Notion 토큰과 알림 설정은 web application 설정으로 입력받아 암호화 저장한다.

---

## 12. API/모듈 설계

### 12.1 주요 백엔드 라우트

| Method | Path | 설명 |
|---|---|---|
| GET | `/` | web app 진입 |
| GET | `/auth/notion/start` | Notion OAuth 시작 |
| GET | `/auth/notion/callback` | Notion OAuth callback |
| POST | `/settings/openrouter` | OpenRouter API 키 저장/검증 |
| POST | `/settings/slack-webhook` | Slack webhook 저장/테스트 |
| POST | `/settings/email` | 이메일 인증 코드 발송 |
| POST | `/settings/email/verify` | 이메일 인증 완료 |
| GET | `/targets` | 점검 대상 목록 |
| POST | `/targets` | 점검 대상 추가 |
| DELETE | `/targets/{id}` | 점검 대상 비활성화 |
| GET | `/runs` | 실행 로그 목록 |
| POST | `/runs/manual` | 수동 점검 실행 |
| POST | `/apply-approved` | 승인 항목 수동 반영 |

### 12.2 내부 모듈

```
app/
  main.py
  config.py
  db/
  models/
  routes/
  services/
    notion_service.py
    openrouter_service.py
    web_search_service.py
    notification_service.py
    email_service.py
    slack_service.py
  agent/
    harness.py
    planner.py
    scanner.py
    analyzer.py
    validator.py
    proposal_writer.py
    approval_applier.py
    run_logger.py
  scheduler/
    scheduler.py
```

---

## 13. 프롬프트/출력 계약

### 13.1 LLM 출력 JSON 스키마

LLM은 반드시 JSON 배열로 제안 후보를 반환한다.

```json
[
  {
    "issue_type": "error",
    "source_page_id": "notion_page_id",
    "block_id": "notion_block_id",
    "original_sentence": "원문 문장",
    "suggested_sentence": "제안 문장",
    "apply_mode": "replace",
    "rationale": "왜 문제인지",
    "source_urls": ["https://example.com"],
    "confidence": 0.82
  }
]
```

허용값:

- `issue_type`: `error`, `omission`, `contradiction`
- `apply_mode`: `replace`, `append`
- `confidence`: 0 이상 1 이하

### 13.2 최소 수정 원칙

제안 문장은 원문의 의미를 필요한 만큼만 바꿔야 한다.

- 문체 전체를 리라이팅하지 않는다.
- 사용자의 주장이나 의도를 임의로 확장하지 않는다.
- 사실 확인이 필요한 항목은 근거 없이 단정하지 않는다.
- 누락 제안은 추가할 문장만 제안한다.
- 모순 제안은 어떤 문장을 기준으로 정정하는지 근거를 포함한다.

---

## 14. 보안과 개인정보

### 14.1 권한 최소화

- Notion 권한은 사용자가 승인한 워크스페이스와 페이지 범위로 제한한다.
- 선택하지 않은 페이지는 점검하지 않는다.
- 하위 페이지 포함은 선택한 페이지 아래로만 적용한다.

### 14.2 비밀정보 관리

- 사용자 OpenRouter API 키는 암호화 저장한다.
- Slack webhook URL은 암호화 저장한다.
- Notion token은 암호화 저장한다.
- 로그에는 API 키, token, webhook URL 원문을 남기지 않는다.

### 14.3 데이터 삭제

사용자가 연결 해제 또는 계정 삭제를 요청하면 다음 데이터를 삭제한다.

- Notion token
- OpenRouter API 키
- Slack webhook URL
- 이메일 주소
- scan targets
- run logs
- local proposal cache

Notion 워크스페이스 안에 생성된 수정함 DB는 사용자가 직접 삭제하도록 안내한다. MVP는 외부 워크스페이스 데이터 자동 삭제를 기본 동작으로 삼지 않는다.

---

## 15. 실패/예외 처리

### 15.1 Notion 오류

- 레이트 리밋: 백오프 후 재시도
- 권한 없음: 해당 페이지 건너뛰고 로그 기록
- 블록 삭제: 제안 저장 또는 반영 실패로 기록

### 15.2 OpenRouter 오류

- 키 없음: run 시작 불가
- 키 유효하지 않음: 설정 화면에서 오류 표시
- 호출 실패: 페이지 단위 재시도, 실패 시 run partial_success 또는 failed
- 비용/한도 초과: 사용자에게 연결 오류로 표시

### 15.3 웹 검색 오류

- 검색 실패는 전체 run 실패로 보지 않는다.
- 해당 사실 오류 제안의 확신도를 낮추거나 보류한다.
- 내부 누락/모순 분석은 계속 진행한다.

### 15.4 알림 오류

- Slack 실패 후 이메일이 연결된 경우 이메일 발송을 계속한다.
- 이메일 실패 후 Slack이 연결된 경우 Slack 발송을 계속한다.
- 모든 알림 실패 시 run은 partial_success

---

## 16. MVP 수용 기준

### 16.1 온보딩

- 사용자는 web application에서 Notion OAuth 연결을 완료한다.
- 사용자는 OpenRouter API 키를 등록하고 유효성 확인을 받는다.
- 사용자는 Slack webhook 또는 이메일 주소 중 하나 이상을 연결한다.

### 16.2 점검 대상

- 사용자는 Notion 페이지 또는 데이터베이스를 점검 대상으로 추가한다.
- 선택한 페이지의 하위 페이지가 기본 포함된다.
- 제외한 페이지는 점검되지 않는다.

### 16.3 Agent 점검

- 매일 밤 scheduler가 agent run을 생성한다.
- agent는 마지막 성공 실행 이후 수정된 페이지만 검사한다.
- agent는 Nocturne이 수정한 페이지를 다음 최근 수정 검사에서 제외한다.
- agent는 오류/누락/모순 제안을 생성한다.
- 사실 오류 검증에 전체 웹 검색을 쓸 수 있다.

### 16.4 수정함

- Nocturne은 사용자의 Notion 워크스페이스에 수정함 DB를 자동 생성한다.
- 제안은 원본 위치, 원문, 제안문, 근거, 확신도, 상태를 포함한다.
- 원본 페이지는 제안 저장 단계에서 수정되지 않는다.

### 16.5 알림

- Slack webhook 알림이 발송된다.
- 이메일 알림이 발송된다.
- 발견 항목이 0개여도 문제 없음 알림이 발송된다.
- 알림에는 수정함 링크가 포함된다.

### 16.6 승인 반영

- 사용자가 수정함에서 `승인`으로 바꾼 항목만 반영된다.
- `대기`, `거절`, `보류` 항목은 반영되지 않는다.
- 원문 문장이 현재 블록에 없으면 반영하지 않고 실패 상태를 기록한다.
- 반영 완료 후 수정함 상태가 `반영됨`으로 바뀐다.

### 16.7 배포

- 애플리케이션은 Docker 단일 컨테이너로 실행된다.
- SQLite DB는 Docker volume에 저장된다.
- Coolify에서 환경변수와 volume을 설정해 배포한다.

---

## 17. 구현 우선순위

1. FastAPI web application skeleton
2. SQLite schema 및 암호화 유틸리티
3. Notion OAuth 연결
4. OpenRouter API 키 저장/검증
5. Slack webhook 및 이메일 주소 연결
6. 점검 대상 선택 및 하위 페이지 확장
7. 수정함 DB 자동 생성
8. agent harness 기본 run 구조
9. 최근 수정 페이지 증분 수집
10. 블록 텍스트화
11. OpenRouter LLM 분석
12. 전체 웹 검색 adapter
13. proposal validator
14. 수정함 제안 저장
15. Slack/이메일 알림
16. 승인 항목 반영
17. 실행 로그 화면
18. Dockerfile 및 Coolify 배포 문서

---

## 18. Agent 구현자에게 주는 핵심 지시

- 이 MVP의 중심은 web app이 아니라 "승인 경계가 있는 agentic 문서 점검 루프"다.
- agent는 최근 수정 페이지 검사, 분석, 제안 저장, 알림, 승인 항목 원문 반영까지 맡지만 원문 수정은 승인 항목에만 제한한다.
- Hermes 구현체는 없으므로 특정 코드를 찾지 말고 계획-도구-검증-기록 구조를 자체 구현한다.
- OpenRouter는 사용자의 API 키를 사용하고 모델은 서비스 기본값만 쓴다.
- Slack은 webhook으로만 구현한다.
- 이메일은 사용자가 주소만 등록하고 서비스가 발송한다.
- Notion 수정함은 자동 생성한다.
- Docker는 단일 컨테이너로 만들고 SQLite는 volume에 둔다.
- 0건이어도 "문제 없음" 알림을 보내야 한다.
- Nocturne의 승인 반영 때문에 수정된 페이지는 다음 최근 수정 검사에서 제외해야 한다.
