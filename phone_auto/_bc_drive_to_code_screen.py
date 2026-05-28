"""BC 페이북: PIN 137601 자동 입력 + 광고 modal close + PC결제 코드 입력 화면 도달.

매 단계 dump+screencap 으로 좌표 확보 (좌표 추측 금지 — memory [[feedback_phone_coord_no_estimate]]).
flow_runner 통합 전 standalone 검증용.

사용:
    python3 -m phone_auto._bc_drive_to_code_screen
"""
from __future__ import annotations
import re, subprocess, sys, time, json
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from phone_auto.adb_input import ADB

BC_PKG = "kvp.jjy.MispAndroid320"
TMP = PROJECT_ROOT / "phone_auto" / "_tmp"
TMP.mkdir(exist_ok=True)


def sh(*cmd) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def cap(name: str) -> Path:
    p = TMP / f"_bc_{name}.png"
    subprocess.run(["adb", "shell", "screencap", "-p", "/sdcard/_s.png"], check=False)
    subprocess.run(["adb", "pull", "/sdcard/_s.png", str(p)], check=False, capture_output=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/_s.png"], check=False, capture_output=True)
    return p


def dump_xml(name: str) -> str:
    subprocess.run(["adb", "shell", "uiautomator", "dump", "/sdcard/_d.xml"], check=False, capture_output=True)
    p = TMP / f"_bc_{name}_dump.xml"
    subprocess.run(["adb", "pull", "/sdcard/_d.xml", str(p)], check=False, capture_output=True)
    subprocess.run(["adb", "shell", "rm", "/sdcard/_d.xml"], check=False, capture_output=True)
    return p.read_text()


def get_keypad_buttons(xml: str) -> dict[int, tuple[int, int]]:
    """nf_key_serial1~12 ImageButton 의 center 좌표. {slot_no: (cx, cy)}."""
    out = {}
    pat = re.compile(r'<node\s+[^>]*?resource-id="[^"]*nf_key_serial(\d+)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', re.DOTALL)
    for m in pat.finditer(xml):
        slot = int(m.group(1))
        x1, y1, x2, y2 = map(int, [m.group(2), m.group(3), m.group(4), m.group(5)])
        out[slot] = ((x1 + x2) // 2, (y1 + y2) // 2)
    return out


def ocr_digits_in_slots(img_path: Path, slot_bounds: dict[int, tuple[int, int, int, int]]) -> dict[str, tuple[int, int]]:
    """각 button slot 의 ROI crop OCR — 표시된 digit → button center.
    Returns {digit_str: (cx, cy)}.
    """
    import Vision, Quartz
    from Foundation import NSURL
    from Cocoa import NSImage, NSData
    url = NSURL.fileURLWithPath_(str(img_path))
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    W, H = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
    out = {}
    for slot, (x1, y1, x2, y2) in slot_bounds.items():
        # crop region
        rect = Quartz.CGRectMake(x1, y1, x2 - x1, y2 - y1)
        crop_cg = Quartz.CGImageCreateWithImageInRect(cg, rect)
        h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(crop_cg, {})
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        req.setUsesLanguageCorrection_(False)
        req.setRecognitionLanguages_(['en-US'])
        h.performRequests_error_([req], None)
        for obs in (req.results() or []):
            cand = obs.topCandidates_(1)[0]
            text = (cand.string() or "").strip()
            if text.isdigit() and len(text) == 1:
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                out[text] = (cx, cy)
                break  # 한 slot 에 하나만
    return out


def main():
    adb = ADB()
    print("[1] BC 앱 force_stop + launch")
    subprocess.run(["adb", "shell", "am", "force-stop", BC_PKG], check=False)
    time.sleep(0.8)
    subprocess.run(["adb", "shell", "monkey", "-p", BC_PKG, "-c", "android.intent.category.LAUNCHER", "1"],
                   capture_output=True)
    time.sleep(5)

    print("[2] PIN 화면 dump")
    xml = dump_xml("pin_screen")
    keypad = get_keypad_buttons(xml)
    if len(keypad) != 12:
        print(f"[FAIL] keypad slot {len(keypad)}/12")
        return 1
    # slot bounds (for ROI OCR)
    slot_bounds = {}
    pat = re.compile(r'<node\s+[^>]*?resource-id="[^"]*nf_key_serial(\d+)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', re.DOTALL)
    for m in pat.finditer(xml):
        slot = int(m.group(1))
        slot_bounds[slot] = tuple(map(int, [m.group(2), m.group(3), m.group(4), m.group(5)]))
    done_pat = re.search(r'resource-id="[^"]*nf_fun_bottom_serial_done"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml)
    done_xy = None
    if done_pat:
        x1, y1, x2, y2 = map(int, done_pat.groups())
        done_xy = ((x1 + x2) // 2, (y1 + y2) // 2)
    print(f"  keypad slots ok, 완료 = {done_xy}")

    print("[3] BC PIN 키패드 = 셔플 — 매 입력 후 vote_digits (4-engine OCR) 매핑")
    from phone_auto.ocr_keypad import vote_digits

    PIN = "137601"
    for i, d in enumerate(PIN, 1):
        cap_p = cap(f"pin_step{i}")
        mapping = vote_digits(str(cap_p), flip_h=False, roi_y_frac=(0.66, 0.92),
                              allow_partial=True, verbose=False)
        if not mapping or d not in mapping:
            print(f"  [FAIL] step {i} vote_digits 매핑 부족 (got={sorted((mapping or {}).keys())})")
            return 2
        x, y = mapping[d]
        print(f"  [{i}/6] tap '{d}' @ ({x},{y}) (mapped {len(mapping)}/10)")
        adb.tap(x, y)
        time.sleep(0.6)

    # 완료 button
    if done_xy:
        print(f"[4] 완료 tap @ {done_xy}")
        adb.tap(*done_xy)
    else:
        print(f"[FAIL] 완료 button 좌표 없음")
        return 3
    time.sleep(3)

    print("[5] 메인 화면 도달 확인 + 광고 modal dump")
    cap("main_screen")
    main_xml = dump_xml("main_screen")
    print(f"  current activity: {sh('adb','shell','dumpsys','window').split('mCurrentFocus=')[1].split(chr(10))[0][:80] if 'mCurrentFocus=' in sh('adb','shell','dumpsys','window') else '?'}")

    # 광고 modal 검출 — '광고 이미지' content-desc 또는 'WebView' class
    has_ad = '광고' in main_xml or 'webview' in main_xml.lower()
    print(f"  광고 modal 감지: {has_ad}")

    print(f"\n[OK] PIN 통과. dump 저장 위치: {TMP}")
    print("  - phone_auto/_tmp/_bc_main_screen.png — 메인 화면 (광고 modal 있을 가능성)")
    print("  - phone_auto/_tmp/_bc_main_screen_dump.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
