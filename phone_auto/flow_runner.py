"""카드 결제 flow JSON → ADB 자동 실행.

각 카드 flow는 `phone_auto/coords/apps/<카드>.json` 의 `flow_payment` array.

지원 action:
  - tap: {"action":"tap", "xy":[x,y]}  또는 {"action":"tap", "target":"home_apps.현대카드"}
  - sleep_ms: {"action":"sleep_ms": <ms>}
  - close_popup_if_present: 광고 modal X 검출 + 닫기 (dump의 content-desc='닫기' / OCR '×')
  - verify_text: 화면에 텍스트 보이는지 OCR 확인
  - input_pin: PIN/결제코드 매 키 dump+OCR 재매핑 + 0.5초+ 페이싱
    {"action":"input_pin", "kind":"bc_6digit_shuffle", "value":"137601", "tap_delay_sec":0.7}

CLI:
    python3 -m phone_auto.flow_runner hyundai_card pc_pay
    python3 -m phone_auto.flow_runner bc_paybook_isp pc_pay --pin 137601 --code 1234567
"""
from __future__ import annotations
import json
import time
import subprocess
import re
from pathlib import Path
from typing import Any

from .adb_input import ADB, load_coords, COORDS_DIR
from .screen_ocr import find_text


def _ocr_digits(img_path: str, y_min: int = 1600, y_max: int = 2200) -> dict[str, tuple[int, int]]:
    """이미지에서 single digit 만 추출. {'0':(x,y), '1':(x,y), ...}"""
    try:
        import Vision, Quartz
        from Foundation import NSURL
    except ImportError:
        raise RuntimeError("pyobjc Vision/Quartz 필요 — brew python3")
    url = NSURL.fileURLWithPath_(img_path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(False)
    req.setRecognitionLanguages_(['en-US'])
    h.performRequests_error_([req], None)
    digits = {}
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)[0]
        text = (cand.string() or "").strip()
        if not (text.isdigit() and len(text) == 1): continue
        bb = obs.boundingBox()
        x = bb.origin.x * W; y = (1 - bb.origin.y - bb.size.height) * H
        bw = bb.size.width*W; bh = bb.size.height*H
        cx, cy = int(x+bw/2), int(y+bh/2)
        if not (y_min <= cy <= y_max): continue
        digits[text] = (cx, cy)
    return digits


def _ocr_texts(img_path: str) -> list[dict]:
    """전체 텍스트 OCR. [{'text','cx','cy','w','h'}, ...]"""
    import Vision, Quartz
    from Foundation import NSURL
    url = NSURL.fileURLWithPath_(img_path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setUsesLanguageCorrection_(True)
    req.setRecognitionLanguages_(['ko-KR','en-US'])
    h.performRequests_error_([req], None)
    items = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)[0]
        text = (cand.string() or "").strip()
        if not text: continue
        bb = obs.boundingBox()
        x = bb.origin.x * W; y = (1 - bb.origin.y - bb.size.height) * H
        bw = bb.size.width*W; bh = bb.size.height*H
        items.append({"text": text, "cx": int(x+bw/2), "cy": int(y+bh/2),
                      "w": int(bw), "h": int(bh)})
    return items


def _dump_close_buttons(adb: ADB) -> list[tuple[int, int]]:
    """dump에서 content-desc='닫기' 또는 비슷한 button 좌표 list."""
    tmp = "/tmp/_close.xml"
    adb.dump_ui(tmp)
    import xml.etree.ElementTree as ET
    BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
    root = ET.parse(tmp).getroot()
    out = []
    keys = ["닫기", "팝업 닫기", "close"]
    for n in root.iter():
        a = n.attrib
        d = a.get("content-desc","").strip()
        t = a.get("text","").strip()
        if any(k in d or k in t for k in keys):
            b = BOUNDS.match(a.get("bounds",""))
            if b:
                x1,y1,x2,y2 = map(int, b.groups())
                out.append(((x1+x2)//2, (y1+y2)//2))
    return out


class FlowError(Exception):
    pass


class FlowRunner:
    def __init__(self, adb: ADB | None = None, verbose: bool = True,
                 use_camera: bool | None = None, cam_idx: int = 0,
                 use_portrait: bool | None = None):
        self.adb = adb or ADB()
        self.verbose = verbose
        self._tmp_img = "/tmp/_flow.png"
        # 카메라 모드 — FLAG_SECURE 화면 (monimo 등) screencap 이 검정이라 카메라 OCR 필요
        # use_camera=None → FLOW_USE_CAMERA env var 또는 calibration.json 존재 시 자동 활성
        if use_camera is None:
            import os as _os
            env = _os.environ.get("FLOW_USE_CAMERA", "").lower()
            if env in ("1", "true", "yes"):
                use_camera = True
            elif env in ("0", "false", "no"):
                use_camera = False
            else:
                use_camera = False  # 기본 = ADB screencap (호환성)
        self.use_camera = use_camera
        # portrait 캡처 모드 — 폰 가로 마운트 + cam rotation 90 = 1080x1920
        # (memory: feedback_phone_landscape_mount_cam_portrait). FLAG_SECURE PIN 키패드용.
        if use_portrait is None:
            import os as _os
            use_portrait = _os.environ.get("FLOW_PORTRAIT", "").lower() in ("1", "true", "yes")
        self.use_portrait = use_portrait
        self.cam_idx = cam_idx
        self._calib = None
        if self.use_camera:
            try:
                from .screen_ocr import load_calibration
                self._calib = load_calibration()
                if self._calib is None:
                    raise RuntimeError("calibration.json 없음 — phone_auto/_tmp/calibration.json")
                self._log(f"camera mode ON (cam_idx={cam_idx}, calib loaded)")
            except Exception as e:
                self._log(f"⚠ camera mode 활성화 실패: {e} — ADB screencap 으로 fallback")
                self.use_camera = False

    def calibrate_from_home(self) -> bool:
        """홈화면 OCR + home_apps.json 알려진 좌표 매칭 → calibration auto-update.
        매 카드앱 진입 전 1회. 폰 위치 살짝 변경되어도 자동 보정.

        Returns True 시 calibration 갱신 성공.
        """
        if not self.use_camera:
            return False
        import subprocess
        from .screen_ocr import find_text, save_calibration
        from .adb_input import load_coords
        # 홈 화면 진입
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_HOME"], check=False)
        time.sleep(1.5)
        # 캡쳐 + OCR
        self._cap()
        items = _ocr_texts(self._tmp_img)
        # home_apps.json 의 알려진 앱 좌표
        try:
            home = load_coords("home_apps")
        except Exception as e:
            self._log(f"calibrate_from_home: home_apps load fail: {e}")
            return False
        apps = home.get("apps", {})
        # OCR 결과에서 app name 매칭 → (cam, phone) anchor pairs
        anchors = []
        for app_name, info in apps.items():
            hit = find_text(items, app_name)
            if hit:
                anchors.append({"text": app_name, "phone": [info["x"], info["y"]],
                                 "cam_ref": [hit["cx"], hit["cy"]]})
        self._log(f"calibrate_from_home: {len(anchors)} anchor 매칭 (홈앱 / OCR)")
        if len(anchors) < 3:
            self._log(f"  ⚠ anchor 부족 ({len(anchors)}/3) — calibration skip")
            return False
        # adaptive_calib 의 build_transform_from_anchors 로 transform 계산
        from .adaptive_calib import build_transform_from_anchors
        ref = {"anchors": anchors}
        transform = build_transform_from_anchors(items, ref)
        if not transform:
            self._log(f"  ⚠ transform 계산 실패")
            return False
        # transform 으로 4 corner phone (0,0)~(1080,2400) 역계산 → static calibration 갱신
        import cv2
        import numpy as np
        M = np.array(transform["matrix"], dtype=np.float32)
        # phone (0,0), (1080,0), (1080,2400), (0,2400) → cam 역변환 (inverse)
        M_inv = np.linalg.inv(M)
        phone_corners = np.float32([[[0, 0]], [[1080, 0]], [[0, 2400]], [[1080, 2400]]])
        cam_corners = cv2.perspectiveTransform(phone_corners, M_inv)
        # portrait 모드 = cam 1080x1920, landscape = 1920x1080
        cam_w, cam_h = (1080, 1920) if self.use_portrait else (1920, 1080)
        new_calib = {
            "cam_w": cam_w, "cam_h": cam_h,
            "phone_w": 1080, "phone_h": 2400,
            "tl": [int(cam_corners[0][0][0]), int(cam_corners[0][0][1])],
            "tr": [int(cam_corners[1][0][0]), int(cam_corners[1][0][1])],
            "bl": [int(cam_corners[2][0][0]), int(cam_corners[2][0][1])],
            "br": [int(cam_corners[3][0][0]), int(cam_corners[3][0][1])],
            "method": f"home_auto_{len(anchors)}anchor",
        }
        save_calibration(new_calib)
        self._calib = new_calib
        self._log(f"  ✓ calibration 갱신: tl={new_calib['tl']} tr={new_calib['tr']} bl={new_calib['bl']} br={new_calib['br']}")
        return True

    def _cluster_centers(self, values: list, n: int) -> list:
        """1D 좌표 list 를 n 개 cluster center 로 — gap-based (큰 gap 이 boundary)."""
        if not values or n < 1:
            return []
        sorted_vals = sorted(values)
        if n == 1 or len(sorted_vals) <= n:
            return [sum(sorted_vals) // len(sorted_vals)]
        # 가장 큰 (n-1) 개 gap 위치 가 cluster boundary
        gaps = [(sorted_vals[i+1] - sorted_vals[i], i) for i in range(len(sorted_vals) - 1)]
        gaps.sort(reverse=True)
        splits = sorted([g[1] for g in gaps[:n-1]])
        clusters = []
        start = 0
        for sp in splits:
            chunk = sorted_vals[start:sp+1]
            if chunk:
                clusters.append(sum(chunk) // len(chunk))
            start = sp + 1
        last_chunk = sorted_vals[start:]
        if last_chunk:
            clusters.append(sum(last_chunk) // len(last_chunk))
        return clusters

    def _log(self, msg: str):
        if self.verbose: print(f"[flow] {msg}")

    def _cap(self):
        if self.use_camera and self.use_portrait:
            # portrait AVFoundation 캡처 (rotation 90 + zoom + AF). FLAG_SECURE 키패드용.
            from .screen_ocr import capture_portrait_frame
            if capture_portrait_frame(out_path=self._tmp_img, warmup_frames=30) is None:
                raise FlowError("_cap: portrait 캡처 실패 (iPhone 카메라 미연결 — AVF device 0개)")
        elif self.use_camera:
            # 카메라 frame 캡쳐 → /tmp/_flow.png 저장 (OCR 함수가 path 받음)
            from .screen_ocr import capture_phone_screen
            img = capture_phone_screen(self.cam_idx, warmup_frames=3)
            # capture_phone_screen 이 path string 반환하면 그대로 + 다른 위치면 _tmp_img 로 복사
            if isinstance(img, str) and img != self._tmp_img:
                import shutil
                shutil.copy(img, self._tmp_img)
        else:
            self.adb.screencap(self._tmp_img)

    def run_action(self, action: dict, vars: dict | None = None) -> Any:
        vars = vars or {}
        kind = action.get("action")
        if kind == "tap":
            if "xy" in action:
                x, y = action["xy"]
            elif "target" in action:
                src, name = action["target"].split(".", 1)
                data = load_coords(src)
                apps = data.get("apps", data)
                c = apps[name]
                x, y = c["x"], c["y"]
            else:
                raise FlowError(f"tap: xy 또는 target 필요")
            self._log(f"tap ({x},{y}) {action.get('name','')}")
            self.adb.tap(x, y)

        elif kind == "sleep_ms":
            ms = action.get("ms") or action.get("sleep_ms")
            self._log(f"sleep {ms}ms")
            time.sleep(ms / 1000.0)

        elif kind == "close_popup_if_present":
            self._log("close_popup_if_present")
            # 모든 카드사 광고 modal 공통 패턴 — OCR로 X/닫기/오늘 그만 보기 매칭 (메모리 [[feedback_card_app_popup_close]])
            # 1. dump 기반 닫기 button (content-desc / text 매칭)
            btns = _dump_close_buttons(self.adb)
            if btns:
                x, y = btns[0]
                self._log(f"  close button (dump) @ ({x},{y})")
                self.adb.tap(x, y)
                time.sleep(0.8)
                return
            # 2. OCR 기반 — close keyword 매칭. 사용자 정의 list + default 확장.
            close_keywords = action.get("close_keywords") or [
                "오늘 그만 보기", "오늘 하루 그만 보기", "오늘 그만",
                "다시 보지 않기", "다시 보지", "다시는 보지",
                "건너뛰기", "skip", "Skip",
                "팝업 닫기", "닫기", "닫 기",
                "×", "X", "x",  # icon-as-text fallback
            ]
            self._cap()
            items = _ocr_texts(self._tmp_img)
            # 매칭 우선순위: long text > short text (정확도 위해)
            sorted_kw = sorted(close_keywords, key=lambda s: -len(s))
            for kw in sorted_kw:
                for it in items:
                    if kw == it["text"] or (len(kw) >= 3 and kw in it["text"]):
                        # X / × / x 는 사이즈 가드 (큰 ImageView X 우선)
                        if kw in ("×", "X", "x") and it["w"] > 200:
                            continue
                        # "앱 종료" / "혜택 더 보기" 같은 결말 button 회피 — 명시 거부
                        if any(bad in it["text"] for bad in ("앱 종료", "혜택 더", "확인", "동의", "구매", "결제")):
                            continue
                        self._log(f"  OCR '{it['text']}' @ ({it['cx']},{it['cy']})")
                        self.adb.tap(it["cx"], it["cy"])
                        time.sleep(0.8)
                        return
            # 3. hint 좌표 fallback
            if "x" in action and "y" in action:
                self._log(f"  hint @ ({action['x']},{action['y']})")
                self.adb.tap(action["x"], action["y"])
                time.sleep(0.8)
                return
            self._log("  no popup found, skip")

        elif kind == "tap_until_text":
            # tap 좌표 + tap 후 expect_text 도달 verification + retry.
            # 매 step 의 robustness — tap 후 화면 안 바뀌면 같은 좌표 또는 fallback 좌표 재시도.
            import subprocess, re as _re, xml.etree.ElementTree as ET
            xy = action.get("xy")
            fallback_xys = action.get("fallback_xys", [])
            expect_text = action.get("expect_text")
            retries = action.get("retries", 3)
            sleep_after_tap = action.get("post_sleep_sec", 2.0)
            if not xy or not expect_text:
                raise FlowError("tap_until_text: 'xy' + 'expect_text' 필요")
            self._log(f"tap_until_text xy={xy} expect='{expect_text}' retries={retries}")
            tmp = "/tmp/_dump_until.xml"
            attempts = [xy] + fallback_xys
            for ai, attempt_xy in enumerate(attempts):
                for ri in range(retries):
                    self.adb.tap(attempt_xy[0], attempt_xy[1])
                    time.sleep(sleep_after_tap)
                    subprocess.run(["adb", "shell", "rm", "/sdcard/_dump.xml"], check=False, capture_output=True)
                    subprocess.run(["adb", "exec-out", "uiautomator", "dump", "--compressed", "/sdcard/_dump.xml"],
                                   check=False, capture_output=True)
                    subprocess.run(["adb", "pull", "/sdcard/_dump.xml", tmp], check=False, capture_output=True)
                    try:
                        root = ET.parse(tmp).getroot()
                    except Exception:
                        continue
                    found = False
                    for n in root.iter():
                        if expect_text in (n.attrib.get("text", "") or ""):
                            found = True; break
                    if found:
                        self._log(f"  ✓ tap @ {attempt_xy} → '{expect_text}' 확인 (xy[{ai}] retry {ri+1})")
                        return
                    self._log(f"  retry {ri+1}/{retries} @ {attempt_xy}: '{expect_text}' 미발견")
            raise FlowError(f"tap_until_text: 모든 좌표 retry 후 '{expect_text}' 미도달")

        elif kind == "tap_dump_text":
            # ADB uiautomator dump 의 element text 정확 매칭 → bounds center tap.
            # 좌표 hardcode 보다 robust — monimo 의 다른 activity 진입 시도 좌표 충돌 회피.
            import subprocess, re as _re, xml.etree.ElementTree as ET
            target = action.get("text")
            if not target:
                raise FlowError("tap_dump_text: 'text' 필드 필요")
            timeout = action.get("timeout_sec", 8)
            self._log(f"tap_dump_text '{target}' (timeout {timeout}s)")
            BOUNDS = _re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
            start = time.time()
            tmp = "/tmp/_dump.xml"
            while time.time() - start < timeout:
                subprocess.run(["adb", "shell", "rm", "/sdcard/_dump.xml"], check=False, capture_output=True)
                subprocess.run(["adb", "exec-out", "uiautomator", "dump", "--compressed", "/sdcard/_dump.xml"],
                               check=False, capture_output=True)
                subprocess.run(["adb", "pull", "/sdcard/_dump.xml", tmp], check=False, capture_output=True)
                try:
                    root = ET.parse(tmp).getroot()
                except Exception:
                    time.sleep(0.5); continue
                exact = action.get("exact", False)
                for n in root.iter():
                    nt = (n.attrib.get("text", "") or "")
                    if (nt == target) if exact else (target in nt):
                        m = BOUNDS.match(n.attrib.get("bounds", ""))
                        if m:
                            x1, y1, x2, y2 = map(int, m.groups())
                            cx, cy = (x1+x2)//2, (y1+y2)//2
                            self.adb.tap(cx, cy)
                            self._log(f"  ✓ '{target}' tap @ ({cx},{cy})")
                            time.sleep(action.get("post_sleep_sec", 1.0))
                            return
                time.sleep(0.5)
            if action.get("ignore_fail"):
                self._log(f"  '{target}' 미발견 — ignore_fail=True, skip")
                return
            raise FlowError(f"tap_dump_text '{target}' 미발견 (timeout {timeout}s)")

        elif kind == "wait_for_activity":
            # monimo 화면 분기용 — ADB dumpsys 의 mCurrentFocus 가 target activity substring 포함 될 때까지 polling
            import subprocess
            target = action.get("activity")
            if not target:
                raise FlowError("wait_for_activity: 'activity' 필드 필요")
            timeout = action.get("timeout_sec", 10)
            self._log(f"wait_for_activity '{target}' (timeout {timeout}s)")
            start = time.time()
            while time.time() - start < timeout:
                r = subprocess.run(["adb", "shell", "dumpsys", "window"],
                                   capture_output=True, text=True, timeout=5)
                m = re.search(r"mCurrentFocus=Window\{[^}]+\}", r.stdout)
                cur = m.group(0) if m else ""
                if target in cur:
                    self._log(f"  ✓ {target} 도달 ({time.time()-start:.1f}s)")
                    return
                time.sleep(0.5)
            raise FlowError(f"wait_for_activity '{target}' timeout — last focus: {cur[:120]}")

        elif kind == "keyevent":
            # ADB KEYCODE_HOME, KEYCODE_APP_SWITCH, KEYCODE_BACK 등 송신
            import subprocess
            code = action.get("keycode") or action.get("code")
            if not code:
                raise FlowError("keyevent: 'keycode' 필드 필요")
            self._log(f"keyevent {code}")
            subprocess.run(["adb", "shell", "input", "keyevent", code], check=False)
            time.sleep(action.get("post_sleep_sec", 0.3))

        elif kind == "force_stop":
            # 앱 강제 종료 — 이전 화면 잔여 (예: monimo Manage) 정리용
            import subprocess
            pkg = action.get("package")
            if not pkg:
                raise FlowError("force_stop: 'package' 필드 필요")
            self._log(f"force_stop {pkg}")
            subprocess.run(["adb", "shell", "am", "force-stop", pkg], check=False)
            time.sleep(action.get("post_sleep_sec", 0.5))

        elif kind == "tap_camera_text":
            # 카메라 OCR 로 텍스트 찾아 tap. 화면 상태 무관하게 robust.
            # retry 사이 5초 wait — 화면 전환 늦는 경우 (사용자 알려준 정책)
            if not self.use_camera:
                raise FlowError("tap_camera_text: use_camera=False (FLOW_USE_CAMERA=1 필요)")
            text = action.get("text")
            if not text:
                raise FlowError("tap_camera_text: 'text' 필드 필요")
            retries = action.get("retries", 2)
            retry_wait = action.get("retry_wait_sec", 5.0)  # 5초 — 화면 전환 늦는 경우 대비
            from .screen_ocr import camera_to_phone
            for attempt in range(retries + 1):
                self._cap()
                items = _ocr_texts(self._tmp_img)
                hit = next((it for it in items if text in it["text"] and it.get("w", 0) < 600), None)
                if hit:
                    px, py = camera_to_phone(hit["cx"], hit["cy"], self._calib)
                    self._log(f"tap_camera_text '{text}' → '{hit['text']}' phone({px},{py})")
                    self.adb.tap(px, py)
                    time.sleep(action.get("post_sleep_sec", 1.0))
                    break
                self._log(f"  '{text}' OCR 미발견 (attempt {attempt+1}/{retries+1}) — {retry_wait}s 후 retry")
                if attempt < retries:
                    time.sleep(retry_wait)
            else:
                raise FlowError(f"tap_camera_text '{text}' 미발견 ({retries+1} 시도)")

        elif kind == "verify_text":
            text = action["text"]
            self._cap()
            items = _ocr_texts(self._tmp_img)
            found = any(text in it["text"] for it in items)
            if not found:
                raise FlowError(f"verify_text fail: '{text}' not seen on screen")
            self._log(f"verify_text ✓ '{text}'")

        elif kind == "verify_selected":
            # 라디오/결제수단 선택 상태 검증 (페이북머니 가드 [[feedback_paybook_card_payment]]).
            # text: selected=true 여야 함. forbid_text: selected=true 면 결제 중단.
            import xml.etree.ElementTree as ET
            want_text = action.get("text")
            forbid = action.get("forbid_text")
            tmp = "/tmp/_vsel.xml"
            self.adb.dump_ui(tmp)
            try:
                root = ET.parse(tmp).getroot()
            except Exception:
                raise FlowError("verify_selected: dump 파싱 실패")
            ok = want_text is None
            for n in root.iter():
                t = (n.attrib.get("text", "") or "")
                sel = n.attrib.get("selected", "") == "true"
                if forbid and forbid in t and sel:
                    raise FlowError(f"verify_selected: 금지 결제수단 '{forbid}' 선택됨 — 결제 중단")
                if want_text and want_text in t and sel:
                    ok = True
            if not ok:
                raise FlowError(f"verify_selected: '{want_text}' selected=true 아님")
            self._log(f"verify_selected ✓ '{want_text}'=selected, '{forbid}' not selected")

        elif kind == "lotte_change_address":
            target_keyword = action.get("address_keyword", "화곡동 890")
            self._log(f"lotte_change_address target='{target_keyword}'")
            import subprocess
            def _ocr_now():
                subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_lf.png"], check=False, capture_output=True)
                subprocess.run(["adb", "pull", "/sdcard/_lf.png", self._tmp_img], check=False, capture_output=True)
                return _ocr_texts(self._tmp_img)
            items = _ocr_now()
            expand_arrow = next((it for it in items if it["text"].strip() in ("~", "v", "∨") and 280 < it["cy"] < 360), None)
            if expand_arrow:
                self.adb.tap(expand_arrow["cx"], expand_arrow["cy"])
                self._log(f"  배송정보 펼침 ({expand_arrow['cx']},{expand_arrow['cy']})")
                time.sleep(0.5)
                items = _ocr_now()
            # 주소 '변경 >' = 우측정렬 short text. '배송방법 변경(픽업서비스)' 와 구분 (실측 5/27).
            cands = [it for it in items if "변경" in it["text"]
                     and "배송방법" not in it["text"] and "픽업" not in it["text"]]
            cands.sort(key=lambda it: -it["cx"])
            change_btn = cands[0] if cands else None
            if not change_btn:
                raise FlowError("lotte_change_address: 주소 '변경 >' button 없음 (배송방법 변경 제외)")
            self.adb.tap(change_btn["cx"], change_btn["cy"])
            self._log(f"  변경 tap ({change_btn['cx']},{change_btn['cy']})")
            time.sleep(0.5)
            items = _ocr_now()
            target = next((it for it in items if target_keyword in it["text"]), None)
            if not target:
                raise FlowError(f"lotte_change_address: 주소 '{target_keyword}' 없음")
            self.adb.tap(target["cx"], target["cy"])
            self._log(f"  주소 tap '{target['text'][:40]}' ({target['cx']},{target['cy']})")
            time.sleep(0.5)
            items = _ocr_now()
            if not any(target_keyword in it["text"] for it in items):
                raise FlowError(f"lotte_change_address: 주소 변경 검증 실패")
            self._log(f"  ✓ 주소 변경 완료")

        elif kind == "lotte_apply_coupons":
            coupon_section = action.get("section", "할인쿠폰")
            verify_change = action.get("verify_total_change", True)
            import subprocess
            def _ocr_now():
                subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_lf.png"], check=False, capture_output=True)
                subprocess.run(["adb", "pull", "/sdcard/_lf.png", self._tmp_img], check=False, capture_output=True)
                return _ocr_texts(self._tmp_img)
            def _get_total(items):
                for it in items:
                    if it["cy"] > 1950 and "원" in it["text"]:
                        m = re.search(r"([\d,]+)\s*원", it["text"])
                        if m:
                            return int(m.group(1).replace(",", ""))
                return 0
            items = _ocr_now()
            section_hdr = next((it for it in items if coupon_section in it["text"]), None)
            if not section_hdr:
                raise FlowError(f"lotte_apply_coupons: '{coupon_section}' section 없음")
            # 변경 button = '변경 >' (우측정렬). 안내문 '변경 버튼을 클릭...'(cx 작음) 과 구분 — '버튼' 제외 (실측 5/27).
            def _find_change(its):
                cs = [it for it in its if "변경" in it["text"] and "버튼" not in it["text"]
                      and abs(it["cy"] - section_hdr["cy"]) < 150]
                cs.sort(key=lambda c: -c["cx"])
                return cs[0] if cs else None
            change_btn = _find_change(items)
            if not change_btn:
                # 미선택 → section tap 으로 선택. (이미 선택돼 '변경 >' 보이면 재탭 금지 = 선택해제 toggle)
                self.adb.tap(section_hdr["cx"], section_hdr["cy"])
                time.sleep(0.6)
                items = _ocr_now()
                change_btn = _find_change(items)
            if not change_btn:
                raise FlowError(f"lotte_apply_coupons: '{coupon_section}' '변경 >' button 없음")
            self.adb.tap(change_btn["cx"], change_btn["cy"])
            self._log(f"  {coupon_section} 변경 tap @ ({change_btn['cx']},{change_btn['cy']})")
            # 할인선택 화면 전환 대기 — 단일 0.5s 는 너무 빨라 dropdown 놓침 (실측 5/27). poll.
            dropdowns = []
            for _ in range(8):
                time.sleep(0.5)
                items = _ocr_now()
                dropdowns = [it for it in items if "쿠폰을 선택해 주세요" in it["text"]]
                if dropdowns:
                    break
            self._log(f"  상품 dropdown {len(dropdowns)}개")
            if not dropdowns:
                raise FlowError(f"lotte_apply_coupons: '{coupon_section}' 할인선택 dropdown 미검출 (화면 전환 실패?)")
            def _pct(t):
                m = re.search(r"(\d+)\s*%", t)
                return int(m.group(1)) if m else -1
            def _close_btn(its):
                return next((it for it in its if it["text"].strip() == "닫기"), None)
            for idx, dd in enumerate(dropdowns):
                self._log(f"  [{idx+1}/{len(dropdowns)}] dropdown @ ({dd['cx']},{dd['cy']})")
                self.adb.tap(540, dd["cy"])
                # ★ 모달 완전히 열릴 때까지 대기 (닫기 등장). 0.5s 고정은 너무 빨라 underlying 행을
                #   옵션으로 오인 (이미 적용된 다른 상품의 % 라벨 = 같은 텍스트). 실측 5/27.
                modal_items = None
                for _ in range(8):
                    time.sleep(0.4)
                    its = _ocr_now()
                    if _close_btn(its):
                        modal_items = its
                        break
                if not modal_items:
                    raise FlowError(f"lotte_apply_coupons: 상품 {idx+1} 쿠폰 모달 안 열림")
                close_y = _close_btn(modal_items)["cy"]
                pre_total = _get_total(modal_items)
                # 옵션 = 닫기 button 위쪽의 '<n>%' 행 (모달 영역 = underlying 행 가림). 최고 % 선택.
                cands = [it for it in modal_items if _pct(it["text"]) > 0 and it["cy"] < close_y - 30
                         and not it["text"].lstrip().startswith(("()", ")"))]
                cands.sort(key=lambda c: (-_pct(c["text"]), c["cy"]))
                # % 값별 1개만 시도 (모달에 같은 % 가 여러 개 — 중복 tap 낭비 방지)
                seen, uniq = set(), []
                for c in cands:
                    p = _pct(c["text"])
                    if p not in seen:
                        seen.add(p); uniq.append(c)
                if not uniq:
                    raise FlowError(f"lotte_apply_coupons: 상품 {idx+1} 쿠폰 옵션 없음")
                # ★ 최고 % 부터 시도. 모달 닫힘 = 적용 성공. 모달 유지 = 미적용(다른 상품용) → 다음 % .
                #   (본윤2종은 15/14% 적용불가, 12% 만 적용 — 실측 5/27)
                applied = False
                for c in uniq:
                    self.adb.tap(540, c["cy"])
                    time.sleep(0.8)
                    its2 = _ocr_now()
                    if _close_btn(its2):
                        self._log(f"    {_pct(c['text'])}% 미적용(모달 유지) → 다음 %")
                        continue
                    post_total = _get_total(its2)
                    self._log(f"    ✓ {_pct(c['text'])}% 적용 합계 {pre_total}→{post_total}")
                    applied = True
                    break
                if not applied:
                    cb = _close_btn(_ocr_now())
                    if cb:
                        self.adb.tap(cb["cx"], cb["cy"])
                    raise FlowError(f"lotte_apply_coupons: 상품 {idx+1} 적용가능 쿠폰 없음 (시도 {[_pct(c['text']) for c in uniq]})")
            items = _ocr_now()
            ok_btn = next((it for it in items if it["text"].strip() == "선택완료"), None)
            if ok_btn:
                self.adb.tap(ok_btn["cx"], ok_btn["cy"])
                self._log(f"  선택완료 tap")
                time.sleep(0.5)

        elif kind == "lotte_change_card":
            # ★ card_name 해석: action.card_name > vars[card_name_var]. silent default 금지
            #   (이전 '삼성카드' 하드 default = 잘못된 카드 결제 위험 — memory: feedback_lotte_daily_discount_card)
            card_name = action.get("card_name")
            if not card_name and action.get("card_name_var"):
                card_name = (vars or {}).get(action["card_name_var"])
            if not card_name:
                raise FlowError(
                    "lotte_change_card: card_name 미지정. vars['daily_discount_card'] 또는 "
                    "action.card_name 필요 (당일 banner 자동검출은 미구현 — 실제 banner 샘플 필요)")
            self._log(f"lotte_change_card target='{card_name}'")
            import subprocess
            def _ocr_now():
                subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_lf.png"], check=False, capture_output=True)
                subprocess.run(["adb", "pull", "/sdcard/_lf.png", self._tmp_img], check=False, capture_output=True)
                return _ocr_texts(self._tmp_img)
            items = _ocr_now()
            other_btn = next((it for it in items if "다른 결제수단" in it["text"]), None)
            if not other_btn:
                raise FlowError("lotte_change_card: '다른 결제수단' button 없음")
            self.adb.tap(80, other_btn["cy"])
            self._log(f"  다른결제수단 radio tap")
            time.sleep(0.5)
            items = _ocr_now()
            credit_btn = next((it for it in items if it["text"].strip() == "신용카드"), None)
            if not credit_btn:
                raise FlowError("lotte_change_card: '신용카드' button 없음")
            self.adb.tap(credit_btn["cx"], credit_btn["cy"])
            time.sleep(0.5)
            items = _ocr_now()
            pick_btn = next((it for it in items if "선택 해주세요" in it["text"] and 1800 < it["cy"] < 1950), None)
            if not pick_btn:
                raise FlowError("lotte_change_card: '선택 해주세요' dropdown 없음")
            self.adb.tap(pick_btn["cx"], pick_btn["cy"])
            time.sleep(0.5)
            items = _ocr_now()
            card_opt = next((it for it in items if card_name in it["text"]), None)
            if not card_opt:
                raise FlowError(f"lotte_change_card: '{card_name}' option 없음")
            self.adb.tap(card_opt["cx"], card_opt["cy"])
            self._log(f"  ✓ {card_name} tap")
            time.sleep(0.5)

        elif kind == "lotte_tap_agreement":
            import subprocess
            subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_lf.png"], check=False, capture_output=True)
            subprocess.run(["adb", "pull", "/sdcard/_lf.png", self._tmp_img], check=False, capture_output=True)
            items = _ocr_texts(self._tmp_img)
            ag = next((it for it in items if "동의(필수)" in it["text"]), None)
            if not ag:
                # 스크롤로 찾기 (1회만) — swipe up (cy 큰 → 작은) = 페이지 아래쪽 reveal
                self.adb.swipe(540, 2000, 540, 200, 600)
                time.sleep(0.5)
                subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_lf.png"], check=False, capture_output=True)
                subprocess.run(["adb", "pull", "/sdcard/_lf.png", self._tmp_img], check=False, capture_output=True)
                items = _ocr_texts(self._tmp_img)
                ag = next((it for it in items if "동의(필수)" in it["text"]), None)
            if not ag:
                raise FlowError("lotte_tap_agreement: '동의(필수)' text 없음")
            # text bbox 의 왼쪽 = checkbox 위치 (estimated -100~-150 from cx)
            checkbox_x = max(50, ag["cx"] - ag["w"] // 2 - 30)
            self.adb.tap(checkbox_x, ag["cy"])
            self._log(f"  동의 checkbox tap ({checkbox_x},{ag['cy']})")
            time.sleep(0.5)

        elif kind == "input_pin_fixed_layout":
            value = action.get("value")
            if value is None:
                key = action.get("var", "pin")
                value = vars.get(key) if vars else None
            if not value:
                raise FlowError(f"input_pin_fixed_layout: value 없음")
            delay = action.get("tap_delay_sec", 0.5)
            tap_ok = action.get("tap_ok_after", True)
            if not self.use_camera:
                raise FlowError("input_pin_fixed_layout: camera mode 필수 (screencap 차단됨)")
            self._log(f"input_pin_fixed_layout digits={len(value)} delay={delay}s")
            from .ocr_keypad import _load_gcv
            from google.cloud import vision
            import json as _json, cv2 as _cv2, numpy as _np
            # GCV OCR → digit cam 좌표 + ok button (row4 col3) 추정
            self._cap()
            client = _load_gcv()
            with open(self._tmp_img, "rb") as f:
                resp = client.text_detection(image=vision.Image(content=f.read()))
            cam_digits = {}
            for ann in resp.text_annotations[1:]:
                t = ann.description
                if t in ("0","1","2","3","4","5","6","7","8","9") and t not in cam_digits:
                    verts = ann.bounding_poly.vertices
                    cx = sum(v.x for v in verts) // 4
                    cy = sum(v.y for v in verts) // 4
                    if cy > 700:
                        cam_digits[t] = (cx, cy)
            # 키패드 grid 추정 — col_x cluster (3), row_y cluster (4) from 잡힌 digits
            if len(cam_digits) < 7:
                raise FlowError(f"input_pin_fixed_layout: OCR 숫자 {len(cam_digits)}개 잡힘 (≥7 필요)")
            col_xs = sorted(self._cluster_centers([cx for cx,_ in cam_digits.values()], 3))
            row_ys = sorted(self._cluster_centers([cy for _,cy in cam_digits.values()], 3))
            if len(col_xs) != 3 or len(row_ys) < 3:
                raise FlowError(f"input_pin_fixed_layout: col_xs={col_xs} row_ys={row_ys}")
            # row4 (back, 0, ok) 추정 — row3 - row2 차이 만큼 row3 아래
            row4_y = row_ys[2] + (row_ys[2] - row_ys[1])
            # 고정 layout: 1=col0/row0, 2=col1/row0, 3=col2/row0, ..., 0=col1/row3, ok=col2/row3
            keypad_cam = {
                "1": (col_xs[0], row_ys[0]), "2": (col_xs[1], row_ys[0]), "3": (col_xs[2], row_ys[0]),
                "4": (col_xs[0], row_ys[1]), "5": (col_xs[1], row_ys[1]), "6": (col_xs[2], row_ys[1]),
                "7": (col_xs[0], row_ys[2]), "8": (col_xs[1], row_ys[2]), "9": (col_xs[2], row_ys[2]),
                "0": (col_xs[1], row4_y),    "ok": (col_xs[2], row4_y),
            }
            # cam → phone matrix
            cal = self._calib
            src = _np.float32([cal["tl"], cal["tr"], cal["br"], cal["bl"]])
            dst = _np.float32([[0,0],[cal["phone_w"],0],[cal["phone_w"],cal["phone_h"]],[0,cal["phone_h"]]])
            M = _cv2.getPerspectiveTransform(src, dst)
            def _to_phone(cx, cy):
                pt = _np.array([[[cx, cy]]], dtype=_np.float32)
                out = _cv2.perspectiveTransform(pt, M)[0][0]
                return int(out[0]), int(out[1])
            phone_keys = {k: _to_phone(*v) for k, v in keypad_cam.items()}
            self._log(f"  keypad phone coords: {phone_keys}")
            # 각 digit tap
            for d in str(value):
                if d not in phone_keys:
                    raise FlowError(f"input_pin_fixed_layout: digit '{d}' phone coord 없음")
                px, py = phone_keys[d]
                self.adb.tap(px, py)
                self._log(f"  tap '{d}' → phone=({px},{py})")
                time.sleep(delay)
            if tap_ok and "ok" in phone_keys:
                px, py = phone_keys["ok"]
                self.adb.tap(px, py)
                self._log(f"  tap 'ok' → phone=({px},{py})")

        elif kind == "input_pin":
            value = action.get("value")
            if value is None:
                key = action.get("var", "pin")
                value = vars.get(key)
            if not value:
                raise FlowError(f"input_pin: value 없음 (kind={action.get('kind')})")
            # 페이싱: 카드사 PIN/결제코드 매 tap 사이 1.2초 (셔플 재배열 애니메이션 완료 wait)
            delay = action.get("tap_delay_sec", 1.2)
            # step 별 use_camera override — BC 같이 mixed (PIN=ADB, 7자리/결제PIN=camera) 카드 지원
            # 예: {"action":"input_pin", "kind":"bc_login6", "use_camera": false} → 이 step 만 ADB screencap
            _saved_use_camera = self.use_camera
            if "use_camera" in action:
                self.use_camera = bool(action["use_camera"])
                self._log(f"input_pin use_camera override → {self.use_camera}")
            # source=="dump": content-desc 기반 키패드 (롯데 로카페이 간편번호 등).
            # 셔플이어도 uiautomator dump 의 content-desc 로 숫자 위치 정확 — OCR/카메라 불필요.
            # 셔플은 화면 로드시 1회뿐([[feedback_shuffle_keypad_ocr_once]]) → 1회 dump 후 value 연속탭.
            if action.get("source") == "dump":
                import subprocess as _sp, xml.etree.ElementTree as _ET, re as _re2
                _sp.run(["adb","exec-out","uiautomator","dump","--compressed","/sdcard/_kp.xml"], capture_output=True)
                _sp.run(["adb","pull","/sdcard/_kp.xml","/tmp/_kp.xml"], capture_output=True)
                _BD = _re2.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
                ks = {}
                for n in _ET.parse("/tmp/_kp.xml").getroot().iter():
                    dd = (n.attrib.get("content-desc","") or "")
                    if dd.isdigit() and len(dd) == 1:
                        mm = _BD.match(n.attrib.get("bounds",""))
                        if mm:
                            x1,y1,x2,y2 = map(int, mm.groups()); ks[dd] = ((x1+x2)//2, (y1+y2)//2)
                need = set(value)
                if not need.issubset(ks.keys()):
                    raise FlowError(f"input_pin(dump): content-desc 키패드 부족 (got {sorted(ks)}, need {sorted(need)})")
                self._log(f"input_pin(dump) content-desc {len(ks)}/10 매핑 → {len(value)}자리 연속탭 (셔플 무관)")
                for _i, _d in enumerate(value, 1):
                    _x, _y = ks[_d]; self.adb.tap(_x, _y)
                    self._log(f"  [{_i}/{len(value)}] tap '{_d}' @ ({_x},{_y})"); time.sleep(delay)
                self.use_camera = _saved_use_camera
                return
            # 카메라 모드 = 카메라 frame 전체에서 OCR (y_min/y_max 무시)
            # ADB screencap 모드 = 폰 좌표 기준 키패드 영역 필터
            if self.use_camera:
                y_min, y_max = 0, 999999
                phone_y_min = action.get("phone_y_min", 1000)
                phone_y_max = action.get("phone_y_max", 2250)  # row 4 (y=2129) 포함
            else:
                y_min = action.get("y_min", 1600)
                y_max = action.get("y_max", 2200)
            # adaptive_calib: action 에 "screen" 명시되면 reference anchor 기반 매 캡쳐 transform 갱신
            # 폰 위치 살짝 변경 (전화 받고 폰 이동 등) 시 자동 보정
            screen_name = action.get("screen")  # e.g. "monimo_pc_pay"
            ref = None
            if screen_name and self.use_camera:
                from .adaptive_calib import load_reference
                ref = load_reference(screen_name)
                if ref:
                    self._log(f"input_pin: adaptive screen='{screen_name}' anchors={len(ref.get('anchors',[]))}")

            # 4엔진 voting OCR — kind 기반 preset 자동 매핑 (samsung_7digit_shuffle → samsung_code7 등)
            preset_name = action.get("preset")
            if not preset_name:
                kind_str = action.get("kind", "")
                # samsung_7digit_shuffle → samsung_code7, samsung_6digit_shuffle → samsung_pin6
                if "samsung_7digit" in kind_str or "samsung_login_6digit" in kind_str:
                    preset_name = "samsung_code7"
                elif "samsung_6digit" in kind_str:
                    preset_name = "samsung_pin6"
                # BC paybook — bc_code7 (FLAG_SECURE 7자리 fixed) / bc_pin6 (6자리 셔플 결제 비번) / bc_login6 (셔플 앱 로그인)
                elif "bc_7digit" in kind_str or "bc_code7" in kind_str:
                    preset_name = "bc_code7"
                elif "bc_6digit_login" in kind_str or "bc_login6" in kind_str:
                    preset_name = "bc_login6"
                elif "bc_6digit" in kind_str or "bc_pin6" in kind_str:
                    preset_name = "bc_pin6"
            preset = None
            if preset_name:
                from .ocr_keypad import KEYPAD_PRESETS
                preset = KEYPAD_PRESETS.get(preset_name)
                if preset:
                    self._log(f"input_pin preset='{preset_name}' (4-engine voting OCR)")

            self._log(f"input_pin kind={action.get('kind')} digits={len(value)} delay={delay}s mode={'camera' if self.use_camera else 'adb'}")
            ocr_retry = action.get("ocr_retry", 4)
            # ★ 시작 전 한 번 4엔진 voting 으로 0~9 매핑 → 캐시 사용
            # 1 digit missing 시 ADB dump button position (10 cell 고정) + cam→phone 변환 후 매칭으로 추정
            digits_cache: dict[str, tuple[int,int]] = {}
            items_full: list[dict] = []
            if preset and self.use_camera:
                from .ocr_keypad import vote_digits
                self._log(f"  [pre-vote] 0~9 매핑 시작 (multi-frame union)...")
                vote_attempts = action.get("vote_retry", 10)
                last_map = {}
                # multi-frame UNION mode — 매 frame OCR 결과 union, 다른 frame 못 잡은 digit 보완
                union_map: dict[str, tuple[int,int]] = {}
                # button 위치별로 각 frame OCR 가 잡은 digit 누적 — 같은 위치 (±50px) 면 같은 button 가정
                # vote_digits cam 좌표 → button-grid phone 좌표 매핑 (calibration 의존 X)
                # samsung_monimo button grid: row_y=[1583,1765,1947,2129], col_x=[200,540,880]
                # row 4 는 가운데 (540, 2129) 만 digit, 좌우 = 재배열/삭제
                BUTTON_ROW_Y = [1583, 1765, 1947, 2129]
                BUTTON_COL_X = [200, 540, 880]
                for va in range(vote_attempts):
                    self._cap()
                    full_map = vote_digits(
                        self._tmp_img,
                        flip_h=preset.get("flip_h", False),
                        roi_y_frac=preset.get("roi_y_frac"),
                        allow_partial=True,
                    )
                    if full_map and len(full_map) == 10:
                        # cam 좌표의 relative grid → button phone 좌표 매핑
                        cam_xs = sorted(set(cx for cx, cy in full_map.values()))
                        cam_ys = sorted(set(cy for cx, cy in full_map.values()))
                        # cluster cam x 를 3 col, cam y 를 4 row 로 분류
                        # k-means 단순화: 정렬 후 차이 ratio 로 col/row index
                        cam_col_centers = self._cluster_centers([cx for cx,_ in full_map.values()], 3)
                        cam_row_centers = self._cluster_centers([cy for _,cy in full_map.values()], 4)
                        if len(cam_col_centers) == 3 and len(cam_row_centers) >= 3:
                            mapped: dict[str, tuple[int,int]] = {}
                            for d, (cx, cy) in full_map.items():
                                ci = min(range(3), key=lambda i: abs(cam_col_centers[i] - cx))
                                ri = min(range(len(cam_row_centers)), key=lambda i: abs(cam_row_centers[i] - cy))
                                if ri >= 4: ri = 3
                                phone_x = BUTTON_COL_X[ci]
                                phone_y = BUTTON_ROW_Y[ri]
                                mapped[d] = (phone_x, phone_y)
                            # 검증 1: 10 unique phone 좌표여야
                            unique_pos = set(mapped.values())
                            # 검증 2: 재배열 (200,2129) / 삭제 (880,2129) 매핑 금지
                            FORBIDDEN = {(200, 2129), (880, 2129)}
                            forbidden_hit = unique_pos & FORBIDDEN
                            # 검증 3: row 4 (y=2129) 에 정확히 1 digit (가운데 col 1) 만
                            row4_count = sum(1 for (px, py) in mapped.values() if py == 2129)
                            if len(unique_pos) == 10 and not forbidden_hit and row4_count == 1:
                                digits_cache = mapped
                                digits_cache["__phone_coord__"] = (0, 0)
                                self._log(f"  [pre-vote] ✓ 10/10 + unique + valid (attempt {va+1})")
                                for d in sorted(mapped):
                                    if d.startswith("_"): continue
                                    self._log(f"    '{d}' → phone({mapped[d][0]},{mapped[d][1]})")
                                if ref:
                                    items_full = _ocr_texts(self._tmp_img)
                                break
                            else:
                                self._log(f"  [pre-vote] 매핑 invalid (unique={len(unique_pos)}/10, forbidden={forbidden_hit}, row4={row4_count}) — retry")
                    got = sorted(full_map.keys()) if full_map else []
                    self._log(f"  [pre-vote] attempt {va+1}/{vote_attempts} got={got}")
                    time.sleep(0.8)

                # 9/10 매핑이라도 사용 — missing 1 digit 은 button position 매칭으로 추정
                if not digits_cache and len(last_map) >= 9:
                    missing = set("0123456789") - set(last_map.keys())
                    self._log(f"  [pre-vote] 9/10 매핑 — missing {missing}, button-grid 매칭으로 추정")
                    # 9 cell 매핑됨. 키패드 10 cell 중 9 = 매핑 위치, 1 = 빈 cell.
                    # 키패드 button position (samsung — row 4 = 가운데만 digit):
                    button_grid_phone = [
                        (200, 1583), (540, 1583), (880, 1583),
                        (200, 1765), (540, 1765), (880, 1765),
                        (200, 1947), (540, 1947), (880, 1947),
                        (540, 2129),  # row 4 가운데 — 재배열/삭제는 제외
                    ]
                    # 매핑된 9 digit 의 cam→phone 변환 좌표
                    from .screen_ocr import camera_to_phone
                    mapped_phone = {d: camera_to_phone(cx, cy, self._calib) for d, (cx, cy) in last_map.items()}
                    # 각 button position 에 가장 가까운 매핑된 digit
                    used_digits = set()
                    button_to_digit = {}
                    for bi, (bx, by) in enumerate(button_grid_phone):
                        candidates = [(d, ((mapped_phone[d][0]-bx)**2 + (mapped_phone[d][1]-by)**2)**0.5)
                                      for d in mapped_phone if d not in used_digits]
                        if candidates:
                            candidates.sort(key=lambda x: x[1])
                            d, dist = candidates[0]
                            if dist < 250:  # 너무 멀면 매칭 X
                                button_to_digit[bi] = d
                                used_digits.add(d)
                    # 매칭 안 된 button position = missing digit
                    unused_button = [bi for bi in range(10) if bi not in button_to_digit]
                    if len(unused_button) == 1 and len(missing) == 1:
                        miss_d = list(missing)[0]
                        miss_bx, miss_by = button_grid_phone[unused_button[0]]
                        digits_cache = dict(mapped_phone)
                        digits_cache[miss_d] = (miss_bx, miss_by)
                        self._log(f"  [pre-vote] ✓ missing '{miss_d}' 추정 = phone({miss_bx},{miss_by}) (button #{unused_button[0]})")
                        # 캐시는 phone 좌표 — 변환 skip 마커
                        digits_cache["__phone_coord__"] = (0, 0)
                    else:
                        raise FlowError(f"pre-vote 매칭 ambiguous: unused_buttons={unused_button}, missing={missing}")
                elif not digits_cache:
                    raise FlowError(f"input_pin pre-vote 실패: 9+ digit 매핑 못 함 ({vote_attempts} attempt)")
                else:
                    pass  # 완전 매핑 OK

            # ADB(screencap) 모드 셔플 키패드: single-pass 4-engine vote_digits.
            # 셔플은 화면 로드시 1회뿐 → 매 키 재OCR 금지([[feedback_shuffle_keypad_ocr_once]]).
            # 단일엔진 _ocr_digits는 '0' 등 누락(5/29 BC 7/10) → vote_digits 4엔진. 결과=이미 phone 픽셀 좌표.
            if not digits_cache and not self.use_camera and preset:
                from .ocr_keypad import vote_digits
                need = set(value)
                for va in range(action.get("vote_retry", 4)):
                    self._cap()
                    vd = vote_digits(self._tmp_img, flip_h=preset.get("flip_h", False),
                                     roi_y_frac=preset.get("roi_y_frac"), allow_partial=True) or {}
                    if need.issubset(vd.keys()):
                        digits_cache = dict(vd)
                        digits_cache["__phone_coord__"] = (0, 0)  # phone 좌표 → cam→phone 변환 skip
                        self._log(f"  [adb vote] {len(vd)}/10 ✓ need={sorted(need)} (attempt {va+1})")
                        break
                    self._log(f"  [adb vote] attempt {va+1}: got {sorted(vd.keys())}, need {sorted(need)} — retry")
                    time.sleep(0.5)
                if not digits_cache:
                    raise FlowError(f"input_pin(adb): vote_digits가 PIN digit 전부 못잡음 (need {sorted(set(value))})")

            cache_is_phone_coord = digits_cache.pop("__phone_coord__", None) is not None
            for i, d in enumerate(value, 1):
                if digits_cache:
                    # 캐시 사용 — 매 키 사이 OCR 안 함 (셔플 무효 회피)
                    digits = digits_cache
                else:
                    # fallback: preset 없거나 cache 실패 시 매 키 OCR
                    for attempt in range(ocr_retry):
                        self._cap()
                        digits = _ocr_digits(self._tmp_img, y_min, y_max)
                        if d in digits: break
                        time.sleep(1.0)
                if d not in digits:
                    raise FlowError(f"input_pin: digit '{d}' not in cache @ step {i} (cache={sorted(digits.keys())})")
                x, y = digits[d]
                # 카메라 → 폰 좌표 변환: cache 가 이미 phone 좌표면 skip (button-grid 매칭 결과)
                if self.use_camera and not cache_is_phone_coord:
                    transform = None
                    if ref and items_full:
                        from .adaptive_calib import build_transform_from_anchors, cam_to_phone_adaptive
                        transform = build_transform_from_anchors(items_full, ref)
                    if transform:
                        from .adaptive_calib import cam_to_phone_adaptive
                        px, py = cam_to_phone_adaptive(x, y, transform)
                        self._log(f"  [{i}/{len(value)}] adaptive transform ({transform['n_anchors']}anc) cam({x},{y}) → phone({px},{py})")
                    else:
                        from .screen_ocr import camera_to_phone
                        px, py = camera_to_phone(x, y, self._calib)
                        if ref:
                            self._log(f"  [{i}/{len(value)}] adaptive 매칭 실패 ({sum(1 for a in ref['anchors'] if find_text(items_full, a['text']))}/{len(ref['anchors'])} anchor) — static calib 으로 fallback")
                    if not (phone_y_min <= py <= phone_y_max):
                        raise FlowError(
                            f"input_pin: '{d}' 폰좌표 ({px},{py}) 키패드영역 ({phone_y_min}~{phone_y_max}) 밖 — 잘못 OCR된 숫자"
                        )
                    x, y = px, py
                self._log(f"  [{i}/{len(value)}] tap '{d}' @ phone({x},{y})")
                self.adb.tap(x, y)
                time.sleep(delay)
            # use_camera step-override 복원 (저장만 하고 복원 안 하던 버그 fix)
            self.use_camera = _saved_use_camera

        elif kind == "launch":
            pkg = action.get("package")
            if not pkg:
                raise FlowError("launch: 'package' 필요")
            self._log(f"launch {pkg}")
            self.adb._shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            time.sleep(action.get("post_sleep_sec", 0.5))

        elif kind == "swipe":
            if "from_xy" in action:
                x1, y1 = action["from_xy"]; x2, y2 = action["to_xy"]
            elif "from" in action:
                x1, y1 = action["from"]; x2, y2 = action["to"]
            else:
                raise FlowError("swipe: 'from_xy'+'to_xy' 필요")
            dur = action.get("duration_ms", 300)
            self._log(f"swipe ({x1},{y1})→({x2},{y2}) {dur}ms")
            self.adb.swipe(x1, y1, x2, y2, dur)
            time.sleep(action.get("post_sleep_sec", 0.3))

        elif kind == "dismiss_alert_if_present":
            # 팝업(동의 미체크 alert 등) 즉시 닫기 — '확인'/'닫기' button. dump 우선, OCR fallback.
            import xml.etree.ElementTree as ET
            labels = action.get("labels", ["확인", "닫기", "예"])
            BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
            tmp = "/tmp/_alert.xml"
            tapped = False
            try:
                self.adb.dump_ui(tmp)
                root = ET.parse(tmp).getroot()
            except Exception:
                root = None
            if root is not None:
                for n in root.iter():
                    t = (n.attrib.get("text", "") or "").strip()
                    if t in labels and n.attrib.get("clickable", "") == "true":
                        m = BOUNDS.match(n.attrib.get("bounds", ""))
                        if m:
                            x1, y1, x2, y2 = map(int, m.groups())
                            self.adb.tap((x1 + x2) // 2, (y1 + y2) // 2)
                            self._log(f"  alert(dump) '{t}' tap")
                            tapped = True
                            break
            if not tapped:
                # WebView alert 은 dump 빈약 → OCR fallback (체크아웃 화면 = screencap OK)
                self._cap()
                items = _ocr_texts(self._tmp_img)
                hit = next((it for it in items if it["text"].strip() in labels), None)
                if hit:
                    if self.use_camera:
                        from .screen_ocr import camera_to_phone
                        px, py = camera_to_phone(hit["cx"], hit["cy"], self._calib)
                    else:
                        px, py = hit["cx"], hit["cy"]
                    self.adb.tap(px, py)
                    self._log(f"  alert(OCR) '{hit['text']}' tap @ ({px},{py})")
                    tapped = True
            if not tapped:
                self._log("  팝업 없음, skip")
            time.sleep(action.get("post_sleep_sec", 0.5))

        elif kind == "lotte_cart_select_all":
            # 장바구니 '일반 (n/m)' 그룹 전체선택 체크박스 tap (전체 상품 주문 필수).
            # 체크박스 = 헤더 행 좌측 끝 절대 x≈60 ('(n/m)' 텍스트가 폭 변동 → 상대 offset 불가, 실측 5/27).
            # (n/m) 카운트로 self-verify + retry. WebView 체크아웃 = FLAG_SECURE 아님 → screencap OCR.
            def _hdr():
                self.adb.screencap(self._tmp_img)
                its = _ocr_texts(self._tmp_img)
                return next((it for it in its if "일반" in it["text"] and it["cy"] < 900), None)
            def _sel_state(h):
                m = re.search(r"\((\d+)\s*/\s*(\d+)\)", h["text"])
                return (int(m.group(1)), int(m.group(2))) if m else (None, None)
            hdr = _hdr()
            if not hdr:
                raise FlowError("lotte_cart_select_all: '일반' 그룹 헤더 없음 (빈 장바구니/미진입?)")
            checkbox_x = action.get("checkbox_x", 60)
            for attempt in range(2):
                sel, tot = _sel_state(hdr)
                if sel is not None and tot and sel == tot:
                    self._log(f"  ✓ 전체선택됨 ({sel}/{tot})")
                    return
                self.adb.tap(checkbox_x, hdr["cy"])
                self._log(f"  전체선택 tap ({checkbox_x},{hdr['cy']}) '{hdr['text']}' (attempt {attempt+1})")
                time.sleep(action.get("post_sleep_sec", 0.6))
                hdr = _hdr()
                if not hdr:
                    raise FlowError("lotte_cart_select_all: tap 후 헤더 재검출 실패")
            sel, tot = _sel_state(hdr)
            if not (tot and sel == tot):
                raise FlowError(f"lotte_cart_select_all: 전체선택 실패 ('{hdr['text']}')")
            self._log(f"  ✓ 전체선택됨 ({sel}/{tot})")

        elif kind == "tap_then_expect":
            # 단일 tap 보장 + 화면 전환 검증 + 팝업 자동 닫기.
            #   expect_text 있음 → tap 후 등장 확인, 미전환 시 alert dismiss + 1회 재tap (두번 결제 방지)
            #   expect_text 없음 → 1회 tap 후 팝업만 즉시 닫음 (결제하기처럼 다음 화면 미지정)
            target = action.get("text")
            if not target:
                raise FlowError("tap_then_expect: 'text' 필요")
            expect = action.get("expect_text")
            dismiss_labels = action.get("dismiss_labels", ["확인", "닫기"])
            settle = action.get("post_sleep_sec", 3.0)

            def _ocr_now():
                self._cap()
                return _ocr_texts(self._tmp_img)

            def _to_phone(it):
                if self.use_camera:
                    from .screen_ocr import camera_to_phone
                    return camera_to_phone(it["cx"], it["cy"], self._calib)
                return it["cx"], it["cy"]

            for attempt in range(2):
                items = _ocr_now()
                if expect and any(expect in it["text"] for it in items):
                    self._log(f"  이미 '{expect}' 도달 — '{target}' tap 생략")
                    return
                tgt = next((it for it in items if target in it["text"]), None)
                if not tgt:
                    raise FlowError(f"tap_then_expect: '{target}' OCR 미발견 (attempt {attempt+1})")
                px, py = _to_phone(tgt)
                self.adb.tap(px, py)
                self._log(f"  tap '{target}' @ ({px},{py}) (attempt {attempt+1})")
                if not expect:
                    # 다음 화면 미지정 — tap 후 팝업만 즉시 닫고 종료
                    time.sleep(settle)
                    self.run_action({"action": "dismiss_alert_if_present", "labels": dismiss_labels})
                    return
                deadline = time.time() + settle
                while time.time() < deadline:
                    if any(expect in it["text"] for it in _ocr_now()):
                        self._log(f"  ✓ '{expect}' 도달 — 전환 성공")
                        time.sleep(0.5)
                        return
                    time.sleep(0.7)
                self._log(f"  '{expect}' 미도달 — alert dismiss 후 재시도")
                self.run_action({"action": "dismiss_alert_if_present", "labels": dismiss_labels})
            raise FlowError(f"tap_then_expect: '{target}' tap 후 '{expect}' 전환 실패 (2회)")

        else:
            raise FlowError(f"unknown action: {kind}")

    def run(self, flow: list[dict], vars: dict | None = None):
        # 매 카드앱 진입 전 자동 calibration — 폰/카메라 살짝 변경 대비
        if self.use_camera:
            self._log("=== auto-calibration (홈 화면 OCR + home_apps 매칭) ===")
            self.calibrate_from_home()
        for i, action in enumerate(flow, 1):
            self._log(f"=== step {i}/{len(flow)} ===")
            try:
                self.run_action(action, vars)
            except FlowError as e:
                self._log(f"✗ step {i} fail: {e}")
                raise
        self._log("=== flow complete ===")


def _main():
    import sys
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__); return
    coords_name = args[0]      # e.g. "hyundai_card", "bc_paybook_isp"
    flow_key = args[1] if len(args) > 1 else "flow_payment"
    data = load_coords(f"apps/{coords_name}")
    flow = data.get(flow_key)
    if not flow:
        print(f"flow '{flow_key}' not in apps/{coords_name}.json"); return
    # parse --pin / --code from args
    vars = {}
    for a in args[2:]:
        if a.startswith("--pin="): vars["pin"] = a.split("=",1)[1]
        elif a.startswith("--code="): vars["code"] = a.split("=",1)[1]
        elif a.startswith("--card="): vars["daily_discount_card"] = a.split("=",1)[1]
    runner = FlowRunner()
    runner.run(flow, vars)


if __name__ == "__main__":
    _main()
