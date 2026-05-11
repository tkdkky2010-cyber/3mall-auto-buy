"""캘리브레이션 도구 — 하드웨어 셋업 후 1회 실행.

흐름:
1. 폰을 무선 충전대 정해진 위치에 올림 (충전대 모서리에 폰 모서리 정렬)
2. KB Pay 비번 6자리 셔플 키패드 화면 띄움
3. 이 스크립트 실행:
   - 카메라 캡처 → 사용자가 화면 보고 키패드 영역을 마우스로 드래그 (bbox)
   - 또는 자동 검출 (그리드 패턴 인식)
4. reference 프레임 + bbox + grid 정보 저장 → calibration/ 에 영구 보관
5. 또한 0-9 reference 이미지도 같은 sprite에서 추출 (사용자가 셔플된 숫자 라벨링)

사용:
    python3 -m buy.phase3.calibrate
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from . import camera, alignment


def select_keypad_bbox_interactive(frame: np.ndarray) -> tuple[int, int, int, int]:
    """OpenCV window로 사용자가 키패드 영역 드래그해서 선택."""
    print("키패드 영역을 마우스로 드래그하세요. 끝나면 ENTER, 취소는 c.")
    bbox = cv2.selectROI("Select Keypad Area", frame, fromCenter=False, showCrosshair=True)
    cv2.destroyAllWindows()
    x, y, w, h = bbox
    return (x, y, x + w, y + h)


def label_cells_interactive(frame: np.ndarray, bbox: tuple[int, int, int, int], grid: tuple[int, int]) -> dict[tuple[int, int], int]:
    """각 셀에 대해 사용자가 보고 숫자 입력 → cell (r, c) → digit 매핑 생성."""
    x1, y1, x2, y2 = bbox
    rows, cols = grid
    cell_h = (y2 - y1) // rows
    cell_w = (x2 - x1) // cols

    cell_to_digit = {}
    for r in range(rows):
        for c in range(cols):
            cy1, cy2 = y1 + r * cell_h, y1 + (r + 1) * cell_h
            cx1, cx2 = x1 + c * cell_w, x1 + (c + 1) * cell_w
            cell = frame[cy1:cy2, cx1:cx2]
            cv2.imshow(f"Cell ({r},{c})", cell)
            cv2.waitKey(100)
            print(f"셀 ({r},{c}): 보이는 숫자 입력 (0-9, 빈칸이면 ENTER): ", end="")
            digit = input().strip()
            cv2.destroyAllWindows()
            if digit.isdigit():
                cell_to_digit[(r, c)] = int(digit)
    return cell_to_digit


def save_keypad_refs(frame: np.ndarray, bbox: tuple[int, int, int, int], grid: tuple[int, int], cell_to_digit: dict):
    """각 셀 이미지를 0-9 reference로 저장."""
    refs_dir = Path(__file__).resolve().parent / "keypad_refs"
    refs_dir.mkdir(exist_ok=True)
    x1, y1, x2, y2 = bbox
    rows, cols = grid
    cell_h = (y2 - y1) // rows
    cell_w = (x2 - x1) // cols

    for (r, c), digit in cell_to_digit.items():
        cy1, cy2 = y1 + r * cell_h, y1 + (r + 1) * cell_h
        cx1, cx2 = x1 + c * cell_w, x1 + (c + 1) * cell_w
        cell = frame[cy1:cy2, cx1:cx2]
        cv2.imwrite(str(refs_dir / f"{digit}.png"), cell)
        print(f"  {digit}.png saved from cell ({r},{c})")


def main():
    print("=== KB Pay 키패드 캘리브레이션 ===")
    print("1) 폰을 정확히 정해진 위치에 올렸나? (충전대 모서리에 폰 모서리 정렬)")
    print("2) 폰 KB Pay → 결제 비번 입력 화면 (6자리 셔플 키패드) 띄웠나?")
    input("준비되면 ENTER: ")

    print("\n카메라 캡처 중...")
    frame = camera.capture_frame(cam_index=0)
    if frame is None:
        print("[ERR] 카메라 캡처 실패. 웹캠 연결 확인.")
        return
    print(f"캡처 OK: {frame.shape}")

    # 키패드 영역 선택
    bbox = select_keypad_bbox_interactive(frame)
    print(f"키패드 bbox: {bbox}")

    # 그리드 (KB Pay 기본은 3열 4행)
    grid_in = input("그리드 (rows,cols) [기본 4,3]: ").strip()
    if grid_in:
        rows, cols = [int(x) for x in grid_in.split(",")]
    else:
        rows, cols = 4, 3

    # 각 셀의 숫자 라벨링 (캘리브레이션 시점의 셔플 순서)
    print(f"\n{rows}x{cols} 그리드. 각 셀에 보이는 숫자를 라벨링하세요.")
    cell_to_digit = label_cells_interactive(frame, bbox, (rows, cols))

    # 0-9 reference 이미지 저장
    print("\n0-9 reference 이미지 저장 중...")
    save_keypad_refs(frame, bbox, (rows, cols), cell_to_digit)

    # alignment용 reference 프레임 저장
    alignment.save_calibration(frame, bbox)
    print("\n[OK] 캘리브레이션 완료. 다음 운영부터 자동 OCR 동작.")


if __name__ == "__main__":
    main()
