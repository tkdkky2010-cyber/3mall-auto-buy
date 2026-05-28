"""범용 폰 화면 OCR + camera→phone 좌표 변환 + ESP32 tap.

ocr_keypad.py 가 키패드 숫자 전용이라면 이건 일반 텍스트 라벨 (앱 아이콘, 버튼).

흐름:
  1. capture_phone_screen() — 카메라 1프레임 캡쳐
  2. ocr_text(img) — macOS Vision 으로 모든 텍스트 + 카메라 좌표 추출
  3. camera_to_phone(cx, cy) — 캘리브레이션 매트릭스로 폰 screen 좌표 변환
  4. find_text(items, query) — 라벨로 검색
  5. tap_text(esp, query) — OCR + 검색 + 변환 + esp.click

캘리브레이션:
  - phone_auto/_tmp/calibration.json 에 저장
  - {"cam_w": 1920, "cam_h": 1080, "phone_w": 1080, "phone_h": 2400,
     "tl": [x,y], "tr": [x,y], "bl": [x,y], "br": [x,y]}
  - 4 corners 만 알면 perspective transform (cv2.getPerspectiveTransform) 적용
"""
from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Optional

_VISION_AVAILABLE = None
_CV2_AVAILABLE = None

def _check_imports():
    global _VISION_AVAILABLE, _CV2_AVAILABLE
    if _VISION_AVAILABLE is None:
        try:
            import Vision  # noqa
            import Quartz  # noqa
            from Foundation import NSURL  # noqa
            _VISION_AVAILABLE = True
        except ImportError:
            _VISION_AVAILABLE = False
    if _CV2_AVAILABLE is None:
        try:
            import cv2  # noqa
            _CV2_AVAILABLE = True
        except ImportError:
            _CV2_AVAILABLE = False


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CALIB_PATH = PROJECT_ROOT / "phone_auto" / "_tmp" / "calibration.json"


def capture_phone_screen(cam_idx: int = 0, warmup_frames: int = 10,
                          out_path: Optional[str] = None) -> Optional[str]:
    """카메라 1프레임 캡쳐. ocr_keypad.capture_frame 재사용."""
    from .ocr_keypad import capture_frame
    if out_path is None:
        tmp = PROJECT_ROOT / "phone_auto" / "_tmp"
        tmp.mkdir(exist_ok=True)
        out_path = tmp / "screen.png"
    return capture_frame(cam_idx, warmup_frames=warmup_frames, out_path=out_path)


def capture_portrait_frame(out_path: Optional[str] = None, zoom: float = 2.0,
                            warmup_frames: int = 30, timeout_sec: float = 15.0) -> Optional[str]:
    """AVFoundation portrait 캡처 (method5_focus 이식).

    폰 가로 마운트 + videoRotationAngle 90 → cam 1080x1920 portrait
    (memory: feedback_phone_landscape_mount_cam_portrait). zoom + continuous AF.

    cv2.VideoCapture (landscape, AF/zoom 없음) 와 달리 PIN 키패드 OCR 에 필요한
    portrait 해상도/초점 확보. Returns 저장된 PNG path or None.
    """
    import objc
    from Foundation import NSObject, NSURL, NSRunLoop, NSDate
    import AVFoundation as AVF
    from Quartz import CIImage, CIContext, CGImageDestinationCreateWithURL, \
        CGImageDestinationAddImage, CGImageDestinationFinalize
    from CoreMedia import CMSampleBufferGetImageBuffer
    import Quartz
    import ctypes, ctypes.util, time

    if out_path is None:
        tmp = PROJECT_ROOT / "phone_auto" / "_tmp"
        tmp.mkdir(exist_ok=True)
        out_path = str(tmp / "portrait.png")
    out_path = str(out_path)

    libdispatch = ctypes.CDLL(ctypes.util.find_library("System"))
    libdispatch.dispatch_queue_create.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
    libdispatch.dispatch_queue_create.restype = ctypes.c_void_p

    # ObjC class 는 module load 시 한 번만 register (재정의 시 objc runtime error)
    # capture_portrait_frame 함수가 2회 이상 호출되어도 재정의 안 되도록 캐시.
    _FrameDelegate = globals().get("_PortraitFrameDelegate")
    if _FrameDelegate is None:
        class _PortraitFrameDelegate(NSObject):
            def init(self):
                self = objc.super(_PortraitFrameDelegate, self).init()
                self.image_saved = False
                self.skip = 0
                self.warmup = 30
                self.out_path = ""
                return self

            def captureOutput_didOutputSampleBuffer_fromConnection_(self, output, sampleBuffer, connection):
                if self.image_saved:
                    return
                self.skip += 1
                if self.skip < self.warmup:
                    return
                try:
                    pixbuf = CMSampleBufferGetImageBuffer(sampleBuffer)
                    if pixbuf is None:
                        return
                    ci = CIImage.imageWithCVPixelBuffer_(pixbuf)
                    ctx = CIContext.contextWithOptions_(None)
                    cg = ctx.createCGImage_fromRect_(ci, ci.extent())
                    if cg is None:
                        return
                    url = NSURL.fileURLWithPath_(self.out_path)
                    dest = CGImageDestinationCreateWithURL(url, "public.png", 1, None)
                    CGImageDestinationAddImage(dest, cg, None)
                    CGImageDestinationFinalize(dest)
                    self.image_saved = True
                except Exception:
                    pass
        globals()["_PortraitFrameDelegate"] = _PortraitFrameDelegate
        _FrameDelegate = _PortraitFrameDelegate

    session = AVF.AVCaptureSession.alloc().init()
    if session.canSetSessionPreset_(AVF.AVCaptureSessionPresetHigh):
        session.setSessionPreset_(AVF.AVCaptureSessionPresetHigh)
    # iPhone Continuity 는 ContinuityCamera type 으로 enumerate (External 아님 — macOS 버전별 다름)
    dev_types = [AVF.AVCaptureDeviceTypeExternal, AVF.AVCaptureDeviceTypeBuiltInWideAngleCamera]
    if hasattr(AVF, "AVCaptureDeviceTypeContinuityCamera"):
        dev_types.insert(0, AVF.AVCaptureDeviceTypeContinuityCamera)
    discovery = AVF.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
        dev_types, AVF.AVMediaTypeVideo, AVF.AVCaptureDevicePositionUnspecified,
    )
    devs = discovery.devices()
    if not devs:
        return None
    device = next((d for d in devs if "iPhone" in d.localizedName()), devs[0])
    ok, _ = device.lockForConfiguration_(None)
    if ok:
        max_zoom = device.activeFormat().videoMaxZoomFactor()
        device.setVideoZoomFactor_(min(zoom, max_zoom))
        if device.isFocusModeSupported_(AVF.AVCaptureFocusModeContinuousAutoFocus):
            device.setFocusMode_(AVF.AVCaptureFocusModeContinuousAutoFocus)
        if device.isExposureModeSupported_(AVF.AVCaptureExposureModeContinuousAutoExposure):
            device.setExposureMode_(AVF.AVCaptureExposureModeContinuousAutoExposure)
        device.unlockForConfiguration()
    inp, _ = AVF.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
    session.addInput_(inp)
    output = AVF.AVCaptureVideoDataOutput.alloc().init()
    output.setAlwaysDiscardsLateVideoFrames_(True)
    session.addOutput_(output)
    for conn in output.connections():
        if conn.isVideoRotationAngleSupported_(90):
            conn.setVideoRotationAngle_(90)
    delegate = _FrameDelegate.alloc().init()
    q_ptr = libdispatch.dispatch_queue_create(b"frame_q", None)
    output.setSampleBufferDelegate_queue_(delegate, objc.objc_object(c_void_p=q_ptr))
    session.startRunning()
    end = time.time() + timeout_sec
    while time.time() < end and not delegate.image_saved:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
    session.stopRunning()
    return out_path if delegate.image_saved else None


def ocr_text(img_path: str, languages: list[str] | None = None) -> list[dict]:
    """macOS Vision OCR — 한+영 모든 텍스트 + 카메라 좌표.

    Returns list of {text, cx, cy, w, h, conf} in CAMERA frame coords.
    """
    _check_imports()
    if not _VISION_AVAILABLE:
        raise RuntimeError("Vision framework 모듈 미설치: pip install pyobjc-framework-Vision pyobjc-framework-Quartz")
    import Vision
    import Quartz
    from Foundation import NSURL

    languages = languages or ["ko-KR", "en-US"]
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
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(languages)
    handler.performRequests_error_([req], None)

    items = []
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
        items.append({
            "text": text,
            "cx": cx, "cy": cy,
            "w": int(bw), "h": int(bh),
            "conf": float(cand.confidence()),
        })
    return items


# ─── camera ↔ phone 좌표 변환 ───

def load_calibration() -> Optional[dict]:
    if not CALIB_PATH.exists():
        return None
    return json.loads(CALIB_PATH.read_text())


def save_calibration(calib: dict) -> None:
    CALIB_PATH.parent.mkdir(exist_ok=True)
    CALIB_PATH.write_text(json.dumps(calib, indent=2))


def auto_calibrate_from_ocr(items: list[dict], cam_w: int = 1920, cam_h: int = 1080,
                             phone_w: int = 1080, phone_h: int = 2400,
                             pad_frac: float = 0.05) -> dict:
    """OCR 결과 텍스트들의 bounding box 로 폰 screen 영역 자동 추정.

    가정: 폰 안 텍스트가 카메라 frame 의 중앙 영역 군집. 외부 (KIOXIA 같은 배경)
    는 X 범위 outlier 로 분리.

    pad_frac: 검출 bbox 의 추가 여백 비율 (텍스트 bbox 가 screen 실제 가장자리보다 작음)
    """
    if not items:
        raise ValueError("OCR items 비어있음")
    xs = [it["cx"] for it in items]
    ys = [it["cy"] for it in items]
    # X 중간 50% 의 median 으로 phone 중앙 X 추정
    xs_sorted = sorted(xs)
    q25 = xs_sorted[len(xs_sorted)//4]
    q75 = xs_sorted[len(xs_sorted)*3//4]
    # 중앙 군집만 (외부 텍스트 outlier 제외)
    central = [it for it in items if q25 - 100 <= it["cx"] <= q75 + 100]
    if not central:
        central = items
    cxs = [it["cx"] for it in central]
    cys = [it["cy"] for it in central]
    x_min, x_max = min(cxs), max(cxs)
    y_min, y_max = min(cys), max(cys)
    # padding (텍스트 라벨이 보통 화면 내부에 있어 약간 expand)
    pw = (x_max - x_min) * pad_frac
    ph = (y_max - y_min) * pad_frac
    tl = [int(x_min - pw), int(y_min - ph)]
    br = [int(x_max + pw), int(y_max + ph)]
    tr = [br[0], tl[1]]
    bl = [tl[0], br[1]]
    return {
        "cam_w": cam_w, "cam_h": cam_h,
        "phone_w": phone_w, "phone_h": phone_h,
        "tl": tl, "tr": tr, "bl": bl, "br": br,
        "method": "auto_ocr_bbox",
    }


def manual_calibrate(tl: tuple[int, int], tr: tuple[int, int],
                     bl: tuple[int, int], br: tuple[int, int],
                     cam_w: int = 1920, cam_h: int = 1080,
                     phone_w: int = 1080, phone_h: int = 2400) -> dict:
    """4 모서리 직접 지정. 카메라 frame 안의 폰 화면 좌상/우상/좌하/우하."""
    return {
        "cam_w": cam_w, "cam_h": cam_h,
        "phone_w": phone_w, "phone_h": phone_h,
        "tl": list(tl), "tr": list(tr), "bl": list(bl), "br": list(br),
        "method": "manual",
    }


def _perspective_matrix(calib: dict):
    """4 corners → perspective transform matrix (cam → phone)."""
    _check_imports()
    if not _CV2_AVAILABLE:
        raise RuntimeError("opencv 필요")
    import cv2
    import numpy as np
    src = np.float32([calib["tl"], calib["tr"], calib["br"], calib["bl"]])
    pw, ph = calib["phone_w"], calib["phone_h"]
    dst = np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]])
    return cv2.getPerspectiveTransform(src, dst)


def camera_to_phone(cx: int, cy: int, calib: dict) -> tuple[int, int]:
    """카메라 좌표 → 폰 화면 좌표 (perspective transform 적용)."""
    import cv2
    import numpy as np
    M = _perspective_matrix(calib)
    pts = np.float32([[[cx, cy]]])
    out = cv2.perspectiveTransform(pts, M)
    px, py = int(out[0][0][0]), int(out[0][0][1])
    # clamp
    px = max(0, min(calib["phone_w"] - 1, px))
    py = max(0, min(calib["phone_h"] - 1, py))
    return px, py


# ─── 텍스트 찾기 + 탭 ───

def _normalize(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def find_text(items: list[dict], query: str, fuzzy: bool = True) -> Optional[dict]:
    """OCR 결과에서 query 와 가장 잘 매칭되는 item.

    fuzzy=True: substring + 정규화 (공백/대소문자 무시).
    fuzzy=False: exact match (normalized).
    """
    q = _normalize(query)
    # 1) exact
    for it in items:
        if _normalize(it["text"]) == q:
            return it
    if not fuzzy:
        return None
    # 2) substring (양방향)
    candidates = []
    for it in items:
        t = _normalize(it["text"])
        if q in t or t in q:
            candidates.append((it, abs(len(t) - len(q))))
    if candidates:
        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]
    return None


def tap_text(esp, query: str, cam_idx: int = 0,
             calib: Optional[dict] = None,
             dry_run: bool = False,
             verbose: bool = True) -> Optional[tuple[int, int]]:
    """캡쳐 → OCR → 검색 → 좌표 변환 → esp.click.

    Returns (phone_x, phone_y) on success, None on fail.

    Args:
        esp: ESP32Client (또는 dry_run=True 면 None)
        query: 찾을 텍스트 (예: '현대카드')
        calib: 캘리브레이션 dict (None 이면 파일에서 로드)
        dry_run: True 면 esp.click 안 부르고 좌표만 반환
    """
    calib = calib or load_calibration()
    if calib is None:
        raise RuntimeError(f"calibration 없음 — 먼저 캘리브레이션 필요 ({CALIB_PATH})")
    img = capture_phone_screen(cam_idx=cam_idx)
    if not img:
        if verbose:
            print(f"[tap_text] capture 실패")
        return None
    items = ocr_text(img)
    if verbose:
        print(f"[tap_text] OCR {len(items)}개 텍스트 검출")
    hit = find_text(items, query)
    if not hit:
        if verbose:
            print(f"[tap_text] '{query}' 못 찾음. 가용 텍스트: {[it['text'] for it in items]}")
        return None
    px, py = camera_to_phone(hit["cx"], hit["cy"], calib)
    if verbose:
        print(f"[tap_text] '{hit['text']}' cam=({hit['cx']}, {hit['cy']}) → phone=({px}, {py}) (conf={hit['conf']:.2f})")
    if not dry_run and esp is not None:
        esp.click(px, py)
        if verbose:
            print(f"[tap_text] ✓ esp.click({px}, {py})")
    return px, py


# ─── CLI ───

def _main():
    import sys
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["ocr", "calibrate-auto", "calibrate-manual", "tap", "dry-tap"])
    p.add_argument("--query", help="tap/dry-tap 시 검색할 텍스트")
    p.add_argument("--cam", type=int, default=0)
    p.add_argument("--tl", help="manual: 'x,y' 좌상")
    p.add_argument("--tr", help="manual: 'x,y' 우상")
    p.add_argument("--bl", help="manual: 'x,y' 좌하")
    p.add_argument("--br", help="manual: 'x,y' 우하")
    p.add_argument("--esp-ip", default=None, help="ESP32 IP (tap 시)")
    args = p.parse_args()

    if args.cmd == "ocr":
        img = capture_phone_screen(args.cam)
        items = ocr_text(img)
        for it in items:
            print(f"  '{it['text']}'  ({it['cx']}, {it['cy']})  {it['w']}x{it['h']}  conf={it['conf']:.2f}")
    elif args.cmd == "calibrate-auto":
        img = capture_phone_screen(args.cam)
        items = ocr_text(img)
        calib = auto_calibrate_from_ocr(items)
        save_calibration(calib)
        print(f"[calib auto] saved: {CALIB_PATH}")
        print(json.dumps(calib, indent=2))
    elif args.cmd == "calibrate-manual":
        def parse(s): return tuple(int(x) for x in s.split(","))
        calib = manual_calibrate(parse(args.tl), parse(args.tr),
                                 parse(args.bl), parse(args.br))
        save_calibration(calib)
        print(f"[calib manual] saved: {CALIB_PATH}")
        print(json.dumps(calib, indent=2))
    elif args.cmd in ("tap", "dry-tap"):
        if not args.query:
            print("--query 필요"); sys.exit(1)
        from .esp32_client import ESP32Client
        import os
        if args.cmd == "tap":
            ip = args.esp_ip or os.environ.get("ESP32_IP", "172.30.1.96")
            esp = ESP32Client(ip)
        else:
            esp = None
        result = tap_text(esp, args.query, cam_idx=args.cam,
                          dry_run=(args.cmd == "dry-tap"))
        if result:
            print(f"\n→ phone coord: {result}")
        else:
            sys.exit(2)


if __name__ == "__main__":
    _main()
