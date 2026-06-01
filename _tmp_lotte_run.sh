#!/usr/bin/env bash
# lotte cart_plan (설화수) — 계정 순차 실행.
# 설화수는 계정 간 대기 없음 (WAIT_SEC=0). 7분 추적회피 정책은 Hmall 식품(buy/run.py) 전용.
set -u

cd "/Users/jasonkim/Desktop/Vibe Coding/3mall auto buy"
LOG="logs/2026-05-26-lotte-all.log"
{
  echo
  echo "===== lotte multi-account run start $(date '+%Y-%m-%d %H:%M:%S') ====="
} >> "$LOG"

# (account_idx, combo_no) 쌍 — 사용자 plan
PAIRS=(
  "2 1"
  "3 4"
  "4 4"
  "5 13"
  "6 14"
  "7 15"
)

WAIT_SEC=0  # 설화수 = 계정 간 대기 없음

for pair in "${PAIRS[@]}"; do
  read -r IDX COMBO <<<"$pair"
  {
    echo
    echo "----- $(date '+%H:%M:%S') waiting ${WAIT_SEC}s before account #${IDX} (combo ${COMBO}) -----"
  } >> "$LOG"

  sleep "$WAIT_SEC"

  {
    echo
    echo "----- $(date '+%H:%M:%S') account #${IDX} combo ${COMBO} START -----"
  } >> "$LOG"

  LOTTE_CART_ONLY=true /opt/homebrew/bin/python3 buy/sulwhasoo.py lotte "$IDX" "$COMBO" \
    >> "$LOG" 2>&1
  RC=$?

  {
    echo "----- $(date '+%H:%M:%S') account #${IDX} END rc=${RC} -----"
  } >> "$LOG"
done

echo "===== lotte multi-account run done $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG"
