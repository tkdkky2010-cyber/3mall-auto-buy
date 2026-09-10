"""계정당 로그인 시도 상한 (사용자 지시 2026-09-10).

> "아이디 로그인은 2회까지만 시도해, 만약 해당 아이디 로그인 2회 실패하면
>  해당 계정은 더이상 로그인시도하지마"

왜 파일에 남기나: 상한을 함수 안에서만 세면 **스크립트를 다시 돌릴 때마다 0부터** 센다.
9/10 에 조회를 세 번 나눠 돌려 계정당 4~5회 로그인시켰고 #10·#11 이 차단됐다.
날짜가 바뀌면 자동으로 초기화된다.
"""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

STATE = Path(__file__).parent / "logs" / "hmall_login_attempts.json"
MAX_ATTEMPTS = 2


def _load() -> dict:
    try:
        d = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return d if d.get("date") == date.today().isoformat() else {"date": date.today().isoformat(), "fail": {}}


def blocked(account_id: str) -> bool:
    """2회 실패한 계정 = 오늘은 더 시도하지 않는다."""
    return _load()["fail"].get(account_id, 0) >= MAX_ATTEMPTS


def record(account_id: str, ok: bool) -> int:
    """성공하면 카운트 리셋, 실패하면 +1. 남은 실패 카운트를 돌려준다."""
    d = _load()
    d["fail"][account_id] = 0 if ok else d["fail"].get(account_id, 0) + 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d["fail"][account_id]
