#!/usr/bin/env python3
"""
صياد اللقطات من أمازون مصر
بيدوّر على المنتجات اللي عليها خصم كبير ويبعتلك إشعار على تليجرام.
وبيتأكد من تاريخ السعر على كانبكام عشان يستبعد الخصومات الوهمية.
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

try:  # بيقلّد بصمة متصفح Chrome الحقيقي فأمازون مبيحجبوش بسهولة
    from curl_cffi import requests as browser_requests
except ImportError:
    browser_requests = None

# ================== الإعدادات (عدّل براحتك) ==================
# كلمات بحث أو روابط بحث/أقسام كاملة من amazon.eg (بتغطي كل الأقسام)
SEARCHES = [
    # موبايلات وكمبيوتر
    "smartphone", "tablet", "laptop", "monitor", "smart tv",
    # صوتيات وساعات وشواحن
    "headphones", "earbuds", "bluetooth speaker", "smart watch",
    "power bank", "fast charger",
    # جيمنج وكاميرات وطابعات وشبكات
    "gaming console", "gaming headset", "camera", "printer", "wifi router",
    # إكسسوارات كمبيوتر وتخزين
    "keyboard mouse", "webcam", "ssd", "external hard drive", "memory card",
    # مطبخ وأجهزة منزلية
    "air fryer", "blender", "microwave", "electric kettle", "vacuum cleaner",
    "steam iron",
    # دفايات ومراوح
    "heater", "fan",
    # عناية شخصية
    "hair dryer", "shaver", "hair straightener",
    # عطور وعناية بالبشرة وميكب
    "perfume men", "perfume women", "skincare", "makeup",
    # ملابس رجالي وحريمي
    "men t-shirt", "men jeans", "women dress", "women blouse",
    # شوزات وشنط وساعات ونظارات
    "sneakers", "women shoes", "backpack", "handbag", "men watch",
    "sunglasses",
    # ألعاب وأطفال
    "toys", "lego", "baby diapers", "baby stroller",
    # رياضة
    "dumbbells", "treadmill",
    # عدد وعربيات
    "power tools", "car accessories",
    # مكتب ومفروشات
    "office chair", "bedding set",
    # مكملات وحيوانات أليفة وقهوة
    "whey protein", "vitamins", "cat food", "coffee",
    # "https://www.amazon.eg/s?k=laptop",   # ممكن تحط لينك كامل كمان
]
SEARCHES_PER_RUN = 15    # عدد كلمات البحث في كل تشغيلة (بالدور على القايمة)
MIN_DISCOUNT = 50        # أقل نسبة خصم عشان يعتبرها لقطة (%)
MIN_PRICE = 150          # تجاهل الحاجات الأرخص من كده (جنيه)
MIN_RATING = 3.8         # أقل تقييم (0 = من غير شرط)
PAGES_PER_SEARCH = 2     # عدد الصفحات لكل بحث
MAX_ALERTS_PER_RUN = 15  # أقصى عدد إشعارات في المرة الواحدة
RENOTIFY_DROP = 0.05     # يبعت تاني لو السعر نزل 5% عن آخر إشعار
MAX_ABOVE_LOWEST = 0.10  # وهمي لو السعر أعلى من أقل سعر تاريخي بأكتر من 10%
NEAR_LOWEST = 0.03       # يعتبره "أقل سعر" لو قريب منه في حدود 3%
SKIP_IF_KANBKAM_FAILS = False  # لو كانبكام وقع: False = ابعت مع تحذير، True = متبعتش
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = Path(__file__).with_name("deals_state.json")
MAX_KANBKAM_LOOKUPS = MAX_ALERTS_PER_RUN * 2

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


def pick_searches(state):
    """بياخد الدفعة الجاية من كلمات البحث بالدور، وبيحفظ مكانه في state["_meta"]["next"]"""
    meta = state.setdefault("_meta", {})
    start = meta.get("next", 0) % len(SEARCHES)
    count = min(SEARCHES_PER_RUN, len(SEARCHES))
    batch = [SEARCHES[(start + i) % len(SEARCHES)] for i in range(count)]
    meta["next"] = (start + count) % len(SEARCHES)
    return batch


def make_session():
    if browser_requests:
        print("🌐 وضع المتصفح الحقيقي شغال")
        return browser_requests.Session(impersonate="chrome")
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(USER_AGENTS)
    return s


def fetch(session, url):
    headers = {
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": "https://www.amazon.eg/",
    }
    for attempt in range(3):
        try:
            r = session.get(url, headers=headers, timeout=30)
            if r.status_code == 200 and "validateCaptcha" not in r.text:
                return r.text
            print(f"  ⚠️ اتحجبنا أو كابتشا (محاولة {attempt + 1})")
        except Exception as e:
            print(f"  ⚠️ خطأ اتصال: {e}")
        time.sleep(random.uniform(15, 30))
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


def kanbkam_url(item):
    # الـ slug مش بيفرق مع كانبكام، المهم الـ ASIN في الآخر
    slug = "-".join(re.findall(r"[a-z0-9]+", item["title"].lower())[:12])
    return f"https://www.kanbkam.com/eg/en/{slug + '-' if slug else ''}{item['asin']}"


def parse_kanbkam(page_html, asin):
    """بيرجّع أقل وأعلى سعر من صفحة كانبكام، أو None لو الصفحة مش لنفس المنتج"""
    soup = BeautifulSoup(page_html, "html.parser")
    canonical = soup.select_one('link[rel="canonical"]')
    # لو المنتج مش موجود عندهم بيرجّع الصفحة الرئيسية (canonical = /home)
    if not canonical or not canonical.get("href", "").upper().endswith(asin.upper()):
        return None
    low_el, high_el = soup.select_one("span#min"), soup.select_one("span#max")
    low = parse_number(low_el.get_text()) if low_el else None
    high = parse_number(high_el.get_text()) if high_el else None
    if not low or not high:
        return None
    return {"lowest": low, "highest": high}


def check_kanbkam(session, item):
    url = kanbkam_url(item)
    for attempt in range(2):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return parse_kanbkam(r.text, item["asin"])
            if r.status_code == 404:  # المنتج مش موجود عندهم
                return None
            print(f"  ⚠️ كانبكام رجّع {r.status_code} (محاولة {attempt + 1})")
        except Exception as e:
            print(f"  ⚠️ خطأ اتصال بكانبكام: {e}")
        time.sleep(random.uniform(2, 4))
    return None


def send_telegram(text):
    if not (TOKEN and CHAT_ID):
        print(text, "\n" + "-" * 40)
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
        if r.ok and r.json().get("ok"):
            return True
        try:
            reason = r.json().get("description", r.text)
        except ValueError:
            reason = r.text
        print(f"فشل إرسال تليجرام ({r.status_code}): {reason}")
    except requests.RequestException as e:
        print(f"فشل إرسال تليجرام: {e}")
    return False


def format_msg(item, kb, lowest_ever):
    lines = [
        f"🔥 <b>لقطة - خصم {item['discount']}%</b>",
        html.escape(item["title"][:200]),
        f"💰 {item['price']:,.0f} ج.م بدل {item['old']:,.0f} ج.م",
    ]
    if item["rating"]:
        lines.append(f"⭐ {item['rating']}")
    if kb:
        lines.append(f"📊 كانبكام: أقل سعر {kb['lowest']:,.0f} ج.م | أعلى سعر {kb['highest']:,.0f} ج.م")
        real = max(0, round((kb["highest"] - item["price"]) / kb["highest"] * 100))
        lines.append(f"🎯 الخصم الحقيقي: {real}% من أعلى سعر")
        if item["price"] <= kb["lowest"] * (1 + NEAR_LOWEST):
            lines.append("✅ أقل سعر في تاريخ المنتج أو قريب منه")
    else:
        lines.append("⚠️ مفيش بيانات من كانبكام، اتأكد من السعر بنفسك")
    if lowest_ever:
        lines.append("📉 أقل سعر شفناه للمنتج ده")
    lines.append(f"🛒 {item['url']}")
    lines.append(f"📈 {kanbkam_url(item)}")
    return "\n".join(lines)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    session = make_session()
    found = {}

    # زيارة الصفحة الرئيسية الأول زي أي حد طبيعي، عشان ياخد الكوكيز
    try:
        session.get("https://www.amazon.eg/", timeout=30)
    except Exception:
        pass
    time.sleep(random.uniform(2, 5))

    searches = pick_searches(state)
    print(f"📋 كلمات التشغيلة دي: {', '.join(searches)}")
    for search in searches:
        for page in range(1, PAGES_PER_SEARCH + 1):
            print(f"🔎 {search} - صفحة {page}")
            page_html = fetch(session, build_url(search, page))
            if not page_html:
                break
            for item in parse_results(page_html):
                found[item["asin"]] = item
            time.sleep(random.uniform(3, 7))

    candidates = []
    for asin, item in found.items():
        seen_before = asin in state
        rec = state.setdefault(asin, {"lowest": item["price"]})
        prev_lowest = rec["lowest"]
        rec["lowest"] = min(rec["lowest"], item["price"])
        if not is_deal(item):
            continue
        last = rec.get("notified_price")
        if last is None or item["price"] <= last * (1 - RENOTIFY_DROP):
            lowest_ever = seen_before and item["price"] <= prev_lowest
            candidates.append((item, prev_lowest, lowest_ever))

    candidates.sort(key=lambda c: c[0]["discount"], reverse=True)
    fakes = sent = lookups = 0
    for item, prev_lowest, lowest_ever in candidates:
        if sent >= MAX_ALERTS_PER_RUN:
            break
        rec = state[item["asin"]]
        # البوت نفسه شاف المنتج بسعر أقل بكتير قبل كده
        if item["price"] > prev_lowest * (1 + MAX_ABOVE_LOWEST):
            print(f"  🚫 وهمي (البوت شافه بـ {prev_lowest:,.0f}): {item['asin']} بـ {item['price']:,.0f}")
            fakes += 1
            continue
        if rec.get("fake_price") == item["price"]:
            print(f"  🚫 وهمي (متفحوص قبل كده بنفس السعر): {item['asin']}")
            fakes += 1
            continue
        if lookups >= MAX_KANBKAM_LOOKUPS:
            print("  ⏸️ خلصنا حد كانبكام للتشغيلة دي، الباقي المرة الجاية")
            break
        if lookups:
            time.sleep(random.uniform(2, 4))
        lookups += 1
        kb = check_kanbkam(session, item)
        if kb and item["price"] > kb["lowest"] * (1 + MAX_ABOVE_LOWEST):
            rec["fake_price"] = item["price"]
            print(f"  🚫 وهمي (أقل سعر على كانبكام {kb['lowest']:,.0f}): {item['asin']} بـ {item['price']:,.0f}")
            fakes += 1
            continue
        if not kb:
            print(f"  ⚠️ مفيش بيانات من كانبكام: {item['asin']}")
            if SKIP_IF_KANBKAM_FAILS:
                continue
        if send_telegram(format_msg(item, kb, lowest_ever)):
            rec["notified_price"] = item["price"]
            sent += 1
        time.sleep(1)

    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print(f"✅ اتفحص {len(found)} منتج | مرشحين {len(candidates)} | وهمي اترفض {fakes} | اتبعت {sent}")


if __name__ == "__main__":
    main()
