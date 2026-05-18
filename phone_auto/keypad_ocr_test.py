"""보안 키패드 OCR 검증 v3 — EasyOCR (한국어 강력) + Tesseract 비교.

raw OpenCV 캡처 = 글자 정상 방향 (사용자 라이브 미리보기는 미러 효과지만 데이터 X).
flip 처리 안 함.

실행:
    python3 phone_auto/keypad_ocr_test.py

결과:
    phone_auto/_tmp/keypad_raw.png       — 원본
    phone_auto/_tmp/keypad_easyocr.png   — EasyOCR box 시각화
    phone_auto/_tmp/keypad_tess.png      — Tesseract box 시각화
"""
import cv2
import pytesseract
import easyocr
from pathlib import Path

OUT = Path(__file__).parent / "_tmp"
OUT.mkdir(exist_ok=True)
CAM_IDX = 0  # 로지텍 (Mac 내장 분리되어 0번이 됨)

# 1) 캡처 — 자동초점 안정화 위해 워밍업 강화 + 사용자 카운트다운
import time
print(f"=== 캡처 (cam{CAM_IDX}) — 3초 후 캡처, 폰 화면 + 키패드 보이게 ===")
cap = cv2.VideoCapture(CAM_IDX)
if not cap.isOpened():
    print(f"❌ cam{CAM_IDX} 안 열림")
    exit(1)
for i in range(3, 0, -1):
    print(f"  {i}...")
    time.sleep(1)
    for _ in range(5):
        cap.read()  # 초점 안정화 위해 계속 읽기
# 마지막 캡처 (가장 안정된 프레임)
for _ in range(10):
    cap.read()
ok, frame = cap.read()
cap.release()
if not ok:
    print("❌ 프레임 X")
    exit(1)
raw_path = OUT / "keypad_raw.png"
cv2.imwrite(str(raw_path), frame)
print(f"  → {raw_path} ({frame.shape[1]}x{frame.shape[0]}, raw 그대로)")

# 2) EasyOCR (한국어 + 영문 + 숫자) — Tesseract 대안, 정확도 ↑
print("\n=== EasyOCR (ko+en, 첫 실행은 모델 다운로드 ~50MB) ===")
reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
results = reader.readtext(str(raw_path))
print(f"인식: {len(results)} items")
for bbox, text, conf in results:
    # bbox = [(x1,y1), (x2,y1), (x2,y2), (x1,y2)] — 4 꼭짓점
    x1, y1 = bbox[0]
    x3, y3 = bbox[2]
    cx, cy = int((x1 + x3) / 2), int((y1 + y3) / 2)
    print(f"  '{text}' conf={conf:.2f} center=({cx}, {cy})")

# 시각화 — EasyOCR
viz = frame.copy()
for bbox, text, conf in results:
    if conf < 0.3:
        continue
    pts = [(int(p[0]), int(p[1])) for p in bbox]
    for i in range(4):
        cv2.line(viz, pts[i], pts[(i + 1) % 4], (0, 255, 0), 2)
    cv2.putText(viz, f"{text}({conf:.2f})", pts[0],
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
viz_easyocr = OUT / "keypad_easyocr.png"
cv2.imwrite(str(viz_easyocr), viz)
print(f"시각화 → {viz_easyocr}")

# 3) Tesseract 비교용 (psm=11, 숫자만)
print("\n=== Tesseract (psm=11, 숫자만) ===")
gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
gray_2x = cv2.resize(gray, (gray.shape[1] * 2, gray.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
data = pytesseract.image_to_data(
    gray_2x,
    config="--psm 11 -c tessedit_char_whitelist=0123456789",
    output_type=pytesseract.Output.DICT,
)
tess_hits = 0
for i, t in enumerate(data["text"]):
    t = t.strip()
    if not t:
        continue
    try:
        conf = int(data["conf"][i])
    except (ValueError, TypeError):
        conf = -1
    if conf < 30:
        continue
    x, y = data["left"][i] // 2, data["top"][i] // 2
    w, h = data["width"][i] // 2, data["height"][i] // 2
    cx, cy = x + w // 2, y + h // 2
    print(f"  '{t}' conf={conf:3d} center=({cx}, {cy})")
    tess_hits += 1
print(f"→ Tesseract: {tess_hits} items")
