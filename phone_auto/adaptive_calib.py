"""Adaptive cam→phone transform — reference anchor 기반 매 캡쳐 보정.

흐름:
  1. 한 번의 정확한 캘리브 (manual 4-corner) 로 reference anchor 의 phone 좌표 확정 + 저장
  2. 매 캡쳐 시 OCR 결과에서 reference anchor 텍스트들 찾기
  3. 매칭된 (cam, phone) 페어 ≥3 → cv2.getPerspectiveTransform / getAffineTransform
  4. 그 transform 으로 다른 OCR text 들의 cam → phone 변환 정확

매번 카메라가 5-10% 이동/zoom 해도 transform 이 즉시 보정.

reference_anchors.json 구조:
{
  "screen_name": "hyundai_pin_login",
  "anchors": [
    {"text": "앱 잠금번호 로그인", "phone": [540, 360]},
    {"text": "혜택 금융 자산 앱카드", "phone": [540, 120]},
    {"text": "앱 잠금번호를 잊으셨나요?", "phone": [540, 1700]}
  ]
}
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

from .screen_ocr import _normalize, find_text, PROJECT_ROOT

REFERENCE_PATH = PROJECT_ROOT / "phone_auto" / "_tmp" / "reference_anchors.json"


def load_reference(name: Optional[str] = None) -> Optional[dict]:
    """저장된 reference 로드. name=None 이면 dict (multiple screens) 전체."""
    if not REFERENCE_PATH.exists():
        return None
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8", errors="replace"))
    if name is None:
        return data
    return data.get(name)


def save_reference(screen_name: str, anchors: list[dict]) -> None:
    """anchors: [{"text": str, "phone": [x, y]}, ...]"""
    REFERENCE_PATH.parent.mkdir(exist_ok=True)
    existing = {}
    if REFERENCE_PATH.exists():
        existing = json.loads(REFERENCE_PATH.read_text(encoding="utf-8", errors="replace"))
    existing[screen_name] = {"anchors": anchors}
    REFERENCE_PATH.write_text(json.dumps(existing, indent=2, ensure_ascii=False))


def build_transform_from_anchors(items: list[dict], reference: dict,
                                  min_anchors: int = 3) -> Optional[dict]:
    """현재 OCR items 에서 reference anchor 매칭 → cam→phone perspective transform.

    Returns calib dict (compatible with screen_ocr.camera_to_phone) or None.
    min_anchors 미만 매칭 → None.
    """
    import cv2
    import numpy as np

    pairs = []  # (cam_xy, phone_xy)
    for anc in reference["anchors"]:
        hit = find_text(items, anc["text"])
        if hit is None:
            continue
        pairs.append(((hit["cx"], hit["cy"]), tuple(anc["phone"])))

    if len(pairs) < min_anchors:
        return None

    src = np.float32([p[0] for p in pairs])
    dst = np.float32([p[1] for p in pairs])

    if len(pairs) == 3:
        M = cv2.getAffineTransform(src, dst)
        # affine → 4x4 like homography for unified API: extend to 3x3
        M_h = np.vstack([M, [0, 0, 1]])
    else:
        M_h, _ = cv2.findHomography(src, dst, method=0)
        if M_h is None:
            return None

    # camera_to_phone() 가 calib['tl/tr/bl/br'] 사용 → corner 4점 역계산해서 동일 API
    # cam (0,0), (W,0), (W,H), (0,H) → phone 좌표 매핑하면 자동 caliber
    # 하지만 simpler: matrix 자체를 저장 + 새 변환 함수 추가
    return {
        "method": f"adaptive_{len(pairs)}anchor",
        "matrix": M_h.tolist(),
        "n_anchors": len(pairs),
        "phone_w": 1080, "phone_h": 2400,
    }


def cam_to_phone_adaptive(cx: int, cy: int, transform: dict) -> tuple[int, int]:
    """build_transform_from_anchors 결과로 cam → phone 변환."""
    import cv2
    import numpy as np
    M = np.array(transform["matrix"], dtype=np.float32)
    pts = np.float32([[[cx, cy]]])
    out = cv2.perspectiveTransform(pts, M)
    px, py = int(out[0][0][0]), int(out[0][0][1])
    pw, ph = transform.get("phone_w", 1080), transform.get("phone_h", 2400)
    px = max(0, min(pw - 1, px))
    py = max(0, min(ph - 1, py))
    return px, py


def auto_register_reference(items: list[dict], screen_name: str,
                             calib: dict, anchor_texts: list[str]) -> dict:
    """현재 정확 캘리브 (`calib`) 상태에서 anchor_texts 의 phone 좌표 자동 측정 + 저장.

    한 번만 호출 — 캘리브가 정확한 상태에서 anchor 텍스트들의 phone 좌표를
    수확해서 reference 로 영구 저장. 이후 매 캡쳐 시 build_transform_from_anchors 로
    사용.

    Returns 저장된 anchors list.
    """
    from .screen_ocr import camera_to_phone
    anchors = []
    for text in anchor_texts:
        hit = find_text(items, text)
        if hit is None:
            print(f"[ref] '{text}' OCR 못 찾음 — skip")
            continue
        px, py = camera_to_phone(hit["cx"], hit["cy"], calib)
        anchors.append({"text": text, "phone": [px, py],
                        "cam_ref": [hit["cx"], hit["cy"]],
                        "w": hit["w"], "h": hit["h"]})
        print(f"[ref] '{text}' cam({hit['cx']},{hit['cy']}) → phone({px},{py})")
    if anchors:
        save_reference(screen_name, anchors)
        print(f"[ref] saved {len(anchors)} anchors to {REFERENCE_PATH} (screen={screen_name})")
    return anchors
