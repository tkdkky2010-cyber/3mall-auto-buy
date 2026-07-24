#!/bin/bash
# ★폐기(2026-07-16) — 단일 CFT 원칙: check10 도 로그인된 CFT 9222 를 사용한다.
#   이 스크립트가 띄우던 9223 실제 Chrome(Check10RealChrome)이 사용자 창 포커스를
#   반복적으로 강탈해 폐기. 호출 시 9222 CFT 런처로 위임한다.
echo "[DEPRECATED] launch-check10-chrome.sh → 로그인된 CFT 9222 사용 (launch-hmall-chrome.sh 위임)"
exec bash "$(dirname "$0")/hsmaster/scripts/launch-hmall-chrome.sh"
