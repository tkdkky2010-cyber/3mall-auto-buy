#!/bin/bash
# ★폐기(2026-07-16) — 단일 CFT 원칙: 병렬판은 롯데를 9223 별도 Chrome 으로 띄우는 설계였는데,
#   그 창이 사용자 포커스를 강탈해 폐기. 모든 자동화 = 로그인된 CFT 9222 하나 → 순차 step1.sh 사용.
echo "[DEPRECATED] step1_parallel.sh → 순차 step1.sh 실행 (단일 CFT 원칙)"
exec bash "$(dirname "$0")/step1.sh"
