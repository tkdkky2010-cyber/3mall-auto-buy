# Chrome 자동화 setup — Chrome for Testing + Profile 6

매일 자동화 시 메인 Chrome 안 닫히고 + Hmall 봇 차단 회피되는 격리된 Chrome 환경.

## 핵심 결정

| 항목 | 선택 | 이유 |
|---|---|---|
| Browser binary | **Chrome for Testing** (~/ChromeForTesting) | 메인 "Google Chrome.app"과 완전 별개 .app → 충돌 X |
| 데이터 디렉토리 | **`~/HmallChrome`** | 메인 Chrome `~/Library/.../Chrome` 과 분리 |
| 프로필 | **`Profile 6`** | JASON MORY Google 계정 binding (gaia_id: 108971081211332048810) |
| Launch script | git tracked: **`hsmaster/scripts/launch-hmall-chrome.sh`** | git pull로 양쪽 컴터 sync |
| 호출 경로 | `~/bin/launch-hmall-chrome.sh` (symlink → 위 파일) | 어디서든 호출 가능 |
| CDP port | **9222** | Playwright `connect_over_cdp` |

## 첫 setup (컴터당 1회)

### 1. Chrome for Testing 설치 (Homebrew node 필요)

```bash
export PATH="/opt/homebrew/bin:$PATH"  # apple silicon 의 경우
mkdir -p $HOME/ChromeForTesting
npx -y @puppeteer/browsers install chrome@stable --path "$HOME/ChromeForTesting"
```

→ `~/ChromeForTesting/chrome/mac_arm-NNN.NNN.NNN.NN/chrome-mac-arm64/Google Chrome for Testing.app` 설치됨.

### 2. Profile 6 데이터 준비

**A. 다른 컴터에서 옮긴다면:**
```bash
# 다른 컴터에서 zip
cd ~/HmallChrome
zip -r ~/Desktop/Profile6.zip "Profile 6" "Local State"

# 새 컴터로 옮긴 후
cd ~/HmallChrome
unzip ~/Desktop/Profile6.zip
```

**B. 새로 만든다면 (Profile 6 데이터 0 상태):**
- launch script 처음 실행 시 빈 Profile 6 자동 생성됨
- Chrome for Testing 창 우측 상단 "Sign in to Chromium" → JASON MORY (또는 원하는 Google 계정)으로 로그인 1회
- Hmall 메인 페이지 잠시 둘러보기 (이력 누적)
- → 누적 이력 부족 시 초반 봇 차단 risk 있을 수 있음. 시간 지나면 해결

### 3. ~/bin symlink 생성 (이 프로젝트 git clone 후 1회)

```bash
mkdir -p ~/bin
ln -s "$HOME/Desktop/Vibe Coding/3mall auto buy/hsmaster/scripts/launch-hmall-chrome.sh" \
      "$HOME/bin/launch-hmall-chrome.sh"
```

→ 이후 어디서든 `~/bin/launch-hmall-chrome.sh` 호출하면 git tracked script 실행.

## 매일 사용

```bash
~/bin/launch-hmall-chrome.sh    # CFT 띄움 (이미 떠있으면 그냥 종료)
```

CDP 9222 활성화되면 자동화 스크립트 (`buy/run.py`, `cart/check10.py`)가 `connect_over_cdp` 으로 attach.

## 환경 변수 (기본값 변경 시)

| ENV | 기본값 | 용도 |
|---|---|---|
| `PORT` | 9222 | CDP 디버깅 포트 (스크립트 내 hardcoded) |
| (수정 시 스크립트 직접 편집) | | |

스크립트 내부:
- `PORT=9222`
- `USER_DATA_DIR="$HOME/HmallChrome"`
- `PROFILE_DIR="Profile 6"`

## 동작 흐름

```
[메인 Chrome]                          [자동화 CFT]
- ~/Library/.../Chrome                 - ~/HmallChrome/Profile 6/
- 평소 브라우징 (YouTube, Gmail 등)    - JASON MORY Google 로그인
- CDP 없음                             - port 9222 CDP
- "Google Chrome.app"                  - "Google Chrome for Testing.app"
   ↓ 독립적                                ↓ 독립적
사용자 작업 그대로                     Playwright connect_over_cdp("http://127.0.0.1:9222")
                                       → Hmall 19계정 자동 로그인 + 카트/체크아웃
```

→ 두 .app은 macOS LaunchServices 관점에서 다른 앱. 같은 Mac에서 동시 동작 OK.

## 잘못된 옵션들 (해본 결과)

| 시도 | 결과 |
|---|---|
| `open -na "Google Chrome" --args ...` | 메인 Chrome 강제 종료 (LaunchServices가 같은 .app 처리) |
| 메인 Chrome.app 직접 binary 실행 | 메인 Chrome 가끔 종료, 데이터 충돌 |
| 빈 user-data-dir로 시작 | Hmall "다른 로그인 수단" 차단 (이력 부족) |

## 문제 해결

### Profile 6 Google 로그인이 안 된 경우 (sync 미동작)
```bash
~/bin/launch-hmall-chrome.sh
```
실행 후 띄워진 CFT 창 우측 상단 프로필 아이콘 → "Sign in to Chromium" 클릭 → Google 계정 입력. 이후 Local State에 자동 binding.

### Local State 깨진 경우 (Profile 6 metadata 비어있음)
~/HmallChrome/Local State JSON 직접 수정:
```python
import json
ls = json.load(open('/Users/jasonkim/HmallChrome/Local State'))
# 'Profile 6' 키가 빈 metadata면 다른 작동 프로필 (예: Default) 의 metadata 복사
ic = ls['profile']['info_cache']
ic['Profile 6'] = ic.pop('Default')  # 또는 적절히
ls['profile']['profiles_order'] = [p for p in ls['profile']['profiles_order'] if p != 'Default']
ls['profile']['last_used'] = 'Profile 6'
json.dump(ls, open('/Users/jasonkim/HmallChrome/Local State', 'w'), indent=2, ensure_ascii=False)
```

### CDP 9222 응답 X
```bash
pkill -f "user-data-dir=/Users/jasonkim/HmallChrome"
sleep 3
~/bin/launch-hmall-chrome.sh
curl http://127.0.0.1:9222/json/version  # 정상 응답 확인
```
