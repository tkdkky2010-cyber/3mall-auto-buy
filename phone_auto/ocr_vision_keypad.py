"""Google Vision OCR로 NH 보안 키패드 (anti-automation) 숫자 위치 매핑.

NH 가상키패드는 uiautomator dump의 content-desc로 거짓 라벨 노출 (misdirection).
시각 OCR 만이 진짜 위치 알 수 있음 (사용자 매트릭스: 셔플이지만 스샷 가능).

사용:
    from phone_auto.ocr_vision_keypad import ocr_keypad
    layout = ocr_keypad("/tmp/screen.png", roi=(50, 893, 1029, 1517))
    # → {'0': (908, 1104), '1': (...), ...}
"""
from __future__ import annotations
import os
import re
from pathlib import Path
from typing import Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(PROJECT_ROOT / "gcp-vision-key.json"))


def ocr_keypad(image_path: str, roi: Tuple[int, int, int, int]) -> dict:
    """이미지의 ROI 영역에서 단일 숫자 0-9 위치 찾기.

    Args:
        image_path: 폰 스크린샷 PNG (원본 해상도)
        roi: (x1, y1, x2, y2) 키패드 영역 원본 좌표

    Returns:
        {'0': (cx, cy), '1': ..., ...} 원본 좌표 기준 중심.
    """
    from PIL import Image
    from google.cloud import vision

    img = Image.open(image_path)
    cropped = img.crop(roi)
    import io
    buf = io.BytesIO()
    cropped.save(buf, format="PNG")
    content = buf.getvalue()

    client = vision.ImageAnnotatorClient()
    response = client.text_detection(image=vision.Image(content=content))
    if response.error.message:
        raise RuntimeError(f"Vision API: {response.error.message}")

    layout = {}
    for ann in response.text_annotations[1:]:  # [0] = full text
        txt = ann.description.strip()
        if not re.fullmatch(r"\d", txt):
            continue
        verts = ann.bounding_poly.vertices
        cx = sum(v.x for v in verts) // 4 + roi[0]
        cy = sum(v.y for v in verts) // 4 + roi[1]
        if txt in layout:
            continue  # 중복 시 첫번째 유지
        layout[txt] = (cx, cy)
    return layout


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1]
    roi = tuple(map(int, sys.argv[2:6])) if len(sys.argv) > 5 else (50, 893, 1029, 1517)
    layout = ocr_keypad(path, roi)
    print(json.dumps(layout, indent=2, ensure_ascii=False))
