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
    def __init__(self, adb: ADB | None = None, verbose: bool = True):
        self.adb = adb or ADB()
        self.verbose = verbose
        self._tmp_img = "/tmp/_flow.png"

    def _log(self, msg: str):
        if self.verbose: print(f"[flow] {msg}")

    def _cap(self):
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
            delay = action.get("tap_delay_sec", 0.7)
            y_min = action.get("y_min", 1600)
            y_max = action.get("y_max", 2200)
            self._log(f"input_pin kind={action.get('kind')} digits={len(value)} delay={delay}s")
            for i, d in enumerate(value, 1):
                self._cap()
                digits = _ocr_digits(self._tmp_img, y_min, y_max)
                if d not in digits:
                    raise FlowError(f"input_pin: digit '{d}' not in OCR @ step {i} (got {sorted(digits)})")
                x, y = digits[d]
                self._log(f"  [{i}/{len(value)}] tap '{d}' @ ({x},{y})")
                self.adb.tap(x, y)
                time.sleep(delay)

        else:
            raise FlowError(f"unknown action: {kind}")

    def run(self, flow: list[dict], vars: dict | None = None):
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
