"""웹캠 인덱스 탐색 — 0~5 전부, 검은화면 자동 제외, 평균 밝기 표시.

macOS Continuity Camera (iPhone) 가 카메라 목록에 자동 추가되어 인덱스 충돌 발생.
실제 캡처 + 평균 밝기로 살아있는 카메라 (NV76-CM400A 같은 외부 웹캠) 판별.

⚠️ iPhone Continuity Camera 비활성화 권장:
   iPhone 설정 → 일반 → AirPlay 및 연속성 → "연속성 카메라" OFF
   또는 Mac 시스템 설정 → 일반 → AirDrop 및 Handoff → 같은 항목 OFF

실행:
    python3 phone_auto/camera_probe.py
"""
import cv2
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent / "_tmp"
OUT.mkdir(exist_ok=True)

print("=== macOS 카메라 디바이스 (참고) ===")
import subprocess
res = subprocess.run(["system_profiler", "SPCameraDataType"],
                     capture_output=True, text=True)
for line in res.stdout.split("\n"):
    if line.strip() and not line.startswith("Camera:"):
        print(f"  {line}")

print("\n=== OpenCV 카메라 인덱스 탐색 (0~5) ===")
candidates = []
for i in range(6):
    cap = cv2.VideoCapture(i)
    if not cap.isOpened():
        cap.release()
        continue
    for _ in range(5):
        cap.read()
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        continue
    h, w = frame.shape[:2]
    brightness = float(np.mean(frame))
    out_path = OUT / f"cam{i}_frame.png"
    cv2.imwrite(str(out_path), frame)
    flag = "✓ 살아있음" if brightness > 20 else "✗ 검은화면 (iPhone? 가려짐?)"
    print(f"  index {i}: {w}x{h} brightness={brightness:.1f} {flag} → {out_path}")
    candidates.append((i, brightness, w, h))

print()
alive = [c for c in candidates if c[1] > 20]
if alive:
    best = max(alive, key=lambda c: c[1])
    print(f"✓ 추천 인덱스: cam{best[0]} ({best[2]}x{best[3]}, brightness={best[1]:.1f})")
else:
    print("⚠️ 살아있는 카메라 없음")
