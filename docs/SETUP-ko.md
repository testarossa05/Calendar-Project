# 설치 및 설정 가이드

## 0. 결론 먼저

| 항목 | 권장안 | 이유 |
|---|---|---|
| 통합 방식 | 통합 엔진 + 단일 캘린더 | Apple 개발자 계정·회사 IT 승인 불필요 |
| 회사 일정 수집 (1순위) | Graph API 위임 권한 | 게시 차단과 무관한 별도 정책 축 |
| 회사 일정 수집 (2순위) | macOS EventKit | 테넌트 변경 전혀 불필요 |
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

### 2.1 Microsoft 365 / Teams — 게시 기능이 차단된 경우

Teams 회의는 실제로는 Outlook 캘린더에 저장된다. 따라서 Outlook 캘린더만 읽으면 된다.

**조직 정책으로 "캘린더 게시"가 막혀 있어도 다른 경로가 남아 있다.** 핵심은 이 두
정책이 서로 다른 축이라는 점이다.

| 기능 | 통제 주체 | 설정 |
|---|---|---|
| 캘린더 게시 | Exchange Online | `SharingPolicy` / OWA Mailbox Policy |
| 앱 등록 | Entra ID (Azure AD) | `Users can register applications` |

게시가 막혔다는 사실만으로 앱 등록까지 막혔다고 단정할 수 없다. 아래 순서로 시도한다.

#### 경로 A — Microsoft Graph (권장, 서버 자동화 가능)

**위임(delegated) 권한 `Calendars.Read`** 를 쓴다. 본인 사서함만 읽으므로 응용
프로그램 권한(테넌트 전체 읽기, 관리자 동의 필수)과는 요구 수준이 다르다.

1. Azure 포털 → **Microsoft Entra ID** → **앱 등록** → **새 등록**
   - 이름: 아무거나 (예: `calhub-personal`)
   - 지원 계정 유형: **이 조직 디렉터리의 계정만**
   - 리디렉션 URI: 비워 둠
2. 생성된 앱 → **인증** → 고급 설정 → **공용 클라이언트 흐름 허용: 예**
   - 이걸 켜지 않으면 device code 흐름이 시작되지 않는다.
3. **API 권한** → 권한 추가 → Microsoft Graph → **위임된 권한** → `Calendars.Read`
4. 개요 탭에서 **애플리케이션(클라이언트) ID** 와 **디렉터리(테넌트) ID** 복사

```bash
python -m calhub ms-auth --client-id <클라이언트ID> --tenant-id <테넌트ID>
# 터미널에 표시된 URL과 코드를 브라우저에 입력해 1회 로그인
export MS_CLIENT_ID='...'
export MS_TENANT_ID='...'
export MS_TOKEN_CACHE="$(cat secrets/ms_token_cache.json)"
```

```yaml
- id: outlook
  kind: msgraph
  priority: 10
  client_id: ${MS_CLIENT_ID}
  tenant_id: ${MS_TENANT_ID}
  token_cache: ${MS_TOKEN_CACHE}
  prefix: "[Work] "
```

토큰 캐시에는 **리프레시 토큰**이 들어 있다. 비밀번호와 동일하게 취급할 것.
`secrets/`는 `.gitignore` 대상이며, 절대 커밋하지 않는다.

**막힐 수 있는 지점과 판별법**

| 오류 | 의미 | 대응 |
|---|---|---|
| 앱 등록 메뉴 자체가 비활성 | `Users can register applications = No` | 경로 B로 이동, 또는 IT에 등록 요청 |
| `AADSTS65001` / consent_required | 테넌트가 관리자 동의를 요구 | IT에 `Calendars.Read` 동의 요청, 또는 경로 B |
| `AADSTS50105` / `AADSTS53003` | 조건부 액세스 정책이 차단 (관리 기기만 허용 등) | 경로 B (조건부 액세스를 우회하지 않음) |
| device flow 시작 실패 | "공용 클라이언트 흐름 허용"이 꺼짐 | 2번 단계 재확인 |

#### 경로 B — macOS EventKit (테넌트 변경 전혀 불필요)

**Mac을 보유하고 계시므로 이 경로가 가장 확실하다.** Mac의 Outlook 또는 캘린더 앱에
회사 계정이 이미 연결되어 있다면, 그 데이터는 이미 로컬 EventKit 저장소에 있다.
이를 읽는 것은 **이미 승인된 클라이언트가 내려받은 데이터를 로컬에서 읽는 것**이며,
캘린더 게시도 앱 등록도 관리자 동의도 필요하지 않다.

```bash
pip install pyobjc-framework-EventKit
```

```yaml
- id: outlook
  kind: eventkit
  priority: 10
  calendars: ["Calendar"]     # 생략하면 로컬 전체 캘린더
  prefix: "[Work] "
```

첫 실행 시 macOS가 캘린더 접근 권한을 묻는다. 거부하면 캘린더가 0개로 보인다.
나중에 바꾸려면 시스템 설정 → 개인정보 보호 및 보안 → 캘린더에서 터미널 앱에 허용.

제약: **Mac에서만, Mac이 깨어 있을 때만** 동작한다. 따라서 GitHub Actions가 아니라
Mac에서 주기 실행하고, 결과를 Google sink로 밀어 넣는 구성이 맞다.

```bash
# 예: 15분마다. crontab -e
*/15 * * * * cd ~/calhub && PYTHONPATH=src .venv/bin/python -m calhub sync -q
```

Mac이 잠들어 있는 동안에는 갱신이 멈추지만, **직전까지의 일정은 Google 캘린더에
남아 있으므로** 아이폰에서는 계속 보인다. 새 회의 반영이 늦어질 뿐이다.

#### 경로 C — Power Automate

회사 M365에 Power Automate가 포함되어 있다면, "일정이 추가/변경될 때(V3)" Outlook
트리거 → Google Calendar 커넥터로 복사하는 흐름을 만들 수 있다. 이 경우 calhub는
그 Google 캘린더를 `kind: google` 소스로 읽으면 된다.

다만 게시를 막은 조직이라면 Power Automate의 **DLP 정책**으로 외부 커넥터(Google)도
차단했을 가능성이 높다. 시도해 보고 커넥터가 보이지 않으면 경로 B로 간다.

#### 경로 D — 수동 내보내기

Outlook 데스크톱에서 캘린더를 `.ics`로 내보낸 뒤 `kind: ics` + `file:` 옵션으로 읽는다.
자동화가 불가능하므로 최후 수단이다.

```yaml
- id: outlook
  kind: ics
  file: ~/Downloads/calendar.ics
```

> **하지 말아야 할 것**: 회의 초대를 개인 메일로 자동 전달하는 Outlook 규칙은
> 회사 정보보안 규정 위반이 될 수 있다. 사내 규정과 정보보안 담당자 확인 없이
> 진행하지 말 것. 위 A~D는 모두 사용자 본인에게 이미 허가된 데이터를 본인이
> 접근하는 방식이며, 접근 통제를 우회하지 않는다.

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
| `MS_CLIENT_ID` / `MS_TENANT_ID` | msgraph 사용 시 | Entra 앱 등록 정보 |
| `MS_TOKEN_CACHE` | msgraph 사용 시 | `ms-auth`가 만든 캐시 파일 **내용** |
| `OUTLOOK_ICS_URL` | ics 사용 시 | Outlook 게시 ICS 링크 |
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
| Graph `401` / `silent token refresh failed` | 캐시된 로그인 만료. `ms-auth` 재실행 |
| Graph `403` | 앱에 위임 `Calendars.Read` 권한이 없다 |
| device flow 시작 실패 | 앱 등록에서 "공용 클라이언트 흐름 허용"이 꺼져 있다 |
| EventKit이 캘린더 0개 | macOS 캘린더 접근 권한 미승인. 시스템 설정에서 허용 |
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
