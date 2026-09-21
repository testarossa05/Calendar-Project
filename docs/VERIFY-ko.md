# 검증 순서

## 원칙

세 가지만 지키면 문제가 생겼을 때 원인이 하나로 좁혀진다.

1. **자격증명이 필요 없는 것부터.** 인증 문제와 로직 문제가 섞이면 진단이 어렵다.
2. **소스는 한 번에 하나씩.** 한꺼번에 켜고 실패하면 어느 쪽인지 알 수 없다.
3. **쓰기 전에 항상 눈으로 확인.** `agenda`와 `sync -n`은 아무것도 쓰지 않는다.

각 단계의 **통과 기준**을 충족하지 못하면 다음 단계로 넘어가지 말 것.

```
사전   터미널·git·Python 점검        ~5분
0단계  설치            자격증명 0개   ~10분
1단계  local 소스      자격증명 0개   ~15분   ← 파이프라인 전체를 여기서 검증
2단계  ICS 파일 확인   자격증명 0개   ~5분
3단계  EventKit        자격증명 0개   ~10분   ← 회사 일정
4단계  Google          첫 외부 인증   ~20분
5단계  아이폰          -              ~5분    ← 여기서 실사용 시작
6단계  나머지 소스     소스별 인증    소스당 ~10분
7단계  자동 실행       -              ~5분
8단계  안전장치 확인   -              ~5분
```

5단계까지만 끝내도 실사용이 시작된다. 6단계 이후는 여유 있을 때 하나씩 붙이면 된다.

---

## 시작하기 전에 — 터미널과 사전 점검

### 터미널 여는 법

`⌘ + Space` → `터미널` 입력 → `Enter`.
(응용 프로그램 → 유틸리티 → 터미널 에도 있다.)

명령은 **한 줄씩 붙여넣고 Enter**를 누른다. 여러 줄을 한꺼번에 붙여도 동작하지만,
처음에는 한 줄씩 결과를 보면서 진행하는 편이 낫다.

### 어디에 설치할지 정한다

`git clone`은 **현재 위치에** `Calendar-Project` 폴더를 만든다. 터미널을 새로 열면
홈 디렉터리에서 시작하므로, 별도로 옮기지 않으면 `~/Calendar-Project`가 된다.
문서 폴더에 두고 싶다면 먼저 이동한다.

```bash
cd ~/Documents
```

지금 어디 있는지는 `pwd`로 확인한다.

### 사전 점검 (이것부터 실행)

아래를 통째로 붙여넣는다.

```bash
echo "=== 사전 점검 ==="
for c in git python3 brew; do
  if command -v "$c" >/dev/null 2>&1; then
    printf '%-9s: %s\n' "$c" "$("$c" --version 2>&1 | head -1)"
  else
    printf '%-9s: 없음\n' "$c"
  fi
done
python3 -c 'import sys; v=sys.version_info; print("판정     : Python %d.%d %s" % (v[0], v[1], "OK" if v>=(3,11) else "-> 3.11 이상 필요"))' 2>/dev/null
```

기대 출력:

```
=== 사전 점검 ===
git      : git version 2.39.5 (Apple Git-154)
python3  : Python 3.12.7
brew     : Homebrew 4.x.x
판정     : Python 3.12 OK
```

| 결과 | 조치 |
|---|---|
| `git : 없음` | `git --version`을 한 번 실행하면 macOS가 명령어 도구 설치를 제안한다. 설치 후 재점검 |
| `python3 : 없음` 또는 `3.11 이상 필요` | Homebrew가 있으면 `brew install python@3.12`. 없으면 아래 참조 |
| `brew : 없음` | Python이 3.11 이상이면 **문제없다.** Homebrew는 Python을 새로 깔 때만 필요 |

Homebrew가 없고 Python도 낮다면, [python.org/downloads](https://www.python.org/downloads/macos/)에서
macOS용 설치 파일을 받아 설치하는 쪽이 가장 간단하다. 설치 후 터미널을 새로 열고
사전 점검을 다시 실행한다.

### 알아둘 점

- `./scripts/install-macos.sh`는 **몇 분 걸린다.** 의존성을 내려받기 때문이며,
  특히 EventKit 바인딩(PyObjC)이 크다. 진행이 없어 보여도 기다린다.
- 설치 중 무언가 물어보는 일은 없다. 비밀번호를 요구하면 잘못된 명령이다.
- 이 저장소는 **공개(public)** 상태다. 코드는 공개돼도 무방하지만
  `config.yaml`과 `events.yaml`은 절대 커밋하지 말 것. 두 파일은 `.gitignore`에
  들어 있어 실수로 올라가지 않는다. 개인용으로만 쓸 것이라면 GitHub에서
  저장소를 Private으로 바꿔 두는 편이 안전하다.

---

## 0단계 — 설치

```bash
git clone https://github.com/testarossa05/Calendar-Project.git
cd Calendar-Project
./scripts/install-macos.sh
source .venv/bin/activate
```

이후 모든 명령은 이 디렉터리에서, venv가 활성화된 상태로 실행한다.
새 터미널을 열면 `cd Calendar-Project && source .venv/bin/activate`를 다시 해야 한다.

```bash
calhub --version
calhub doctor --no-probe
python -m pytest -q          # 이 Mac에서 코드 자체가 정상인지
```

**통과 기준**
- `calhub 0.1.0` 출력
- doctor의 Environment 항목이 모두 `✓` (EventKit은 아직 `!`여도 정상)
- 테스트 전부 통과

**실패 시**
- `calhub: command not found` → venv가 활성화되지 않음. `source .venv/bin/activate`
- Python 버전 오류 → `brew install python@3.12` 후 `rm -rf .venv` 하고 재실행

---

## 1단계 — local 소스 (파이프라인 전체 검증)

자격증명이 전혀 필요 없으므로 **수집 → 정규화 → 중복제거 → 기록** 전 과정을
여기서 한 번에 검증한다. 가장 중요한 단계다.

```bash
cp events.example.yaml events.yaml
cp config.example.yaml config.yaml
```

`events.yaml`을 본인 날짜로 수정한다.

`config.yaml`은 **그대로 두면 된다.** 복사된 상태에서 이미 `family` 소스 하나만
켜져 있고 나머지는 `enabled: false`다. 자격증명이 필요한 소스는 6단계에서 하나씩
켠다.

```yaml
sources:
  - id: family
    kind: local
    priority: 20
    file: events.yaml
    prefix: "[가족] "

sinks:
  - kind: ics_file
    path: out/unified.ics
    calendar_name: "Key - Unified"
```

```bash
calhub doctor                # 소스 접속까지 포함
calhub agenda -d 400         # 1년치를 눈으로 확인
```

**통과 기준** — 출력에 다음이 정확히 나와야 한다.
- 기념일이 `[가족] 결혼기념일 (7주년)` 형태로 연차가 맞게 표시
- 생일 연차가 실제 나이와 일치
- D-day가 의도한 날짜 (달력으로 교차 확인할 것)
- 종일 일정이 `all day`로 표시되고 날짜가 하루 밀리지 않음

날짜가 하루씩 밀리면 `config.yaml`의 `timezone: Asia/Seoul`을 확인한다.

```bash
calhub sync -n               # 무엇을 쓸지만 출력
calhub sync                  # 실제 기록
```

**통과 기준**: `Sink [ics_file]: create=N ...` 의 N이 agenda 건수와 일치.

---

## 2단계 — 생성된 ICS가 실제 캘린더 앱에서 열리는지

아이폰에 붙이기 전에 Mac에서 먼저 확인한다. 여기서 깨지면 아이폰에서도 깨진다.

```bash
open out/unified.ics
```

캘린더 앱이 "새로운 캘린더에 추가" 대화상자를 띄운다. **새 캘린더로** 추가한다
(기존 캘린더에 섞지 말 것 — 나중에 지우기 어려워진다).

**통과 기준**
- 일정이 캘린더 앱에 보인다
- 시각이 KST 기준으로 맞다
- 종일 일정이 올바른 날짜에, 종일로 표시된다

확인 후 그 테스트 캘린더는 삭제해도 된다.

---

## 3단계 — EventKit (회사 일정)

Mac의 Outlook/캘린더 앱에 회사 계정이 연결되어 있어야 한다.

`config.yaml`의 **`sources:` 아래에** 추가한다. `sinks:` 아래가 아니다 — 두 목록이
파일에서 나란히 있어 섞이기 쉽다. 들여쓰기(공백 2칸 + `- `)도 정확히 맞출 것.

붙여넣은 뒤 위치를 확인하려면:

```bash
grep -n 'sources:\|sinks:\|- id:\|- kind:' config.yaml
```

`- id: outlook-mac` 줄이 `sources:` 와 `sinks:` **사이**에 있어야 한다.

```yaml
  - id: outlook-mac
    kind: eventkit
    priority: 10
    prefix: "[Work] "
```

> id를 `outlook`이 아니라 `outlook-mac`으로 쓰는 이유: 예시 설정에 이미 비활성
> 상태의 `outlook`(msgraph) 블록이 있다. 같은 id를 쓰면 `duplicate source id`
> 오류가 난다. 비활성 블록도 id 중복 검사 대상이다.

```bash
calhub doctor
```

첫 실행에서 macOS가 캘린더 접근 권한을 묻는다. **허용**한다.

**통과 기준**: doctor의 EventKit 줄에 캘린더 목록이 보인다.
`✓ EventKit: 3 calendar(s): Calendar, 업무, ...`

```bash
calhub agenda -d 14
```

**통과 기준**: 이번 주 실제 회사 일정이 `[Work]` 접두사와 함께 나온다.
반복 회의가 매 주차마다 따로 나오는지도 확인한다.

**실패 시**
- 캘린더 0개 → 시스템 설정 → 개인정보 보호 및 보안 → 캘린더 → 터미널 앱 허용
- 권한 프롬프트가 안 뜸 → `calhub sync` 한 번 실행

**여기서 판단할 것**: 회의 제목을 통합 캘린더에 그대로 넣을지 여부.
외부에 공개되는 경로를 쓸 계획이면 `privacy: busy`를 넣어 시간만 남긴다.

```yaml
  - id: outlook-mac
    kind: eventkit
    priority: 10
    privacy: busy
    busy_title: "• 업무"
```

---

## 4단계 — Google (첫 외부 자격증명)

`docs/SETUP-ko.md` 2.3절대로 서비스 계정을 만들고 JSON 키를 받는다.

```bash
export GOOGLE_SERVICE_ACCOUNT_JSON="$(cat ~/Downloads/서비스계정키.json)"
calhub google-calendars
```

**통과 기준**: 캘린더 목록과 각각의 access 권한이 출력된다.
아무것도 안 나오면 캘린더를 서비스 계정 이메일과 공유하지 않은 것이다.

Google 캘린더 웹에서 **빈 캘린더**를 새로 만든다 (예: `Unified`).
기존 캘린더를 쓰지 말 것 — 미러 전용이면 사고 반경이 0이 된다.
그 캘린더를 서비스 계정 이메일에 **"일정 변경"** 권한으로 공유한다.

```bash
calhub google-calendars      # Unified의 캘린더 ID 확인, access가 writer인지 확인
export GOOGLE_TARGET_CALENDAR_ID='...@group.calendar.google.com'
```

`config.yaml`의 google sink를 켠다.

```bash
calhub sync -n               # create= 건수가 예상과 맞는지
calhub sync
```

**통과 기준**: Google 캘린더 웹에서 Unified 캘린더에 일정이 보인다.

```bash
calhub sync                  # 한 번 더
```

**통과 기준 (중요)**: 두 번째 실행은 `create=0 update=0 unchanged=N`이어야 한다.
create가 다시 N이면 멱등성이 깨진 것이므로 여기서 멈추고 알릴 것.

---

## 5단계 — 아이폰

아이폰 → 설정 → 앱 → 캘린더 → 계정 → 계정 추가 → Google → 로그인.
캘린더 앱에서 `Unified` 체크.

**통과 기준**: 아이폰 캘린더에 통합 일정이 보인다.

**여기부터 실사용이 가능하다.** 아래는 소스를 늘리는 작업이다.

---

## 6단계 — 나머지 소스 (하나씩)

**반드시 하나씩** 추가하고, 매번 아래 3단계를 반복한다.

```bash
calhub doctor                # 새 소스가 ✓ 인지
calhub agenda -d 14          # 데이터가 맞는지 눈으로
calhub sync -n               # 무엇이 바뀔지
calhub sync
```

권장 순서:

| 순서 | 소스 | 준비물 | 참조 |
|---|---|---|---|
| 1 | 배우자 공유 Google 캘린더 | 캘린더 공유만 | SETUP-ko.md 2.5 |
| 2 | Notion | Integration Token | SETUP-ko.md 2.2 |
| 3 | iCloud | 앱 전용 암호 | SETUP-ko.md 2.4 |
| 4 | msgraph | Entra 앱 등록 | SETUP-ko.md 2.1 경로 A |

EventKit이 이미 회사 일정을 가져오고 있다면 **msgraph는 선택 사항**이다.
Mac이 꺼져 있을 때도 동기화되어야 할 때만 추가하면 된다.

두 소스가 같은 회의를 갖고 있으면 중복 제거가 동작해야 한다.

```bash
calhub sync -n
```

**통과 기준**: `dupes_dropped=N`이 0보다 크고, 출력의 `kept ... dropped ...`
줄에서 우선순위가 낮은 번호(= 더 신뢰하는 소스)가 남았는지 확인한다.

---


### 6-B. Notion 싱크와 동기화 버튼 (선택)

소스가 안정되면 Notion 데이터베이스로도 기록할 수 있다.

```bash
export NOTION_TOKEN='ntn_...'
calhub notion-setup --parent-page '<부모 페이지 URL>'
```

출력된 id를 `config.yaml`의 notion 싱크에 넣고 `enabled: true`로 바꾼 뒤:

```bash
calhub sync -n     # 무엇을 쓸지
calhub sync        # 실제 기록 (첫 실행은 느리다)
calhub sync        # 두 번째는 create=0 unchanged=N 이어야 한다
```

**통과 기준**
- Notion 데이터베이스에 일정이 보인다
- 종일 일정의 **마지막 날짜가 하루 더 길지 않다** (Notion은 종료일 포함 방식)
- 두 번째 실행이 `created=0`이다. 아니면 멱등성이 깨진 것이므로 멈추고 알릴 것

버튼:

```bash
./scripts/install-button.sh
```

바탕화면의 `캘린더 동기화.command`를 더블클릭한다.

**통과 기준**: 터미널이 열려 동기화가 돌고, 성공 시 알림이 뜬 뒤 창이 닫힌다.
일부러 `config.yaml` 이름을 바꿔 두고 눌러 보면 창이 열린 채 오류가 보여야 한다.

---

## 7단계 — 자동 실행

```bash
./scripts/install-launchd.sh
```

```bash
tail -f ~/Library/Logs/calhub/sync.log
```

**통과 기준**: 등록 즉시 1회 실행되어 로그가 남는다.

```bash
launchctl print gui/$(id -u)/com.calhub.sync | head -20
```

15분 뒤 로그가 한 줄 더 늘어나는지 확인한다.

---

## 8단계 — 안전장치가 실제로 동작하는지

여기까지 왔으면 마지막으로 **일부러 고장 내서** 가드를 확인한다.
이걸 해두지 않으면 진짜 사고가 났을 때 동작 여부를 모른다.

`config.yaml`에서 소스 하나의 경로/ID를 일부러 틀리게 바꾼다.

```bash
calhub sync
```

**통과 기준**: 아래처럼 출력되고, **Google 캘린더의 기존 일정이 그대로 남아 있어야
한다.**

```
✗ family    local   FAILED: events file not found ...
! SKIPPED: source(s) failed: family. Writing now would drop their events ...
```

Google 캘린더 웹에서 일정이 지워지지 않았는지 직접 확인한다.
설정을 원복하고 `calhub sync`로 정상 복귀를 확인하면 끝이다.

---

## 요약 체크리스트

```
[ ] -  사전 점검: git 있음, Python 3.11 이상
[ ] 0  calhub --version / doctor --no-probe / pytest 통과
[ ] 1  agenda에서 기념일·D-day 날짜가 달력과 일치
[ ] 2  out/unified.ics가 Mac 캘린더 앱에서 정상 표시
[ ] 3  doctor에서 EventKit 캘린더 목록 확인, 회사 일정이 agenda에 표시
[ ] 4  google-calendars에서 writer 권한 확인, 2회차 sync가 unchanged
[ ] 5  아이폰 캘린더에 Unified 표시
[ ] 6  소스별로 하나씩 추가, 중복 제거 동작 확인
[ ] 6B Notion 싱크 2회차가 created=0, 버튼 더블클릭 동작 (선택)
[ ] 7  launchd 등록 후 로그 생성 확인
[ ] 8  소스를 일부러 깨뜨렸을 때 기존 일정이 보존되는지 확인
```

## 막히면

```bash
calhub doctor
```

문제마다 해결 방법을 함께 출력한다. 그래도 모르겠으면
`docs/SETUP-ko.md` 5장의 증상별 표를 확인한다.
