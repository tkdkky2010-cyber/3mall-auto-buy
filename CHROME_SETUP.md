# Chrome 자동화 setup — 결정 사항 + 이유

매일 자동화 시 Chrome을 어떻게 띄워야 메인 Chrome 영향 없이 + Hmall 봇 차단 우회까지 되는지 정리.

## 핵심 요건 3개

1. **메인 Chrome 안 닫힘** — 평소 쓰는 Chrome 인스턴스 그대로 유지
2. **Hmall 봇 차단 우회** — Chrome user-agent + 누적 브라우징 이력으로 정상 사용자처럼 보여야 함
3. **로그인 영속성** — 한 번 로그인하면 cookies/세션 디스크 저장, 재시작 후 유지

## 결정

| 항목 | 채택 | 채택 안 한 이유 |
|---|---|---|
| **실제 Google Chrome binary 사용** | ✅ `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | Chrome for Testing은 user-agent가 "Chrome for Testing" → Hmall 봇 의심 가능 |
| **별도 user-data-dir** | ✅ `~/HmallChrome/Default` (메인 Chrome 데이터와 분리) | 같은 데이터 디렉토리 쓰면 SingletonLock 충돌 |
| **Profile 6 데이터 카피본** | ✅ `~/HmallChrome/Default` 안에 메인 Chrome의 Profile 6 데이터 (Google 로그인 + 브라우징 이력) 복사 | 빈 프로필 시 Hmall 봇 차단 ("다른 로그인 수단" 문구) |
| **직접 binary 실행 + `disown`** | ✅ `bash` 백그라운드로 띄움 | `open -na "Google Chrome"`은 macOS LaunchServices가 메인 Chrome 강제 종료시킴 |
| **CDP `--remote-debugging-port=9222`** | ✅ Playwright `connect_over_cdp` 으로 attach | Playwright 자체가 Chrome 띄우면 별도 인스턴스 누적, 봇 탐지 더 잘됨 |

## 동작 흐름

```
[USER 메인 Chrome]                  [자동화 Chrome]
  /Library/.../Chrome data            ~/HmallChrome/Default (Profile 6 카피)
  ↓                                   ↓
  Google Chrome.app 실행 중           Google Chrome.app 다른 process로 실행 중
  (port: 일반)                        (port: 9222 CDP)
  ↓                                   ↓
  영향 없음                           Playwright connect_over_cdp("http://127.0.0.1:9222")
                                      → 봇 차단 회피 + 영구 로그인 사용
```

## 잘못된 옵션들 (해본 결과)

| 시도 | 결과 |
|---|---|
| `open -na "Google Chrome" --args ...` | 메인 Chrome 강제 종료 (LaunchServices 충돌) |
| Chrome for Testing (Playwright bundled) | Hmall 봇 차단됨 (user-agent로 탐지) |
| `~/HmallChrome` 빈 프로필로 시작 | Hmall 로그인 시 "다른 로그인 수단" 차단 |
| 메인 Chrome의 user-data-dir 직접 사용 (`--user-data-dir=~/Library/Application Support/Google/Chrome --profile-directory="Profile 6"`) | 메인 Chrome 닫고 자동화 Chrome 띄우는 사이클 — 매번 메인 Chrome 닫혀야 가능 |

## 사용법

### 자동화 Chrome 띄우기
```bash
cd "~/Desktop/Vibe Coding/3mall auto buy"
bash hsmaster/scripts/launch-chrome-cdp.sh
```

→ `~/HmallChrome/Default` 데이터로 새 Chrome process 시작 + CDP 9222 활성화. 메인 Chrome 영향 X.

### 첫 setup (한 번만)

```bash
# 1. 메인 Chrome 종료 (Profile 6 lock 풀기)
osascript -e 'quit app "Google Chrome"'
sleep 3

# 2. Profile 6 데이터를 ~/HmallChrome/Default로 복사
mkdir -p ~/HmallChrome
cp -r "$HOME/Library/Application Support/Google/Chrome/Profile 6" ~/HmallChrome/Default

# 3. 메인 Chrome 다시 띄워도 OK
open -a "Google Chrome"

# 4. 자동화 Chrome 띄우기
bash hsmaster/scripts/launch-chrome-cdp.sh

# 5. 자동화 Chrome 창에서 Hmall 로그인 1회 (cookies는 이미 있을 가능성 높음)
```

이후 매일 launch-chrome-cdp.sh만 실행하면 됨.

## 환경 변수

| ENV | 기본값 | 용도 |
|---|---|---|
| `CDP_PORT` | 9222 | CDP 디버깅 포트 |
| `HMALL_USER_DATA_DIR` | `$HOME/HmallChrome` | 자동화 Chrome 데이터 디렉토리 |
| `HMALL_CHROME_PROFILE` | Default | 프로필 폴더명 |
| `CHROME_BIN` | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` | Chrome binary 경로 |

## 문제 해결

### CDP 9222 접속 실패
```bash
# 포트 확인
curl http://127.0.0.1:9222/json/version

# 안 뜨면 launch script 다시
bash hsmaster/scripts/launch-chrome-cdp.sh
```

### Hmall 로그인 차단 ("다른 로그인 수단")
- ~/HmallChrome/Default 에 Profile 6 데이터가 있는지 확인 (1GB 정도)
- 비어있으면 위 "첫 setup" 절차 다시

### 메인 Chrome이 닫힘
- launch script가 직접 binary 실행 + `disown`인지 확인 (`open -na` 쓰면 안 됨)
- 두 Chrome 인스턴스 모두 `--user-data-dir`이 다른지 확인 (같으면 SingletonLock 충돌)
