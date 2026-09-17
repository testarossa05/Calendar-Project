# 설치 및 설정 가이드

## 0. 결론 먼저

| 항목 | 권장안 | 이유 |
|---|---|---|
| 통합 방식 | 통합 엔진 + 단일 캘린더 | Apple 개발자 계정·회사 IT 승인 불필요 |
| 회사 일정 수집 | Outlook "캘린더 게시" ICS | Azure AD 앱 등록 불필요 |
| 아이폰 연결 | **Google 전용 캘린더** (1순위) | 비공개 유지, 동기화 빠름 |
| 아이폰 연결 | 구독 `.ics` (2순위) | 무료·간단하나 URL이 공개됨 |
| 업무 일정 기밀 | `privacy: busy` 적용 검토 | 회의 제목 외부 유출 차단 |

---

## 1. 사전 준비

```bash
git clone https://github.com/testarossa05/PTKR-Mechanical-Sales-Ontology-Hub.git
cd PTKR-Mechanical-Sales-Ontology-Hub
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
export PYTHONPATH=src
```

`config.yaml`은 `.gitignore`에 포함되어 있다. 토큰은 이 파일에 직접 적지 말고
`${ENV_VAR}` 형태로만 참조한다.

---

## 2. 소스별 설정

### 2.1 Microsoft 365 / Teams — 관리자 승인 불필요 경로

Teams 회의는 실제로는 Outlook 캘린더에 저장된다. 따라서 Outlook 캘린더만 읽으면 된다.

1. Outlook 웹(outlook.office.com) 접속
2. 설정(⚙) → 캘린더 → **공유 캘린더**
3. **캘린더 게시** 섹션에서 대상 캘린더 선택
4. 권한을 **"모든 세부 정보를 볼 수 있음"** 으로 지정 → **게시**
5. 생성된 두 링크 중 **ICS 링크**를 복사 (HTML 링크 아님)

```bash
export OUTLOOK_ICS_URL='https://outlook.office365.com/owa/calendar/.../calendar.ics'
```

```yaml
- id: outlook
  kind: ics
  priority: 10
  url: ${OUTLOOK_ICS_URL}
  prefix: "[Work] "
  skip_declined: true
```

**게시 기능이 비활성화된 경우 (테넌트 정책상 흔함)** — 대안을 순서대로 시도한다.

| 대안 | 방법 | 비고 |
|---|---|---|
| A. Power Automate | "일정이 추가될 때" 트리거 → Google Calendar 커넥터로 복사 | 회사 M365에 기본 포함된 경우가 많음. 가장 현실적 |
| B. Outlook 규칙 + 개인 계정 전달 | 회의 초대를 개인 메일로 자동 전달 | 회사 정보보안 정책 위반 소지 → **반드시 사전 확인** |
| C. 데스크톱 Outlook 내보내기 | 주기적 `.ics` 내보내기 후 `file:` 옵션 사용 | 수동, 자동화 불가 |
| D. Azure AD 앱 등록 승인 요청 | IT에 Graph API `Calendars.Read` 위임 권한 요청 | 정공법. 승인되면 가장 안정적 |

> **주의**: B안은 회사 정보보안 규정 위반이 될 수 있다. Primetals 사내 규정과
> 정보보안 담당자 확인 없이 진행하지 말 것. 이 판단은 사용자 책임이다.

### 2.2 Notion

1. https://www.notion.so/my-integrations → **New integration** 생성
2. Internal Integration Token 복사
3. 대상 데이터베이스 페이지 열기 → 우상단 `...` → **연결(Connections)** → 방금 만든 통합 추가
4. 데이터베이스 ID는 URL에서 추출:
   `notion.so/workspace/`**`a1b2c3d4e5f6...`**`?v=...` ← 굵은 부분

```bash
export NOTION_TOKEN='ntn_...'
export NOTION_DATABASE_ID='a1b2c3d4e5f6...'
```

```yaml
- id: notion
  kind: notion
  priority: 50
  token: ${NOTION_TOKEN}
  database_id: ${NOTION_DATABASE_ID}
  date_property: Date          # Notion의 날짜 컬럼명과 정확히 일치해야 함
  description_properties: [Status, Opportunity]
```

`date_property`가 틀리면 Notion이 400을 반환한다. 오류 메시지가 컬럼명을 알려준다.

> API 대신 간단히 가려면 데이터베이스 뷰 → `...` → **Copy link to calendar**로
> ICS 링크를 받아 `kind: ics`로 써도 된다. 대신 페이지 URL·속성은 가져오지 못한다.

### 2.3 Google Calendar

**서비스 계정 방식 (자동 실행에 권장)**

1. console.cloud.google.com → 프로젝트 생성 → **Google Calendar API** 사용 설정
2. 사용자 인증 정보 → **서비스 계정** 생성 → JSON 키 다운로드
3. Google 캘린더 웹 → 대상 캘린더 설정 → **특정 사용자와 공유** →
   서비스 계정 이메일(`...@....iam.gserviceaccount.com`) 추가
   - 소스로만 쓸 캘린더: "모든 일정 세부정보 보기"
   - 통합 대상(sink) 캘린더: **"일정 변경"** 권한 필요

```bash
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat ~/secrets/sa.json)"   # 내용 또는 경로 모두 가능
python -m calhub google-calendars      # 접근 가능한 캘린더 ID 확인
```

**OAuth 방식 (Workspace가 서비스 계정 공유를 막는 경우)**

```bash
python -m calhub google-auth ~/Downloads/client_secret.json -o secrets/google_token.json
export GOOGLE_TOKEN_JSON="$(cat secrets/google_token.json)"
```

### 2.4 iCloud

Apple ID 암호는 2단계 인증 때문에 작동하지 않는다. **앱 전용 암호**가 필요하다.

1. appleid.apple.com → 로그인 및 보안 → **앱 암호** → 새로 생성
2. 생성된 `xxxx-xxxx-xxxx-xxxx` 형식의 암호 사용

```bash
export ICLOUD_USERNAME='testarossa05@icloud.com'
export ICLOUD_APP_PASSWORD='xxxx-xxxx-xxxx-xxxx'
```

```yaml
- id: icloud
  kind: caldav
  priority: 40
  enabled: true
  url: https://caldav.icloud.com/
  username: ${ICLOUD_USERNAME}
  password: ${ICLOUD_APP_PASSWORD}
```

---

## 3. 아이폰 연결

### 방법 A — Google 전용 캘린더 (권장: 비공개 유지)

1. Google 캘린더 웹에서 **빈 캘린더** 새로 생성 (예: "Unified")
   - 기존 캘린더를 쓰지 말 것. 미러 전용 캘린더면 사고 반경이 0이 된다.
2. `config.yaml`의 `google` sink를 `enabled: true`로 변경하고 캘린더 ID 지정
3. 아이폰: 설정 → 앱 → 캘린더 → 계정 → 계정 추가 → Google → 로그인
4. Google 캘린더 앱 또는 iOS 캘린더에서 "Unified" 체크

장점: 완전 비공개, 동기화 빠름, 색상 구분 가능.

### 방법 B — 구독 `.ics` (Apple 개발자 계정·Google 계정 불필요)

1. 추측 불가능한 슬러그 생성:
   ```bash
   openssl rand -hex 16     # 예: 7f3a9c2e8b1d4a6f0c5e9b2d7a4f1c8e
   ```
2. GitHub 저장소 → Settings → Secrets and variables → Actions에 `PUBLISH_SLUG` 등록
3. Settings → Pages → Source를 **GitHub Actions**로 설정
4. 워크플로 실행 후 아이폰에서:
   설정 → 앱 → 캘린더 → 계정 → 계정 추가 → **기타** → **구독 캘린더 추가** →
   `https://testarossa05.github.io/<repo>/<slug>/unified.ics`

> **경고**: 이 URL은 공개된다. `.ics` 구독은 인증을 지원하지 않기 때문에
> URL을 아는 사람은 누구나 일정을 읽을 수 있다. 업무 소스에는 `privacy: busy`를
> 적용해 시간만 노출하고 제목은 내보내지 않는 것을 권장한다.

```yaml
- id: outlook
  kind: ics
  privacy: busy          # 제목 → "• Busy", 설명·장소 제거. 시간만 남음
  busy_title: "• 업무"
```

---

## 4. 자동 실행 (GitHub Actions)

저장소 Secrets에 등록할 항목:

| Secret | 필수 | 설명 |
|---|---|---|
| `PUBLISH_SLUG` | ics_file 사용 시 | 추측 불가능한 URL 경로 |
| `OUTLOOK_ICS_URL` | | Outlook 게시 ICS 링크 |
| `NOTION_TOKEN` / `NOTION_DATABASE_ID` | | Notion 연동 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | | 서비스 계정 키 전체 내용 |
| `GOOGLE_SOURCE_CALENDAR_ID` / `GOOGLE_TARGET_CALENDAR_ID` | | 읽을/쓸 캘린더 |
| `ICLOUD_USERNAME` / `ICLOUD_APP_PASSWORD` | | iCloud 연동 |

기본 주기는 15분이다. GitHub는 부하 시 예약 실행을 지연시키므로 "약 15분"으로
이해할 것. 더 짧은 주기가 필요하면 로컬 Mac에서 `launchd` 또는 `cron`으로 실행한다.

```bash
# 예: 맥에서 10분마다 실행
*/10 * * * * cd ~/calhub && PYTHONPATH=src .venv/bin/python -m calhub sync -q
```

---

## 5. 문제 해결

| 증상 | 원인 및 조치 |
|---|---|
| `the URL did not return an iCalendar feed` | HTML 링크를 복사했다. ICS 링크를 다시 확인 |
| Notion `404` | 데이터베이스를 통합과 공유하지 않았다. `...` → 연결 → 통합 추가 |
| Notion `400` + 컬럼명 | `date_property`가 실제 컬럼명과 다르다 |
| `CalDAV connection failed` | Apple ID 암호를 썼다. 앱 전용 암호로 교체 |
| `source(s) failed: ... 이전 버전을 유지` | 의도된 동작. 소스를 고치거나 `--force` |
| `max_delete_ratio` 초과로 중단 | 소스가 대량 누락됐을 가능성. `sync -n`으로 확인 후 `--force` |
| 아이폰에 반영이 느림 | iOS 구독 캘린더 새로고침 주기 제한. 방법 A(Google)가 더 빠름 |
| 종일 일정 날짜가 하루 밀림 | `timezone` 설정 확인 (`Asia/Seoul`) |

진단은 항상 이 순서로:

```bash
python -m calhub check        # 어느 소스가 실패하는지
python -m calhub agenda -d 7  # 데이터가 제대로 들어오는지
python -m calhub sync -n      # 무엇을 쓰려 하는지
```

---

## 6. 향후 네이티브 iOS 앱으로 확장할 경우

이 엔진은 그대로 백엔드가 된다. 추가로 필요한 것:

1. **유료 Apple Developer 계정** ($99/년) — 무료 프로비저닝은 7일마다 재설치
2. SwiftUI + **EventKit** — 아이폰 로컬 캘린더 읽기/쓰기
3. 이 엔진을 API 서버로 배포하거나, 각 어댑터를 Swift로 이식
4. 위젯 / 알림 / 잠금화면 라이브 액티비티

다만 현재 방식으로도 아이폰 기본 캘린더 앱에서 위젯·알림·Siri가 모두 동작하므로,
네이티브 앱의 실익은 크지 않다. 먼저 이 엔진을 수 주간 운용해 보고 부족한 점이
구체화된 뒤에 판단할 것을 권한다.
