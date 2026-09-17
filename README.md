# calhub — Unified Calendar

Outlook/Teams, Notion, Google Calendar, iCloud에 흩어진 일정을 **하나의 캘린더**로
통합해 아이폰에서 한 눈에 보기 위한 동기화 엔진.

```
Outlook (ICS)  ─┐
Notion (API)   ─┤                        ┌─→ unified.ics  → 아이폰 "구독 캘린더"
Google (API)   ─┼─→ 정규화 → 중복제거 ──┤
iCloud (CalDAV)─┘                        └─→ Google 전용 캘린더 → 아이폰 계정 동기화
```

## 왜 네이티브 앱이 아닌가

당초 요청은 iOS 앱이었으나, 실제 제약을 반영해 **통합 엔진 + 단일 캘린더** 방식으로
구현했다. 판단 근거는 다음과 같다.

| 제약 | 영향 |
|---|---|
| 유료 Apple Developer 계정 없음 | 무료 프로비저닝은 7일마다 재설치 필요 → 상시 사용 불가 |
| 회사 M365 Azure AD 앱 등록 차단 가능성 높음 | 네이티브 앱의 Graph API 연동이 막히면 앱의 핵심 기능이 무력화 |
| 통합의 본질은 UI가 아니라 데이터 파이프라인 | 아이폰 기본 캘린더 앱이 이미 충분한 UI |

이 방식은 Apple 개발자 계정도, 회사 IT 승인도 필요 없다. 나중에 네이티브 앱을
만들 경우 이 엔진을 그대로 백엔드로 재사용할 수 있다.

## 빠른 시작

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml     # config.yaml은 git에 올라가지 않음
export PYTHONPATH=src

python -m calhub check        # 설정 검증 + 모든 소스 연결 확인
python -m calhub agenda -d 7  # 통합 결과를 터미널에서 미리보기 (아무것도 쓰지 않음)
python -m calhub sync -n      # 무엇이 바뀔지만 출력 (dry-run)
python -m calhub sync         # 실제 동기화
```

## 명령어

| 명령 | 설명 |
|---|---|
| `sync` | 수집 → 병합 → 모든 sink에 기록. `-n` dry-run, `--force` 안전장치 무시 |
| `check` | 설정 파일과 모든 소스 연결을 검증 |
| `agenda -d N` | 향후 N일 통합 일정을 터미널에 출력 |
| `google-auth <client.json>` | Google OAuth 토큰 1회 발급 |
| `google-calendars` | 자격증명이 접근 가능한 Google 캘린더 목록 |

## 소스(Source)

| kind | 대상 | 인증 | 관리자 승인 |
|---|---|---|---|
| `ics` | Outlook/Teams, Notion, iCloud 공유, 기타 모든 ICS | 없음 (URL만) | **불필요** |
| `notion` | Notion 데이터베이스 | Integration Token | 불필요 |
| `google` | Google Calendar | 서비스 계정 또는 OAuth | 불필요 |
| `caldav` | iCloud, 기타 CalDAV | 앱 전용 암호 | 불필요 |

`ics` 어댑터가 M365 우회의 핵심이다. Outlook 웹 → 설정 → 캘린더 → 공유 캘린더 →
"캘린더 게시"로 ICS 링크를 얻으면 Azure AD 앱 등록 없이 회사 일정을 읽을 수 있다.

## 싱크(Sink)

| kind | 결과물 | 아이폰 연결 방법 |
|---|---|---|
| `ics_file` | 병합된 `.ics` 파일 | 설정 → 캘린더 → 계정 → 기타 → 구독 캘린더 추가 |
| `google` | 전용 Google 캘린더에 미러링 | 아이폰에 Google 계정 추가 |

## 보안 및 안전장치

이 도구는 업무 일정을 다루므로 기본값을 보수적으로 잡았다.

**기밀성**
- `.ics` 구독 URL은 **인증이 불가능하다.** 아이폰이 익명으로 가져가기 때문이다.
  공개 호스팅(GitHub Pages 등)을 쓴다면 `PUBLISH_SLUG`로 추측 불가능한 경로를
  반드시 설정하고, 업무 소스에는 `privacy: busy`를 적용해 **시간만 노출하고
  회의 제목은 내보내지 않는 것**을 강력히 권장한다.
- 회의 제목까지 아이폰에서 봐야 한다면 `ics_file` 공개 호스팅을 쓰지 말고
  `google` sink(비공개 캘린더)를 사용할 것.
- 모든 자격증명은 `${ENV_VAR}`로만 참조한다. `config.yaml`은 `.gitignore` 대상이다.

**데이터 보호**
- `allow_partial: false` (기본값) — 소스 하나라도 실패하면 **기록하지 않는다.**
  Outlook 링크가 만료됐을 때 업무 일정이 조용히 사라지는 사고를 막는다.
- `max_delete_ratio: 0.5` (기본값) — 한 번에 대상 캘린더의 절반 넘게 삭제하려 하면
  중단한다.
- 모든 소스가 실패하면 즉시 중단한다. 빈 집합을 동기화하면 통합 캘린더가 전멸한다.
- Google sink는 자신이 만든 이벤트에만 표식(`calhub_owner`)을 남기고, 표식 없는
  이벤트는 **절대 수정·삭제하지 않는다.**

## 중복 제거

같은 회의가 Outlook과 Notion 양쪽에 있는 경우가 흔하다. 제목을 정규화하고
(대소문자, `RE:`/`FW:`, `[Teams]` 같은 대괄호 태그, Zoom/Teams 상용구 제거)
시작·종료 시각을 분 단위로 버킷팅해 동일 회의를 판정한 뒤, `priority` 값이 낮은
소스의 사본을 남긴다. 표시용 접두사나 `busy` 마스킹은 원본 제목을 따로 보존하므로
중복 판정에 영향을 주지 않는다.

## 자동 실행

`.github/workflows/calendar-sync.yml`이 15분 주기로 동기화하고 GitHub Pages에
게시한다. 활성화 전에 워크플로 파일 상단의 **PRIVACY WARNING**을 반드시 읽을 것.

## 상세 설정

소스별 단계별 설정, M365가 막혔을 때의 대안, 아이폰 구독 방법은
[docs/SETUP-ko.md](docs/SETUP-ko.md) 참조.

## 개발

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```
