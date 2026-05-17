"""웹캠 인덱스 자동 탐색 + 1프레임 캡처.

macOS 카메라 권한 필요: 시스템 설정 → 개인정보 → 카메라 → Terminal 허용.

실행:
    python3 phone_auto/camera_probe.py

결과:
    phone_auto/_tmp/cam{i}_frame.png  — 각 인덱스별 1프레임 저장
    stdout 에 인덱스 + 해상도 + 저장 경로
"""
import cv2
from pathlib import Path

OUT = Path(__file__).parent / "_tmp"
OUT.mkdir(exist_ok=True)

print("=== 카메라 인덱스 탐색 (0~3) ===")
for i in range(4):
    cap = cv2.VideoCapture(i)
    if not cap.isOpened():
        print(f"  index {i}: 안 열림")
        cap.release()
        continue
    # 워밍업 (몇 프레임 버려야 안정적 캡처)
    for _ in range(3):
        cap.read()
    ok, frame = cap.read()
    if not ok or frame is None:
        print(f"  index {i}: 열림 but 프레임 X")
        cap.release()
        continue
    h, w = frame.shape[:2]
    out_path = OUT / f"cam{i}_frame.png"
    cv2.imwrite(str(out_path), frame)
    print(f"  index {i}: {w}x{h} → {out_path}")
    cap.release()

print("\n사진 확인 후 어느 인덱스가 로지텍 웹캠인지 알려주세요.")
