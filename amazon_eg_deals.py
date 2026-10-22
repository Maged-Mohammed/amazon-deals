#!/usr/bin/env python3
"""
صياد اللقطات من أمازون مصر
بيدوّر على المنتجات اللي عليها خصم كبير ويبعتلك إشعار على تليجرام.
"""
import html
import json
import os
import random
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

# ================== الإعدادات (عدّل براحتك) ==================
# كلمات بحث أو روابط بحث/أقسام كاملة من amazon.eg
SEARCHES = [
    "headphones",
    "smart watch",
    "air fryer",
    "power bank",
    "perfume",
    "sneakers",
    # "https://www.amazon.eg/s?k=laptop",   # ممكن تحط لينك كامل كمان
]
MIN_DISCOUNT = 40        # أقل نسبة خصم عشان يعتبرها لقطة (%)
MIN_PRICE = 150          # تجاهل الحاجات الأرخص من كده (جنيه)
MIN_RATING = 3.8         # أقل تقييم (0 = من غير شرط)
PAGES_PER_SEARCH = 2     # عدد الصفحات لكل بحث
MAX_ALERTS_PER_RUN = 15  # أقصى عدد إشعارات في المرة الواحدة
RENOTIFY_DROP = 0.05     # يبعت تاني لو السعر نزل 5% عن آخر إشعار
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = Path(__file__).with_name("deals_state.json")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
]
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")


def parse_number(text):
    if not text:
        return None
    text = text.translate(AR_DIGITS)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


def build_url(search, page):
    if search.startswith("http"):
        base = search
    else:
        base = f"https://www.amazon.eg/s?k={quote_plus(search)}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}language=en_AE&page={page}"


def fetch(session, url):
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    for attempt in range(3):
        try:
            r = session.get(url, headers=headers, timeout=25)
            if r.status_code == 200 and "validateCaptcha" not in r.text:
                return r.text
            print(f"  ⚠️ اتحجبنا أو كابتشا (محاولة {attempt + 1})")
        except requests.RequestException as e:
            print(f"  ⚠️ خطأ اتصال: {e}")
        time.sleep(random.uniform(8, 15))
    return None


def parse_results(page_html):
    soup = BeautifulSoup(page_html, "html.parser")
    items = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        asin = card.get("data-asin")
        title_el = card.select_one("h2 span") or card.select_one("h2")
        price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
        old_el = card.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (asin and title_el and price_el and old_el):
            continue
        price, old = parse_number(price_el.get_text()), parse_number(old_el.get_text())
        if not price or not old or old <= price:
            continue
        rating_el = card.select_one("span.a-icon-alt")
        rating = parse_number(rating_el.get_text()) if rating_el else None
        items.append({
            "asin": asin,
            "title": title_el.get_text(strip=True),
            "price": price,
            "old": old,
            "discount": round((old - price) / old * 100),
            "rating": rating if rating and rating <= 5 else None,
            "url": f"https://www.amazon.eg/dp/{asin}",
        })
    return items


def is_deal(item):
    if item["discount"] < MIN_DISCOUNT or item["price"] < MIN_PRICE:
        return False
    if MIN_RATING and (item["rating"] is None or item["rating"] < MIN_RATING):
        return False
    return True


def send_telegram(text):
    if not (TOKEN and CHAT_ID):
        print(text, "\n" + "-" * 40)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"فشل إرسال تليجرام: {e}")


def format_msg(item, lowest_ever):
    lines = [
        f"🔥 <b>لقطة - خصم {item['discount']}%</b>",
        html.escape(item["title"][:200]),
        f"💰 {item['price']:,.0f} ج.م بدل {item['old']:,.0f} ج.م",
    ]
    if item["rating"]:
        lines.append(f"⭐ {item['rating']}")
    if lowest_ever:
        lines.append("📉 أقل سعر شفناه للمنتج ده")
    lines.append(item["url"])
    return "\n".join(lines)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    session = requests.Session()
    found = {}

    for search in SEARCHES:
        for page in range(1, PAGES_PER_SEARCH + 1):
            print(f"🔎 {search} - صفحة {page}")
            page_html = fetch(session, build_url(search, page))
            if not page_html:
                break
            for item in parse_results(page_html):
                found[item["asin"]] = item
            time.sleep(random.uniform(3, 7))

    deals = []
    for asin, item in found.items():
        rec = state.setdefault(asin, {"lowest": item["price"]})
        lowest_ever = item["price"] <= rec["lowest"]
        rec["lowest"] = min(rec["lowest"], item["price"])
        if not is_deal(item):
            continue
        last = rec.get("notified_price")
        if last is None or item["price"] <= last * (1 - RENOTIFY_DROP):
            deals.append((item, lowest_ever))

    deals.sort(key=lambda d: d[0]["discount"], reverse=True)
    for item, lowest_ever in deals[:MAX_ALERTS_PER_RUN]:
        send_telegram(format_msg(item, lowest_ever))
        state[item["asin"]]["notified_price"] = item["price"]
        time.sleep(1)

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"✅ اتفحص {len(found)} منتج، واتبعت {min(len(deals), MAX_ALERTS_PER_RUN)} لقطة")


if __name__ == "__main__":
    main()
