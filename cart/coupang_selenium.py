"""쿠팡 직접 크롤링 — Selenium + undetected-chromedriver + BS4 파싱.

쿠팡 차단 (Akamai Bot Manager) 회피:
- undetected-chromedriver: Selenium의 stealth 패치판 (webdriver flag/CDP flag 제거)
- 영구 프로필 (~/CoupangChrome): cookies/_abck 유지 (hmall 자동화의 ~/HmallChrome 와 격리)
- 자연 동작: 메인 페이지 warmup → 랜덤 sleep(2~4s) → 부드러운 scroll
- Akamai sensor data JS 자동 실행 후 실제 페이지 도달까지 wait

사용법:
    python3 cart/coupang_direct.py --search "곡물도감 서리태"
    python3 cart/coupang_direct.py --category 305798       # 카테고리 Top
    python3 cart/coupang_direct.py --product 7290082549    # 상품 상세

첫 실행: GUI Chrome 창 뜸. 캡차 등장 시 사람이 1회 풀어주면 cookies 유지됨.
"""
from __future__ import annotations
import argparse
import json
import random
import re
import sys
import time
import urllib.parse
from pathlib import Path

import undetected_chromedriver as uc
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup

PROFILE_DIR = Path.home() / "CoupangChrome"


def _launch_driver(headless: bool = False) -> uc.Chrome:
    """undetected-chromedriver 인스턴스 (격리 영구 프로필)."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    opts = uc.ChromeOptions()
    opts.add_argument(f"--user-data-dir={PROFILE_DIR}")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    # locale
    prefs = {"intl.accept_languages": "ko-KR,ko"}
    opts.add_experimental_option("prefs", prefs)
    return uc.Chrome(options=opts, headless=headless, use_subprocess=True)


def _human_wait(low: float = 2.0, high: float = 4.0) -> None:
    time.sleep(random.uniform(low, high))


def _natural_scroll(driver, total: int = 1200, step: int = 400) -> None:
    """자연스러운 분할 스크롤 (1 frame씩 내려가지 말고 사람 처럼 끊어 내림)."""
    for y in range(0, total, step):
        driver.execute_script(f"window.scrollBy({{top: {step}, behavior: 'smooth'}})")
        time.sleep(random.uniform(0.4, 0.9))


def _warmup(driver) -> None:
    """쿠팡 메인 페이지 진입 + cookies 획득."""
    if "coupang.com" not in (driver.current_url or ""):
        driver.get("https://www.coupang.com/")
        _human_wait(2.5, 4)
        _natural_scroll(driver, total=800)
        _human_wait(1.0, 2.0)


VERBOSE = False


def _wait_for_real_page(driver, timeout: int = 30) -> bool:
    """Akamai 챌린지 페이지가 사라지고 실제 HTML 로 바뀔 때까지 대기."""
    last_len = -1
    for i in range(timeout):
        html = driver.page_source or ""
        is_blocked = "Access Denied" in html
        # Akamai sensor 페이지는 2~3KB 로 매우 작고 첫 줄에 sensor script src 있음
        is_akamai_sensor = len(html) < 10000 and (
            "/YIEnD9uBMl2Tq1ut0pzy/" in html or '<script type="text/javascript" src="/' in html[:500]
        )
        is_cf = "Just a moment" in html
        if VERBOSE and (i == 0 or len(html) != last_len):
            print(f"  [wait {i:>2}s] len={len(html):>6} blocked={is_blocked} sensor={is_akamai_sensor} cf={is_cf}",
                  file=sys.stderr)
            last_len = len(html)
        if len(html) > 30000 and not is_blocked and not is_akamai_sensor and not is_cf:
            return True
        time.sleep(1)
    if VERBOSE:
        print(f"  [wait] timeout — url={driver.current_url}", file=sys.stderr)
        print(f"  [wait] HTML head: {html[:400]}", file=sys.stderr)
    return False


# ────────────── 핵심 API ──────────────


def search(query: str, driver, limit: int = 30) -> list[dict]:
    _warmup(driver)
    url = f"https://www.coupang.com/np/search?q={urllib.parse.quote(query)}&channel=user"
    driver.get(url)
    _human_wait(2.5, 4)
    if not _wait_for_real_page(driver):
        return []
    _natural_scroll(driver, total=1600)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    return _parse_product_list(soup, limit=limit)


def category(category_id: int | str, driver, page_num: int = 1, limit: int = 30) -> list[dict]:
    _warmup(driver)
    url = f"https://www.coupang.com/np/categories/{category_id}?page={page_num}"
    driver.get(url)
    _human_wait(2.5, 4)
    if not _wait_for_real_page(driver):
        return []
    _natural_scroll(driver, total=1600)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    return _parse_product_list(soup, limit=limit)


def product_detail(product_id: str | int, driver, vendor_item_id: str | int | None = None) -> dict:
    _warmup(driver)
    url = f"https://www.coupang.com/vp/products/{product_id}"
    if vendor_item_id:
        url += f"?vendorItemId={vendor_item_id}"
    driver.get(url)
    _human_wait(2.5, 4)
    if not _wait_for_real_page(driver):
        return {"error": "challenge or blocked"}
    _natural_scroll(driver, total=600)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    out: dict = {"productId": str(product_id),
                 "vendorItemId": str(vendor_item_id) if vendor_item_id else None,
                 "url": driver.current_url}
    title = soup.select_one("h1.prod-buy-header__title, h2.prod-buy-header__title")
    out["name"] = title.get_text(strip=True) if title else None
    for sel in ["span.total-price > strong", "strong.price-value"]:
        el = soup.select_one(sel)
        if el:
            m = re.search(r"([\d,]+)", el.get_text(strip=True))
            if m:
                out["current_price"] = int(m.group(1).replace(",", ""))
                break
    orig = soup.select_one("span.origin-price, del.base-price")
    if orig:
        m = re.search(r"([\d,]+)", orig.get_text(strip=True))
        if m:
            out["original_price"] = int(m.group(1).replace(",", ""))
    dr = soup.select_one("span.discount-percentage")
    if dr:
        m = re.search(r"(\d+)", dr.get_text(strip=True))
        if m:
            out["discount_pct"] = int(m.group(1))
    rv = soup.select_one("span.count")
    if rv:
        m = re.search(r"(\d+(?:,\d+)*)", rv.get_text(strip=True))
        if m:
            out["review_count"] = int(m.group(1).replace(",", ""))
    return out


def _parse_product_list(soup: BeautifulSoup, limit: int = 30) -> list[dict]:
    """쿠팡 React SPA — ul#product-list > li.ProductUnit_productUnit__* 기반 파싱.
    각 li 내부 a[href*='/vp/products/'] 가 상품 카드 link.
    가격: <del>(정가) + <span>현재가</span> + '최대 N원 적립'.
    """
    out: list[dict] = []
    # 새 React SPA selector 우선, fall back 으로 옛 selector
    items = (soup.select('li[class*="ProductUnit_productUnit"]')
             or soup.select("ul#productList > li")
             or soup.select("li.search-product")
             or soup.select("li.baby-product"))
    for li in items:
        a = li.find("a", href=re.compile(r"/vp/products/\d+"))
        if not a:
            continue
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.coupang.com" + href
        m = re.search(r"/vp/products/(\d+)(?:.*?vendorItemId=(\d+))?", href)
        productId = m.group(1) if m else None
        vendorItemId = m.group(2) if (m and m.group(2)) else None

        # 상품명 (img.alt 또는 a 안의 text)
        name = ""
        img = li.find("img", alt=True)
        if img and img.get("alt"):
            name = img["alt"].strip()

        # 가격 영역 — del(정가) 와 비-del 가격 span 분리
        original = None
        del_el = li.find("del")
        if del_el:
            m2 = re.search(r"([\d,]+)\s*원", del_el.get_text())
            if m2:
                original = int(m2.group(1).replace(",", ""))
        # 현재가: del 이 아닌 가격 패턴 중 가장 먼저 등장
        price = None
        for txt_node in li.find_all(string=re.compile(r"\d{1,3}(?:,\d{3})+\s*원")):
            if txt_node.parent and txt_node.parent.name == "del":
                continue
            m2 = re.search(r"([\d,]+)\s*원", str(txt_node))
            if m2:
                price = int(m2.group(1).replace(",", ""))
                break

        # 할인율 (있을 때만)
        discount_pct = None
        if price and original and original > price:
            discount_pct = round((original - price) / original * 100)

        # 적립 (예: '최대 3,724원 적립')
        reward = None
        for txt_node in li.find_all(string=re.compile(r"최대\s*[\d,]+\s*원\s*적립")):
            m2 = re.search(r"최대\s*([\d,]+)\s*원\s*적립", str(txt_node))
            if m2:
                reward = int(m2.group(1).replace(",", ""))
                break

        # 로켓 배지
        is_rocket = bool(li.find("img", alt=re.compile(r"로켓"))
                         or li.select_one('[class*="Rocket"], [class*="rocket"]'))

        # 별점/리뷰 (있으면)
        rating = None
        review_count = None
        for txt in li.find_all(string=re.compile(r"\(\s*\d")):
            m2 = re.search(r"\(\s*([\d,]+)\s*\)", str(txt))
            if m2:
                review_count = int(m2.group(1).replace(",", ""))
                break

        out.append({
            "productId": productId,
            "vendorItemId": vendorItemId,
            "name": name,
            "current_price": price,
            "original_price": original,
            "discount_pct": discount_pct,
            "reward_won": reward,
            "is_rocket": is_rocket,
            "review_count": review_count,
            "rating": rating,
            "url": href,
        })
        if len(out) >= limit:
            break
    return out


# ────────────── CLI ──────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--search", help="검색어")
    g.add_argument("--category", help="카테고리 ID")
    g.add_argument("--product", help="상품 productId")
    ap.add_argument("--vendor", help="vendorItemId (product 옵션)")
    ap.add_argument("--page-num", type=int, default=1)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    global VERBOSE
    VERBOSE = args.verbose

    driver = _launch_driver(headless=args.headless)
    try:
        if args.search:
            items = search(args.search, driver, limit=args.limit)
            print(json.dumps(items, ensure_ascii=False, indent=2))
            print(f"\n[OK] {len(items)}개", file=sys.stderr)
        elif args.category:
            items = category(args.category, driver, page_num=args.page_num, limit=args.limit)
            print(json.dumps(items, ensure_ascii=False, indent=2))
            print(f"\n[OK] {len(items)}개", file=sys.stderr)
        else:
            d = product_detail(args.product, driver, args.vendor)
            print(json.dumps(d, ensure_ascii=False, indent=2))
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
