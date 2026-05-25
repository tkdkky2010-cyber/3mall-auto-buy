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
                 use_camera: bool | None = None, cam_idx: int = 0):
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
        new_calib = {
            "cam_w": 1920, "cam_h": 1080,
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
        if self.use_camera:
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
            # 1. dump 기반 닫기 button
            btns = _dump_close_buttons(self.adb)
            if btns:
                x, y = btns[0]
                self._log(f"  close button @ ({x},{y})")
                self.adb.tap(x, y)
                time.sleep(0.8)
                return
            # 2. OCR 기반 '×'
            self._cap()
            items = _ocr_texts(self._tmp_img)
            for it in items:
                if it["text"] in ("×", "x", "X") and it["w"] < 80:
                    self._log(f"  OCR × @ ({it['cx']},{it['cy']})")
                    self.adb.tap(it["cx"], it["cy"])
                    time.sleep(0.8)
                    return
            # 3. hint 좌표
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
                for n in root.iter():
                    if target in (n.attrib.get("text", "") or ""):
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

        elif kind == "input_pin":
            value = action.get("value")
            if value is None:
                key = action.get("var", "pin")
                value = vars.get(key)
            if not value:
                raise FlowError(f"input_pin: value 없음 (kind={action.get('kind')})")
            # 페이싱: 카드사 PIN/결제코드 매 tap 사이 1.2초 (셔플 재배열 애니메이션 완료 wait)
            delay = action.get("tap_delay_sec", 1.2)
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
            if not preset_name and self.use_camera:
                kind_str = action.get("kind", "")
                # samsung_7digit_shuffle → samsung_code7, samsung_6digit_shuffle → samsung_pin6
                if "samsung_7digit" in kind_str or "samsung_login_6digit" in kind_str:
                    preset_name = "samsung_code7"
                elif "samsung_6digit" in kind_str:
                    preset_name = "samsung_pin6"
            preset = None
            if preset_name and self.use_camera:
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
    runner = FlowRunner()
    runner.run(flow, vars)


if __name__ == "__main__":
    _main()
