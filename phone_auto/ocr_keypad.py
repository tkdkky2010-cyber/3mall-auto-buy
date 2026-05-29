"""4엔진 OCR voting — 보안 키패드 0~9 위치 매핑.

엔진:
  - EasyOCR (한/영, 로컬)
  - Tesseract (psm=11, 숫자 only, 로컬)
  - macOS Vision Framework (PyObjC, 로컬)
  - Google Cloud Vision API (네트워크)

처리 흐름:
  1. 각 엔진이 발견한 (digit, cx, cy, conf) 수집
  2. 좌표 기준 cluster (거리 < dist_threshold)
  3. cluster 당 majority vote (digit 별 detection 수, tiebreak avg conf)
  4. 검증: 0~9 모두 distinct cluster 에 매핑

사용:
  from phone_auto.ocr_keypad import vote_digits
  digit_map = vote_digits("phone_auto/_tmp/keypad_raw.png")
  # → {'0': (367, 864), '1': (577, 788), ..., '9': (381, 1179)} or None

CLI (단일 이미지 테스트):
  python3 phone_auto/ocr_keypad.py phone_auto/_tmp/keypad_raw.png
"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from collections import defaultdict
from typing import Optional

# buy/.env 의 GOOGLE_APPLICATION_CREDENTIALS 로드
PROJECT_ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / "buy" / ".env")
except ImportError:
    pass

# ─── 카드사별 키패드 preset ───
# 카메라: Continuity Camera (Jason's iPhone15,3), 1920x1080, cam0
# 카메라 거치 위치 변경 시 roi_y_frac 재측정 필요.
KEYPAD_PRESETS: dict[str, dict] = {
    "hyundai_code7": {
        "flip_h": False,
        "roi_y_frac": (0.59, 0.95),
        "layout": "fixed_3x4",      # 1-9 + 0(row4 중앙). 셔플 X.
        "n_digits_pin": 7,
        "description": "현대카드 7자리 결제 보안번호 (PC 결제 로그인)",
    },
    "hyundai_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.74, 0.99),
        "layout": "shuffled_3x4",   # 셔플 — 매번 OCR 필수.
        "n_digits_pin": 6,
        "description": "현대카드 6자리 결제 비밀번호 (현대 PIN)",
    },
    "hyundai_hmall_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.33, 0.58),  # 5/29 ADB screencap 실측 10/10 (화면 중앙 키패드). 0.34~0.56 코어.
        "layout": "fixed_3x4",       # 1-9 + 0(row4 중앙). 셔플 X — 좌표만 OCR로 확정([[feedback_phone_coord_no_estimate]]).
        "n_digits_pin": 6,
        "description": "현대몰(Hmall) PIN번호 결제 6자리 (고정 키패드, ADB screencap, 카메라 X)",
    },
    "hyundai_hmall_pw4": {
        "flip_h": False,
        "roi_y_frac": (0.35, 0.61),  # 5/29 ADB screencap 실측 10/10 (본인인증 카드비번, PIN보다 살짝 아래).
        "layout": "fixed_3x4",       # 1-9 + 0(row4 중앙). 셔플 X.
        "n_digits_pin": 4,
        "description": "현대몰(Hmall) 본인인증 카드비밀번호 4자리 (고정 키패드, ADB screencap, 카메라 X)",
    },
    "hana_code7": {
        "flip_h": False,
        "roi_y_frac": (0.50, 0.95),
        "layout": "fixed_3x4",      # 1-9 + 0(row4 중앙). 셔플 X.
        "n_digits_pin": 7,
        "description": "하나카드 7자리 결제 인증코드",
    },
    "hana_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.62, 0.95),  # 5/29 실측: 키패드 3행 y≈1619/1787/1953 (frac 0.675~0.815) + 4행 재배열. 4엔진 10/10.
        "layout": "shuffled_4x3_with_logos",  # 4 cols × 3 rows, 12 cells 중 2개는 회색 하나페이 로고 decoy(숫자 없음).
        "n_digits_pin": 6,
        "description": "하나몰(Hmall) 하나Pay 간편결제 6자리 (com.hanaskcard.paycla nFilter, screencap 읽힘=ADB, 카메라 X)",
    },
    "nh_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.67, 0.95),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows (롯데 LOCA Pay 와 동일 구조). row 4 가운데 1 digit + 양쪽 재배열/삭제.
        "n_digits_pin": 6,
        "description": "농협카드 6자리 결제 비밀번호 (파란 키패드, 롯데 LOCA Pay 와 유사)",
    },
    "nh_code7": {
        "flip_h": False,
        "roi_y_frac": (0.28, 0.65),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows, row 4 가운데 1 digit + 양쪽 버튼. 키패드가 화면 상단에 있음 (다른 카드와 정반대).
        "n_digits_pin": 7,
        "description": "농협카드 7자리 결제코드 (화면 상단 키패드, 셔플)",
    },
    "kb_code7": {
        "flip_h": False,
        "roi_y_frac": (0.55, 0.88),
        "layout": "fixed_3x4",  # 표준 1-9 + 0(row4 중앙).
        "n_digits_pin": 7,
        "description": "KB Pay 7자리 결제 인증코드 (보라/연분홍 배경)",
    },
    "kb_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.70, 0.90),
        "layout": "shuffled_4x3",  # 4 cols × 3 rows. row 2 cols 1, 2 = ← 삭제 / 입력완료 버튼.
        "n_digits_pin": 6,
        "description": "KB Pay 6자리 결제 비밀번호 (보라 배경, 4x3 셔플)",
    },
    "samsung_code7": {
        "flip_h": False,
        "roi_y_frac": (0.55, 0.95),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows, row 4 가운데 1 digit + 양쪽 버튼.
        "n_digits_pin": 7,
        "description": "삼성 Pay 7자리 결제 보안번호 (셔플)",
    },
    "samsung_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.55, 0.95),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows, row 4 가운데 1 digit + 양쪽 버튼.
        "n_digits_pin": 6,
        "description": "삼성 Pay 6자리 결제 비밀번호 (셔플, 7자리와 동일 위치/구조)",
    },
    "bc_login6": {  # 5/20 rename from bc_pin6 — 결제 비번 아니라 앱 로그인 비번
        "flip_h": False,
        "roi_y_frac": (0.71, 0.92),
        "layout": "shuffled_4x3",  # 4 cols × 3 rows, row 2 cols 1,2 = 빈 영역 (KB PIN6 와 유사 구조).
        "n_digits_pin": 6,
        "description": "BC 카드 앱 로그인 6자리 비밀번호 (회색/보라 배경, 셔플) — 결제 비번 별도",
    },
    "bc_code7": {
        "flip_h": False,
        "roi_y_frac": (0.54, 0.88),
        "layout": "fixed_3x4",  # 표준 1-9 + 0(row4 중앙). KB Pay 7자리와 동일 위치/구조.
        "n_digits_pin": 7,
        "description": "BC 카드 7자리 결제코드 (보라/연분홍 배경, 고정)",
    },
    "bc_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.65, 0.95),
        "layout": "shuffled_4x3",  # 4 cols × 3 rows, 빈 셀 2개 random (BC 로그인과 동일 구조).
        "n_digits_pin": 6,
        "description": "BC 카드 결제 6자리 비밀번호 (보라 배경, 셔플) — 로그인 비번 별도 (bc_login6)",
    },
    "lotte_code7": {
        "flip_h": False,
        "roi_y_frac": (0.62, 0.95),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows, row 4 가운데 1 digit + 양쪽 버튼. NH7 과 동일 구조.
        "n_digits_pin": 7,
        "description": "롯데카드 LOCA Pay 7자리 결제 인증코드 (파란 배경, 셔플)",
    },
    "lotte_pin6": {
        "flip_h": False,
        "roi_y_frac": (0.55, 0.95),
        "layout": "shuffled_3x4",  # 3 cols × 4 rows, row 4 가운데 1 digit + 양쪽 버튼. lotte_code7 과 동일 구조.
        "n_digits_pin": 6,
        "description": "롯데카드 LOCA Pay 6자리 결제 비밀번호 (파란 배경, 셔플)",
    },
}


def vote_digits_for_card(
    card_key: str,
    image_path: str | Path | None = None,
    cam_idx: int = 0,
    **vote_kwargs,
) -> Optional[dict[str, tuple[int, int]]]:
    """Preset 적용해서 OCR. image_path None 이면 live 캡쳐."""
    if card_key not in KEYPAD_PRESETS:
        raise ValueError(f"unknown card_key: {card_key}. Available: {sorted(KEYPAD_PRESETS.keys())}")
    preset = KEYPAD_PRESETS[card_key]
    if image_path is None:
        image_path = capture_frame(cam_idx, warmup_frames=15)
        if not image_path:
            return None
    return vote_digits(
        image_path,
        flip_h=preset["flip_h"],
        roi_y_frac=preset["roi_y_frac"],
        **vote_kwargs,
    )


# Lazy globals — 엔진 별 1회만 init
_easyocr_reader = None
_pytesseract = None
_gcv_client = None


def _load_easyocr():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _easyocr_reader


def _load_pytesseract():
    global _pytesseract
    if _pytesseract is None:
        import pytesseract
        _pytesseract = pytesseract
    return _pytesseract


def _load_gcv():
    global _gcv_client
    if _gcv_client is None:
        from google.cloud import vision
        _gcv_client = vision.ImageAnnotatorClient()
    return _gcv_client


# ─── Engine wrappers — 각각 [(digit, cx, cy, conf), ...] 반환 ───

def _ocr_easyocr(img_path: str) -> list[tuple[str, int, int, float]]:
    reader = _load_easyocr()
    results = reader.readtext(img_path, allowlist="0123456789", paragraph=False)
    out = []
    for bbox, text, conf in results:
        text = (text or "").strip()
        # 멀티 digit (예: "2224") 도 포함 — 각 문자 분리해서 같은 center 좌표 부여
        if not text:
            continue
        x1, y1 = bbox[0]
        x3, y3 = bbox[2]
        cx, cy = int((x1 + x3) / 2), int((y1 + y3) / 2)
        for ch in text:
            if ch.isdigit():
                out.append((ch, cx, cy, float(conf)))
    return out


def _ocr_tesseract(img_path: str) -> list[tuple[str, int, int, float]]:
    import cv2
    pt = _load_pytesseract()
    img = cv2.imread(img_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray_2x = cv2.resize(gray, (gray.shape[1] * 2, gray.shape[0] * 2),
                         interpolation=cv2.INTER_CUBIC)
    # numpy array 직접 전달 시 pytesseract↔py3.13 에서 'utf-8 0x89'(PNG) UnicodeDecodeError 발생 →
    # 임시 PNG 파일 경로로 우회 (5/29 fix). 4엔진 voting 복구용.
    _tmp2x = "/tmp/_tess_gray2x.png"
    cv2.imwrite(_tmp2x, gray_2x)
    data = pt.image_to_data(
        _tmp2x,
        config="--psm 11 -c tessedit_char_whitelist=0123456789",
        output_type=pt.Output.DICT,
    )
    out = []
    for i, t in enumerate(data["text"]):
        t = (t or "").strip()
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
        for ch in t:
            if ch.isdigit():
                out.append((ch, cx, cy, conf / 100.0))
    return out


def _ocr_macos_vision(img_path: str) -> list[tuple[str, int, int, float]]:
    import Vision
    import Quartz
    from Foundation import NSURL
    url = NSURL.fileURLWithPath_(img_path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if src is None:
        return []
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        return []
    w = Quartz.CGImageGetWidth(cg)
    h = Quartz.CGImageGetHeight(cg)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(["en-US"])
    handler.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)[0]
        text = (cand.string() or "").strip()
        if not text:
            continue
        bb = obs.boundingBox()
        x_off = bb.origin.x * w
        y_off = (1 - bb.origin.y - bb.size.height) * h
        bw = bb.size.width * w
        bh = bb.size.height * h
        cx, cy = int(x_off + bw / 2), int(y_off + bh / 2)
        conf = float(cand.confidence())
        for ch in text:
            if ch.isdigit():
                out.append((ch, cx, cy, conf))
    return out


def _ocr_gcv(img_path: str) -> list[tuple[str, int, int, float]]:
    from google.cloud import vision
    client = _load_gcv()
    with open(img_path, "rb") as f:
        image = vision.Image(content=f.read())
    response = client.text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"GCV: {response.error.message}")
    out = []
    # text_annotations[0] = 전체 텍스트, [1:] = 단어별
    for t in response.text_annotations[1:]:
        text = (t.description or "").strip()
        if not text:
            continue
        verts = t.bounding_poly.vertices
        xs = [v.x for v in verts]
        ys = [v.y for v in verts]
        cx = sum(xs) // len(xs)
        cy = sum(ys) // len(ys)
        for ch in text:
            if ch.isdigit():
                out.append((ch, cx, cy, 1.0))  # GCV 단어 confidence 미공개
    return out


# ─── 키패드 ROI 검출 (파란 영역) ───

def _detect_keypad_roi(img_path: str, fallback_y_frac: Optional[tuple[float, float]] = None) -> Optional[tuple[int, int, int, int]]:
    """Lotte LOCA Pay 키패드 = 큰 파란 사각형. HSV 필터 → 최대 contour bounding rect.

    파란 키패드 못 찾을 시:
      - fallback_y_frac 명시: 그 Y 비율 범위를 ROI 로 사용
      - 명시 X (None): None 반환 → vote_digits 가 ROI 필터 없이 진행 (UI 오탐 위험)

    카드사별 ROI 정해지면 caller 가 명시. 아직 카드사 confirm 전이라 하드코딩 X.

    Returns (x, y, w, h) in pixel coords, or None if not found.
    """
    import cv2
    import numpy as np
    img = cv2.imread(img_path)
    if img is None:
        return None
    H, W = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 파란 범위 (LOCA Pay 키패드)
    lower = np.array([95, 80, 80])
    upper = np.array([130, 255, 255])
    mask = cv2.inRange(hsv, lower, upper)
    # gap fill (digit 흰색 영역 메우기)
    kernel = np.ones((25, 25), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        biggest = max(contours, key=cv2.contourArea)
        area = cv2.contourArea(biggest)
        if area >= W * H * 0.08:
            x, y, w, h = cv2.boundingRect(biggest)
            # ROI 상단 ~18% = PIN dot row + "숫자코드 입력" 라벨 영역 → 제외
            cut = int(h * 0.18)
            return (x, y + cut, w, h - cut)
    # 폴백 — 파란 키패드 검출 실패 + caller 가 Y 비율 명시한 경우만
    if fallback_y_frac is None:
        return None
    y_top = int(H * fallback_y_frac[0])
    y_bot = int(H * fallback_y_frac[1])
    return (0, y_top, W, y_bot - y_top)


def _filter_detections_by_roi(
    detections: list[tuple[str, str, int, int, float]],
    roi: tuple[int, int, int, int],
) -> list[tuple[str, str, int, int, float]]:
    x, y, w, h = roi
    return [d for d in detections if x <= d[2] < x + w and y <= d[3] < y + h]


# ─── 좌표 cluster + majority vote ───

def _cluster_detections(
    detections: list[tuple[str, str, int, int, float]],
    dist_threshold: int,
) -> list[dict]:
    """detections: [(engine, digit, cx, cy, conf), ...] → clusters."""
    clusters: list[dict] = []
    for engine, digit, cx, cy, conf in detections:
        placed = False
        for cl in clusters:
            ccx, ccy = cl["center"]
            if abs(cx - ccx) < dist_threshold and abs(cy - ccy) < dist_threshold:
                cl["detections"].append((engine, digit, conf))
                n = len(cl["detections"])
                cl["center"] = (
                    int((ccx * (n - 1) + cx) / n),
                    int((ccy * (n - 1) + cy) / n),
                )
                placed = True
                break
        if not placed:
            clusters.append({"center": (cx, cy), "detections": [(engine, digit, conf)]})
    return clusters


def _vote_cluster(cluster: dict) -> tuple[str, float, dict]:
    """cluster 의 detections 에서 best digit + 표 분포 반환.

    Returns (best_digit, avg_conf, vote_dist: {digit: count}).
    """
    votes_by_digit: dict[str, list[float]] = defaultdict(list)
    for _engine, digit, conf in cluster["detections"]:
        votes_by_digit[digit].append(conf)
    vote_dist = {d: len(v) for d, v in votes_by_digit.items()}
    # max count → tiebreak avg confidence
    best_digit = max(
        votes_by_digit,
        key=lambda d: (len(votes_by_digit[d]), sum(votes_by_digit[d]) / len(votes_by_digit[d])),
    )
    avg_conf = sum(votes_by_digit[best_digit]) / len(votes_by_digit[best_digit])
    return best_digit, avg_conf, vote_dist


def vote_digits(
    image_path: str | Path,
    dist_threshold: int = 60,
    engines: tuple[str, ...] = ("easyocr", "tesseract", "vision", "gcv"),
    min_width: int = 720,
    flip_h: bool = False,
    roi_y_frac: Optional[tuple[float, float]] = None,
    verbose: bool = False,
    allow_partial: bool = False,
) -> Optional[dict[str, tuple[int, int]]]:
    """4엔진 union voting. 0~9 distinct cluster 매핑 성공 시 dict, 실패 시 None.

    flip_h=False (default): ADB screencap(폰 인앱 결제) = 미러 없음, 반전 안 함.
    Continuity Camera 의 미러 라이브 미리보기를 OCR 할 때만 True (좌우 반전).
    현재 전 카드 ADB screencap 이라 flip_h=False 가 정상. (preset 도 전부 flip_h=False)

    Auto-upscale: image width < min_width 면 2배 업스케일 후 OCR (저해상도 보강).
    좌표는 원본(또는 flip 적용 후) 좌표공간으로 반환.

    Returns {'0': (cx, cy), '1': (cx, cy), ..., '9': (cx, cy)} or None.
    """
    import cv2
    image_path = str(image_path)
    img = cv2.imread(image_path)
    if img is None:
        return None
    orig_width = img.shape[1]  # flip 환원용 보관
    # flip 적용 (Continuity 라이브 미러 효과 되돌리기)
    if flip_h:
        img = cv2.flip(img, 1)
    # auto-upscale
    work_dir = Path(__file__).resolve().parent / "_tmp"
    work_dir.mkdir(exist_ok=True)
    scale = 1.0
    if img.shape[1] < min_width:
        scale = min_width / img.shape[1]
        new_w, new_h = int(img.shape[1] * scale), int(img.shape[0] * scale)
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        if verbose:
            print(f"[upscale] {int(img.shape[1]/scale)}→{new_w} 2x cubic")
    if flip_h or scale != 1.0:
        tmp_path = work_dir / f"_processed_{Path(image_path).name}"
        cv2.imwrite(str(tmp_path), img)
        image_path = str(tmp_path)
    fn_map = {
        "easyocr": _ocr_easyocr,
        "tesseract": _ocr_tesseract,
        "vision": _ocr_macos_vision,
        "gcv": _ocr_gcv,
    }

    detections: list[tuple[str, str, int, int, float]] = []
    for engine_name in engines:
        fn = fn_map.get(engine_name)
        if not fn:
            continue
        try:
            results = fn(image_path)
            if verbose:
                print(f"[{engine_name}] {len(results)} detections")
            for digit, cx, cy, conf in results:
                detections.append((engine_name, digit, cx, cy, conf))
        except Exception as e:
            print(f"[WARN] {engine_name} 실패: {e}", file=sys.stderr)

    # ★ 키패드 영역 ROI 검출 + UI 영역 detection 제거
    roi = _detect_keypad_roi(image_path, fallback_y_frac=roi_y_frac)
    # 자동 ROI 가 너무 넓으면 roi_y_frac 으로 추가 narrowing (intersection)
    if roi and roi_y_frac:
        img_h = img.shape[0]
        rx, ry, rw, rh = roi
        ry2 = ry + rh
        nry = max(ry, int(img_h * roi_y_frac[0]))
        nry2 = min(ry2, int(img_h * roi_y_frac[1]))
        if nry2 > nry:
            roi = (rx, nry, rw, nry2 - nry)
    if roi:
        before = len(detections)
        detections = _filter_detections_by_roi(detections, roi)
        if verbose:
            print(f"ROI={roi}, detection filter: {before} → {len(detections)}")
    elif verbose:
        print("[WARN] 키패드 ROI 검출 실패 — 전체 영역 사용 (UI 오탐 위험)")

    clusters = _cluster_detections(detections, dist_threshold)

    # cluster 당 best digit
    digit_to_clusters: dict[str, list[tuple[tuple[int, int], float, dict]]] = defaultdict(list)
    for cl in clusters:
        digit, conf, vote_dist = _vote_cluster(cl)
        digit_to_clusters[digit].append((cl["center"], conf, vote_dist))

    # 같은 digit 이 여러 cluster 에 매핑된 경우 → 가장 voting 강한 cluster 선택
    digit_map: dict[str, tuple[int, int]] = {}
    for digit, items in digit_to_clusters.items():
        # 강도: sum(vote_dist[digit]) (= 그 digit 에 투표한 엔진 수)
        best = max(items, key=lambda x: (x[2].get(digit, 0), x[1]))
        digit_map[digit] = best[0]

    if verbose:
        print(f"\nclusters: {len(clusters)}, mapped digits: {sorted(digit_map.keys())}")
        for d in sorted(digit_map):
            print(f"  {d} → {digit_map[d]}")

    expected = set("0123456789")
    found = set(digit_map.keys())
    if expected != found:
        if verbose:
            missing = sorted(expected - found)
            extra = sorted(found - expected)
            print(f"❌ 검증 실패 — 누락 {missing}, 추가 {extra}")
        if not allow_partial:
            return None
        # allow_partial 시 partial map 반환 — 빈 cluster 도 union 에 활용
        if verbose:
            print(f"[partial] {len(found)}/10 매핑 반환")
    # 좌표는 flipped (un-mirrored = 실제 폰 화면) + 원본 사이즈 공간으로 반환
    if scale != 1.0:
        digit_map = {d: (int(x / scale), int(y / scale)) for d, (x, y) in digit_map.items()}
    return digit_map


def capture_frame(cam_idx: int = 0, warmup_frames: int = 10,
                  out_path: str | Path | None = None) -> Optional[str]:
    """웹캠 1프레임 캡처 (자동초점 안정화 위해 warmup_frames 만큼 throwaway).

    Returns saved PNG path or None on failure.
    """
    import cv2
    cap = cv2.VideoCapture(cam_idx)
    if not cap.isOpened():
        return None
    try:
        for _ in range(warmup_frames):
            cap.read()
        ok, frame = cap.read()
        if not ok or frame is None:
            return None
        if out_path is None:
            tmp = Path(__file__).resolve().parent / "_tmp"
            tmp.mkdir(exist_ok=True)
            out_path = tmp / "frame.png"
        cv2.imwrite(str(out_path), frame)
        return str(out_path)
    finally:
        cap.release()


def vote_digits_multi_frame(
    cam_idx: int = 0,
    n_frames: int = 3,
    interval_sec: float = 0.5,
    pos_tolerance: int = 25,
    verbose: bool = False,
) -> Optional[dict[str, tuple[int, int]]]:
    """n_frames 캡처 → 각 vote_digits → 좌표 ±pos_tolerance 일관성 검증.

    실패 조건:
      - 어느 프레임이라도 vote_digits None
      - digit 좌표가 프레임 간 pos_tolerance 초과 차이

    Returns 평균 digit_map or None.
    """
    import time
    tmp_dir = Path(__file__).resolve().parent / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    frame_results: list[dict] = []
    for i in range(n_frames):
        path = tmp_dir / f"mf_frame_{i}.png"
        if not capture_frame(cam_idx, warmup_frames=(10 if i == 0 else 3), out_path=path):
            if verbose:
                print(f"[mf] frame {i} 캡처 실패")
            return None
        result = vote_digits(path, verbose=False)
        if result is None:
            if verbose:
                print(f"[mf] frame {i} vote_digits None")
            return None
        frame_results.append(result)
        if i < n_frames - 1:
            time.sleep(interval_sec)

    # 일관성: 각 digit 의 좌표 max-min 차이 < pos_tolerance
    avg: dict[str, tuple[int, int]] = {}
    for d in "0123456789":
        xs = [fr[d][0] for fr in frame_results]
        ys = [fr[d][1] for fr in frame_results]
        if max(xs) - min(xs) > pos_tolerance or max(ys) - min(ys) > pos_tolerance:
            if verbose:
                print(f"[mf] digit {d} 좌표 불일치: xs={xs}, ys={ys}")
            return None
        avg[d] = (int(sum(xs) / len(xs)), int(sum(ys) / len(ys)))

    if verbose:
        print(f"[mf] {n_frames} 프레임 일관성 OK, 평균 좌표 반환")
    return avg


def count_pin_dots(image_path: str | Path, expected_total: int = 6) -> Optional[int]:
    """PIN 입력 진행 dot 개수 (채워진 ● 개수) 검출.

    LOCA Pay: 키패드 위 6개 dot (or 4 — 카드 비번 자리수에 따라).
    채워진 dot = 밝은 흰색, 빈 dot = 어두운 윤곽선.

    HoughCircles 로 후보 검출 → 중심 밝기 임계로 채워짐 판정.

    Returns filled dot count, or None if circle detection failed.
    """
    import cv2
    import numpy as np
    img = cv2.imread(str(image_path))
    if img is None:
        return None
    H, W = img.shape[:2]
    # 키패드 ROI 위쪽 ~150px 영역 (dot 영역 추정)
    roi_rect = _detect_keypad_roi(str(image_path))
    if roi_rect:
        x, y, w, h = roi_rect
        # dot 영역: 키패드 상단 (y ~ y+150)
        y_top = max(0, y)
        y_bot = min(H, y + 200)
        crop = img[y_top:y_bot, x:x + w]
    else:
        crop = img[: H // 3]

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1, minDist=30,
        param1=80, param2=15, minRadius=6, maxRadius=20,
    )
    if circles is None:
        return None
    circles = np.round(circles[0, :]).astype("int")
    # 가로 정렬 (왼쪽→오른쪽) + 가장 일정한 y 줄만 선택 (median y ±15)
    if len(circles) >= expected_total:
        ys = circles[:, 1]
        med_y = int(np.median(ys))
        circles = circles[np.abs(ys - med_y) < 20]
    # 채워짐 판정: 중심 5x5 영역 평균 밝기 > 임계
    filled = 0
    for cx, cy, r in circles:
        cx, cy = int(cx), int(cy)
        patch = gray[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3]
        if patch.size == 0:
            continue
        if patch.mean() > 180:  # 채워진 흰색 dot
            filled += 1
    return filled


def verify_pin_progress(cam_idx: int = 0, expected_filled: int = 1) -> bool:
    """클릭 후 캡처 → count_pin_dots → expected_filled 와 일치 여부.

    호출자가 PIN 입력 자리마다 (1자리 클릭 → verify_pin_progress(1) → 다음...) 사용.
    """
    path = capture_frame(cam_idx, warmup_frames=3)
    if not path:
        return False
    count = count_pin_dots(path)
    if count is None:
        return False
    return count == expected_filled


def _max_drift(curr: dict[str, tuple[int, int]],
               prev: dict[str, tuple[int, int]]) -> int:
    """두 digit_map 의 좌표 최대 픽셀 drift. 공통 digit 만 비교."""
    drifts = []
    for d in "0123456789":
        if d in curr and d in prev:
            x1, y1 = curr[d]
            x2, y2 = prev[d]
            drifts.append(max(abs(x1 - x2), abs(y1 - y2)))
    return max(drifts) if drifts else 0


def vote_digits_safe(
    cam_idx: int = 0,
    n_frames: int = 3,
    intra_frame_tolerance_px: int = 25,
    prev_digit_map: Optional[dict[str, tuple[int, int]]] = None,
    drift_tolerance_px: int = 15,
    max_retries: int = 3,
    verbose: bool = False,
    **vote_kwargs,
) -> Optional[dict[str, tuple[int, int]]]:
    """3중 안전망 통합 — multi-frame + drift 검증 + 재시도.

    1. n_frames 캡쳐 → 각 vote_digits → intra-frame 좌표 ±intra_frame_tolerance_px
    2. prev_digit_map 있으면 → 결과 좌표 vs prev 의 max drift ≤ drift_tolerance_px
    3. 위 검증 실패 시 max_retries 회까지 다시 캡쳐 + 검증

    Returns 평균 좌표 digit_map or None.
    """
    for attempt in range(max_retries + 1):
        result = vote_digits_multi_frame(
            cam_idx=cam_idx,
            n_frames=n_frames,
            pos_tolerance=intra_frame_tolerance_px,
            verbose=verbose,
        )
        if result is None:
            if verbose:
                print(f"[safe] attempt {attempt}: multi-frame 실패")
            continue
        if prev_digit_map is None:
            if verbose:
                print(f"[safe] attempt {attempt}: prev 없음 → 결과 채택")
            return result
        drift = _max_drift(result, prev_digit_map)
        if drift <= drift_tolerance_px:
            if verbose:
                print(f"[safe] attempt {attempt}: drift={drift} ≤ {drift_tolerance_px} → OK")
            return result
        if verbose:
            print(f"[safe] attempt {attempt}: drift={drift} > {drift_tolerance_px} → 재시도")
    if verbose:
        print(f"[safe] {max_retries} 회 재시도 실패")
    return None


def click_pin_with_verification(
    pin: str,
    digit_map: dict[str, tuple[int, int]],
    click_fn,  # callable(x, y) → None — ESP32 클릭 함수
    cam_idx: int = 0,
    inter_click_delay: float = 0.4,
    verify_dots: bool = True,
    raise_on_dot_mismatch: bool = True,
) -> bool:
    """PIN 자릿수 순차 클릭 + 각 클릭 후 PIN dot 개수 검증.

    각 자릿수 입력 후 dot 카운트 비교:
      - expected = i+1 (i 번째 자릿수 클릭 후 i+1 개 채워짐)
      - 안 맞으면 raise_on_dot_mismatch 따라 즉시 abort or 경고 후 계속

    Returns True (모든 클릭 + 검증 성공) or False.
    """
    import time
    clicks = pin_to_clicks(pin, digit_map)
    for i, (x, y) in enumerate(clicks):
        click_fn(x, y)
        time.sleep(inter_click_delay)
        if verify_dots:
            from .ocr_keypad import capture_frame, count_pin_dots
            tmp = capture_frame(cam_idx, warmup_frames=3)
            if not tmp:
                if raise_on_dot_mismatch:
                    raise RuntimeError(f"capture 실패 at digit {i+1}")
                return False
            filled = count_pin_dots(tmp)
            if filled != i + 1:
                msg = f"클릭 {i+1}자리 후 dot={filled} (예상 {i+1})"
                if raise_on_dot_mismatch:
                    raise RuntimeError(msg)
                print(f"[WARN] {msg}")
                return False
    return True


def pin_to_clicks(pin: str, digit_map: dict[str, tuple[int, int]]) -> list[tuple[int, int]]:
    """PIN 문자열 (예: '1357') + digit_map → 클릭 좌표 시퀀스.

    Raises ValueError 시 매핑 누락 또는 PIN 에 비숫자.
    """
    if not pin or not pin.isdigit():
        raise ValueError(f"invalid PIN: {pin!r}")
    clicks = []
    for ch in pin:
        if ch not in digit_map:
            raise ValueError(f"digit {ch} not in digit_map: {sorted(digit_map.keys())}")
        clicks.append(digit_map[ch])
    return clicks


def save_visualization(image_path: str | Path, digit_map: dict[str, tuple[int, int]],
                       output_path: str | Path) -> None:
    """digit_map 의 각 (x, y) 위치에 숫자 라벨 + 원 표시한 PNG 저장."""
    import cv2
    img = cv2.imread(str(image_path))
    if img is None:
        return
    for d, (x, y) in digit_map.items():
        cv2.circle(img, (x, y), 30, (0, 255, 0), 2)
        cv2.putText(img, d, (x - 15, y + 15), cv2.FONT_HERSHEY_SIMPLEX,
                    1.5, (0, 255, 0), 3)
    cv2.imwrite(str(output_path), img)


# ─── CLI ───

def _main():
    if len(sys.argv) < 2:
        print("usage: python3 phone_auto/ocr_keypad.py <image.png>")
        sys.exit(1)
    img = sys.argv[1]
    print(f"입력: {img}")
    print(f"엔진: easyocr + tesseract + macOS vision + gcv\n")
    result = vote_digits(img, verbose=True)
    print()
    if result is None:
        print("❌ 0~9 distinct 매핑 실패")
        sys.exit(2)
    print(f"✅ 10/10 매핑 성공")
    for d in sorted(result):
        x, y = result[d]
        print(f"   {d}: ({x}, {y})")
    # 시각화 — flip 적용된 이미지 (실 폰 화면 방향) 에 좌표 표시
    import cv2
    img_arr = cv2.imread(img)
    if img_arr is not None:
        unmirrored = cv2.flip(img_arr, 1)  # default flip_h=True 가정 (Continuity)
        tmp_unflipped = Path(img).parent / f"{Path(img).stem}_unmirrored.png"
        cv2.imwrite(str(tmp_unflipped), unmirrored)
        out = Path(img).parent / f"{Path(img).stem}_annotated.png"
        save_visualization(tmp_unflipped, result, out)
        print(f"\n시각화 (un-mirrored): {out}")


if __name__ == "__main__":
    _main()
