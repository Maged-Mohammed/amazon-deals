#!/usr/bin/env python3
"""
صياد اللقطات من أمازون مصر
بيدوّر على المنتجات اللي عليها خصم كبير ويبعتلك إشعار على تليجرام.
وبيتأكد من تاريخ السعر (كانبكام + تاريخ البوت نفسه) عشان يستبعد الخصومات الوهمية،
وبيدي كل لقطة سكور من 0 لـ 100.
"""
import html
import json
import math
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

try:  # بيقلّد بصمة متصفح Chrome الحقيقي فأمازون مبيحجبوش بسهولة
    from curl_cffi import requests as browser_requests
except ImportError:
    browser_requests = None

# ================== الإعدادات (عدّل براحتك) ==================
# كلمات بحث أو روابط بحث/أقسام كاملة من amazon.eg (بتغطي كل الأقسام)، متقسمة لأقسام بتظهر في الداشبورد
SEARCH_GROUPS = {
    "موبايلات وكمبيوتر": ["smartphone", "tablet", "laptop", "monitor", "smart tv"],
    "صوتيات وساعات وشواحن": ["headphones", "earbuds", "bluetooth speaker", "smart watch",
                             "power bank", "fast charger"],
    "جيمنج وكاميرات وشبكات": ["gaming console", "gaming headset", "camera", "printer", "wifi router"],
    "إكسسوارات كمبيوتر وتخزين": ["keyboard mouse", "webcam", "ssd", "external hard drive", "memory card"],
    "مطبخ وأجهزة منزلية": ["air fryer", "blender", "microwave", "electric kettle", "vacuum cleaner",
                           "steam iron"],
    "دفايات ومراوح": ["heater", "fan"],
    "عناية شخصية": ["hair dryer", "shaver", "hair straightener"],
    "عطور وتجميل": ["perfume men", "perfume women", "skincare", "makeup"],
    "ملابس": ["men t-shirt", "men jeans", "women dress", "women blouse"],
    "شوزات وشنط وإكسسوارات": ["sneakers", "women shoes", "backpack", "handbag", "men watch", "sunglasses"],
    "ألعاب وأطفال": ["toys", "lego", "baby diapers", "baby stroller"],
    "رياضة": ["dumbbells", "treadmill"],
    "عدد وعربيات": ["power tools", "car accessories"],
    "مكتب ومفروشات": ["office chair", "bedding set"],
    "مكملات وحيوانات أليفة وقهوة": ["whey protein", "vitamins", "cat food", "coffee"],
    # "أخرى": ["https://www.amazon.eg/s?k=laptop"],   # ممكن تحط لينك كامل كمان
}
SEARCHES = [s for group in SEARCH_GROUPS.values() for s in group]
CATEGORY_OF = {s: name for name, group in SEARCH_GROUPS.items() for s in group}
SEARCHES_PER_RUN = 15    # عدد كلمات البحث في كل تشغيلة (بالدور على القايمة)
MIN_DISCOUNT = 50        # أقل نسبة خصم عشان يعتبرها لقطة (%) - بعد الكوبون
MIN_PRICE = 150          # تجاهل الحاجات الأرخص من كده (جنيه)
MIN_RATING = 3.8         # أقل تقييم (0 = من غير شرط)
PAGES_PER_SEARCH = 2     # عدد الصفحات لكل بحث
MAX_ALERTS_PER_RUN = 15  # أقصى عدد إشعارات في المرة الواحدة
RENOTIFY_DROP = 0.05     # يبعت تاني لو السعر نزل 5% عن آخر إشعار
MAX_ABOVE_LOWEST = 0.10  # وهمي لو السعر أعلى من أقل سعر تاريخي بأكتر من 10%
NEAR_LOWEST = 0.03       # يعتبره "أقل سعر" لو قريب منه في حدود 3%
SKIP_IF_KANBKAM_FAILS = False  # لو كانبكام وقع: False = ابعت مع تحذير، True = متبعتش
MIN_SCORE = 40           # أقل سكور (0-100) عشان اللقطة تتبعتلك
CHANNEL_MIN_SCORE = 60   # أقل سكور عشان اللقطة تتنشر في القناة (بعد فترة التسخين)
CHANNEL_MIN_SCORE_EARLY = 50  # الحد في الأول، لما تاريخ الأسعار لسه قليل والسكورات واطية
HISTORY_WARMUP_DAYS = 14      # بعد كام يوم من أول نقطة في data/history.json نرجع لـ CHANNEL_MIN_SCORE
GLITCH_DISCOUNT = 80     # 🚨 غلطة سعر: خصم فعلي 80% أو أكتر وعند أقل سعر أو تحته
UNVERIFIED_WEIGHT = 0.6  # وزن خصم أمازون المعلن في السكور لو مفيش تاريخ يأكده
HISTORY_CAP = 90         # أقصى عدد نقط سعر محفوظة لكل منتج في data/history.json
QUIET_HOURS = (0, 8)     # ساعات الهدوء بتوقيت القاهرة: من 12 بالليل لحد 8 الصبح
DIGEST_SIZE = 10         # عدد اللقطات في ملخص الصبح
WEEKLY_REPORT_HOUR = 10  # التقرير الأسبوعي يوم الجمعة من الساعة دي
DASHBOARD_DAYS = 7       # اللقطة بتفضل في الداشبورد كام يوم (أو لحد ما سعرها يغلى تاني)
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")          # الشات الخاص: كل حاجة + رسايل الإدارة
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")    # القناة: اللقطات المختارة بس
AMAZON_TAG = os.getenv("AMAZON_TAG", "")             # تاج الأفلييت (مقفول لو فاضي)
SELFTEST = os.getenv("SELFTEST", "").lower() == "true"
ANNOUNCE = os.getenv("ANNOUNCE", "")  # ملف رسالة (HTML) يتنشر في القناة ويتثبت، من غير بحث
STATE_FILE = Path(__file__).with_name("deals_state.json")
HISTORY_FILE = Path(__file__).with_name("data") / "history.json"
DOCS_DIR = Path(__file__).with_name("docs")  # الداشبورد على GitHub Pages
CHANNEL_URL = "https://t.me/loata_amazon"
MAX_LOOKUPS = MAX_ALERTS_PER_RUN * 2
CAIRO = ZoneInfo("Africa/Cairo")
STORE_NAMES = {"noon": "نون", "jumia": "جوميا", "jumai": "جوميا"}
SELLER_NAMES = {"amazon": "أمازون نفسه", "fba": "شحن أمازون (FBA)", "third": "بايع خارجي"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
]
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
AMAZON_HEADERS = {"Accept-Language": "en-US,en;q=0.9,ar;q=0.8", "Referer": "https://www.amazon.eg/"}


def now_cairo():
    return datetime.now(CAIRO)


def parse_number(text):
    if not text:
        return None
    text = text.translate(AR_DIGITS)
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def build_url(search, page):
    if search.startswith("http"):
        base = search
    else:
        base = f"https://www.amazon.eg/s?k={quote_plus(search)}"
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}language=en_AE&page={page}"


def amazon_link(asin):
    url = f"https://www.amazon.eg/dp/{asin}"
    return f"{url}?tag={quote_plus(AMAZON_TAG)}" if AMAZON_TAG else url


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


class AmazonBlocked(Exception):
    """أمازون رجّع صفحة تحقق من البوتات (كابتشا أو bm-verify بتاعة Akamai)"""


def is_challenge(text):
    return "validateCaptcha" in text or "bm-verify" in text


def fetch(session, url):
    for attempt in range(3):
        try:
            r = session.get(url, headers=AMAZON_HEADERS, timeout=30)
            if r.status_code == 200 and is_challenge(r.text):
                # منكررش: التكرار بيطوّل التشغيلة وبيخلي الحجب أسوأ
                raise AmazonBlocked()
            if r.status_code == 200:
                return r.text
            print(f"  ⚠️ أمازون رجّع {r.status_code} (محاولة {attempt + 1})")
        except AmazonBlocked:
            raise
        except Exception as e:
            print(f"  ⚠️ خطأ اتصال: {e}")
        time.sleep(random.uniform(15, 30))
    return None


# ================== تاريخ الأسعار ==================
def record_price(history, asin, price, stamp):
    """نقطة سعر جديدة لو السعر اتغير أو أول مرة في اليوم، ومتخليش أكتر من HISTORY_CAP"""
    points = history.setdefault(asin, [])
    if not points or points[-1][1] != price or points[-1][0][:10] != stamp[:10]:
        points.append([stamp, price])
        del points[:-HISTORY_CAP]


def save_history(history):
    # سطر لكل منتج عشان الـ diff في git يفضل صغير
    HISTORY_FILE.parent.mkdir(exist_ok=True)
    rows = [f"{json.dumps(a)}:{json.dumps(p, separators=(',', ':'))}" for a, p in sorted(history.items())]
    HISTORY_FILE.write_text("{\n" + ",\n".join(rows) + "\n}\n", encoding="utf-8")


# ================== الداشبورد (docs/) ==================
def dash_record(item, ref, score, seller, glitch, stamp):
    return {
        "asin": item["asin"], "title": item["title"], "image": item["image"],
        "category": CATEGORY_OF.get(item["category"], "أخرى"),
        "price": item["eff"], "listed": item["price"], "list": item["old"],
        "coupon": bool(item["coupon_price"]), "discount": item["eff_discount"],
        "real": ref["real"], "verified": bool(ref["source"]), "atl": ref["atl"],
        "score": score, "label": score_label(score), "glitch": glitch, "seller": seller,
        "rating": item["rating"], "reviews": item["reviews"],
        "found": stamp, "seen": stamp, "url": item["url"], "kanbkam": kanbkam_url(item),
    }


def update_dashboard(approved, found, queue, history, now):
    """docs/deals.json = اللقطات الحالية، وdocs/history.json = تاريخ أسعارها بس (عشان الصفحة تفضل خفيفة)"""
    path = DOCS_DIR / "deals.json"
    deals = {d["asin"]: d for d in load_json(path, {}).get("deals", [])}
    stamp = now.isoformat(timespec="minutes")
    # لقطات الطابور اللي اتعملت قبل الداشبورد ملهاش سجل، فبنضيفها بالمعلومات اللي عندنا
    for d in queue.values():
        if d["asin"] not in deals and "found" not in d:
            deals[d["asin"]] = {"asin": d["asin"], "title": d["title"], "image": d.get("photo"),
                                "category": "أخرى", "price": d["eff"], "listed": d["eff"], "real": d["real"],
                                "verified": False, "score": d["score"], "label": d["label"], "glitch": False,
                                "found": stamp, "seen": stamp, "url": d["url"],
                                "kanbkam": f"https://www.kanbkam.com/eg/en/{d['asin']}"}
    for d in approved:
        old = deals.get(d["asin"])
        if old and old["price"] <= d["price"]:
            d["found"] = old["found"]
        deals[d["asin"]] = d
    for asin, d in list(deals.items()):
        if asin in found:
            if found[asin]["price"] > d["listed"] * (1 + RENOTIFY_DROP):
                print(f"  🗑️ من الداشبورد (السعر غلي تاني): {asin}")
                del deals[asin]
                continue
            d["seen"] = stamp
        if (now - datetime.fromisoformat(d["found"])).days >= DASHBOARD_DAYS:
            del deals[asin]
    # الموقع عام زي القناة: عنوان كلمة واحدة (زي "adidas") مش بيظهر
    listing = sorted((d for d in deals.values() if len(d["title"].split()) > 1),
                     key=lambda d: d["found"], reverse=True)
    DOCS_DIR.mkdir(exist_ok=True)
    compact = {"ensure_ascii": False, "separators": (",", ":")}
    path.write_text(json.dumps({"updated": stamp, "channel": CHANNEL_URL, "deals": listing}, **compact),
                    encoding="utf-8")
    (DOCS_DIR / "history.json").write_text(
        json.dumps({d["asin"]: history.get(d["asin"], []) for d in listing}, **compact), encoding="utf-8")
    print(f"🖥️ الداشبورد: {len(listing)} لقطة")


# ================== أمازون ==================
def parse_coupon(text, price):
    """بيحسب السعر بعد الكوبون من نص زي "You pay EGP 1,708 with coupon" أو "Save 10% with coupon" """
    if not text or not re.search(r"coupon|كوبون", text, re.I):
        return None
    text = text.translate(AR_DIGITS)
    eff = None
    if m := re.search(r"You pay\s*(?:EGP|ج\.م\.?)?\s*([\d,]+(?:\.\d+)?)", text, re.I):
        eff = parse_number(m.group(1))
    elif m := re.search(r"(\d+(?:\.\d+)?)\s*%\s*(?:off\s*)?(?:with\s*)?coupon", text, re.I):
        eff = price * (1 - float(m.group(1)) / 100)
    elif m := re.search(r"(?:EGP|ج\.م\.?)\s*([\d,]+(?:\.\d+)?)\s*(?:off\s*)?(?:with\s*)?coupon", text, re.I):
        eff = price - parse_number(m.group(1))
    return round(eff, 2) if eff and 0 < eff < price else None


def big_image(src):
    # الكارت بيدي صورة صغيرة (_AC_UL320_)، بنطلب نسخة أكبر من نفس الصورة
    return re.sub(r"\._[^/]+_\.(jpg|jpeg|png|webp)$", r"._AC_SL1000_.\1", src) if src else None


def parse_results(page_html, category=""):
    soup = BeautifulSoup(page_html, "html.parser")
    items = []
    for card in soup.select('div[data-component-type="s-search-result"]'):
        asin = card.get("data-asin")
        # كروت الملابس فيها h2 للماركة لوحدها وh2 تاني فيه اسم المنتج: بناخد الأطول،
        # ونحط الماركة قبله لو الاسم مفيهوش ولا كلمة منها
        heads = sorted((h.get_text(" ", strip=True) for h in card.select("h2")), key=len)
        title = heads[-1] if heads else ""
        brand = heads[0] if len(heads) > 1 else ""
        if brand and not set(re.findall(r"\w+", brand.lower())) & set(re.findall(r"\w+", title.lower())):
            title = f"{brand} {title}"
        price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen")
        old_el = card.select_one("span.a-price.a-text-price span.a-offscreen")
        if not (asin and title and price_el):
            continue
        price = parse_number(price_el.get_text())
        old = parse_number(old_el.get_text()) if old_el else None
        coupon_text = " ".join(el.get_text(" ", strip=True) for el in card.select('[class*="s-coupon"]'))
        coupon_price = parse_coupon(coupon_text, price) if price else None
        if not price:
            continue
        if not old or old <= price:
            if not coupon_price:  # مفيش خصم خالص
                continue
            old = price  # الخصم كله من الكوبون
        rating_el = card.select_one("span.a-icon-alt")
        rating = parse_number(rating_el.get_text()) if rating_el else None
        reviews_el = card.select_one('a[aria-label*="rating"]')
        img = card.select_one("img.s-image")
        items.append({
            "asin": asin,
            "title": title,
            "price": price,
            "old": old,
            "rating": rating if rating and rating <= 5 else None,
            "reviews": int(parse_number(reviews_el.get("aria-label")) or 0) if reviews_el else None,
            "image": big_image(img.get("src")) if img else None,
            "coupon_price": coupon_price,
            "category": category,
            "url": amazon_link(asin),
        })
    return items


def apply_effective(item):
    """السعر الفعلي = بعد الكوبون لو فيه، ونسبة الخصم الفعلية من سعر أمازون القديم"""
    item["eff"] = item["coupon_price"] or item["price"]
    item["eff_discount"] = round((item["old"] - item["eff"]) / item["old"] * 100)


def is_deal(item):
    if item["eff_discount"] < MIN_DISCOUNT or item["eff"] < MIN_PRICE:
        return False
    if MIN_RATING and (item["rating"] is None or item["rating"] < MIN_RATING):
        return False
    return True


def fetch_details(session, asin):
    """صفحة المنتج: مين البايع، متوفر ولا لأ، وكوبون لو مكانش ظاهر في نتايج البحث"""
    try:
        r = session.get(f"https://www.amazon.eg/dp/{asin}?language=en_AE", headers=AMAZON_HEADERS, timeout=30)
    except Exception as e:
        print(f"  ⚠️ صفحة المنتج وقعت: {e}")
        return None
    if r.status_code != 200 or is_challenge(r.text):
        print(f"  ⚠️ صفحة المنتج رجّعت {r.status_code} أو كابتشا")
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    text = lambda sel: (el.get_text(" ", strip=True) if (el := soup.select_one(sel)) else "")
    merchant, fulfiller = text("#merchantInfoFeature_feature_div"), text("#fulfillerInfoFeature_feature_div")
    availability = text("#availability")
    if re.search(r"Seller\s+Amazon", merchant) or re.search(r"Sold by\s+Amazon", merchant):
        seller = "amazon"
    elif "Amazon" in fulfiller:
        seller = "fba"
    else:
        seller = "third" if merchant else None
    return {
        "seller": seller,
        "in_stock": not re.search(r"unavailable|out of stock|غير متوفر", availability, re.I) if availability else None,
        "coupon_text": text("#promoPriceBlockMessage_feature_div"),
    }


# ================== كانبكام ==================
def kanbkam_url(item):
    # الـ slug مش بيفرق مع كانبكام، المهم الـ ASIN في الآخر
    slug = "-".join(re.findall(r"[a-z0-9]+", item["title"].lower())[:12])
    return f"https://www.kanbkam.com/eg/en/{slug + '-' if slug else ''}{item['asin']}"


def parse_kanbkam(page_html, asin):
    """بيرجّع أقل وأعلى سعر + أسعار نون/جوميا من صفحة كانبكام، أو None لو الصفحة مش لنفس المنتج"""
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
    others = []
    # جدول "Similar Products": كل صف فيه المتجر في data-gtmid زي "product-buy|noon|eg|en"
    for row in soup.select(".similar-product tr"):
        tag = row.select_one("a[data-gtmid]")
        store = tag["data-gtmid"].split("|")[1] if tag and "|" in tag["data-gtmid"] else ""
        name_el, price_el = row.select_one(".item-name"), row.select_one(".item-price p")
        if store in STORE_NAMES and name_el and price_el:
            others.append({"store": store, "title": name_el.get_text(" ", strip=True),
                           "price": parse_number(price_el.get_text())})
    return {"lowest": low, "highest": high, "others": others}


class Kanbkam:
    """فحص كانبكام، ولو حجبنا (403 زي من GitHub Actions) أو مبيردش، بيبطّل يحاول لحد آخر التشغيلة"""

    def __init__(self, session):
        self.session, self.blocked = session, False

    def check(self, item):
        if self.blocked:
            return None
        url = kanbkam_url(item)
        for attempt in range(2):
            try:
                r = self.session.get(url, timeout=15)
            except Exception as e:  # timeout أو مشكلة اتصال: منستناش عليه تاني
                print(f"  🚫 كانبكام مبيردش ({type(e).__name__})، هنكمل بتاريخ البوت بس")
                self.blocked = True
                return None
            if r.status_code == 200:
                return parse_kanbkam(r.text, item["asin"])
            if r.status_code == 404:  # المنتج مش موجود عندهم
                return None
            if r.status_code == 403:
                print("  🚫 كانبكام حاجبنا (403)، هنكمل بتاريخ البوت بس")
                self.blocked = True
                return None
            print(f"  ⚠️ كانبكام رجّع {r.status_code} (محاولة {attempt + 1})")
            time.sleep(random.uniform(2, 4))
        return None


def same_product(a, b):
    """مقارنة عناوين: لازم نفس الموديل (لو فيه) ونسبة كلمات مشتركة عالية"""
    ta, tb = (set(re.findall(r"[a-z0-9]{2,}", t.lower())) for t in (a, b))
    if not ta or not tb:
        return False
    models = {t for t in ta if len(t) >= 4 and re.search(r"\d", t) and re.search(r"[a-z]", t)}
    if models and not models & tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def cheaper_elsewhere(item, kb):
    best = None
    for o in (kb or {}).get("others", []):
        if o["price"] and o["price"] < item["eff"] and same_product(item["title"], o["title"]):
            if not best or o["price"] < best["price"]:
                best = o
    return best


# ================== السكور ==================
def price_reference(item, kb, history, points):
    """أقل سعر تاريخي + الخصم الحقيقي: من كانبكام، وإلا من تاريخ البوت، وإلا خصم أمازون المعلن"""
    lows = [x for x in ((kb or {}).get("lowest"), history and history["lowest"]) if x]
    high, source = None, None
    if kb:
        high, source = kb["highest"], "kanbkam"
    else:
        bot_high = max((p for _, p in points), default=None)
        if bot_high and bot_high > item["eff"]:
            high, source = bot_high, "bot"
    real = max(0, round((high - item["eff"]) / high * 100)) if high else item["eff_discount"]
    return {"atl": min(lows) if lows else None, "real": real, "source": source}


def deal_score(item, ref, seller):
    # 40: الخصم الحقيقي (لحد 70%)، ولو مش متأكد منه بياخد وزن أقل
    points = min(ref["real"], 70) / 70 * 40 * (1 if ref["source"] else UNVERIFIED_WEIGHT)
    # 25: قد إيه قريب من أقل سعر تاريخي
    if ref["atl"]:
        above = item["eff"] / ref["atl"] - 1
        points += 25 if above <= 0 else max(0, 25 * (1 - above / (2 * MAX_ABOVE_LOWEST)))
    else:
        points += 10  # منتج جديد ملوش تاريخ
    # 15: التقييم (3.5 = صفر، 5 = كامل)
    points += 15 * min(1, max(0, ((item["rating"] or 0) - 3.5) / 1.5))
    # 10: عدد التقييمات (1000 أو أكتر = كامل)
    points += 10 * min(1, math.log10((item["reviews"] or 0) + 1) / 3)
    # 10: البايع
    points += {"amazon": 10, "fba": 10, "third": 3}.get(seller, 5)
    return round(points)


def score_label(score):
    return "🔥 أسطورية" if score >= 75 else "👍 كويسة" if score >= 55 else "😐 عادية"


def is_glitch(item, ref):
    # لازم يكون فيه أقل سعر معروف (كانبكام أو تاريخ البوت)، عشان منتج أول مرة نشوفه بخصم أمازون المعلن بس ميصحيش حد بالليل
    return bool(ref["atl"]) and ref["real"] >= GLITCH_DISCOUNT and item["eff"] <= ref["atl"] * (1 + NEAR_LOWEST)


# ================== الرسايل ==================
def format_msg(item, kb, history, ref, score, seller, glitch, lowest_ever):
    lines = []
    if glitch:
        lines.append("🚨 <b>غلطة سعر! الحق بسرعة</b>")
    lines += [
        f"<b>{score_label(score)} — سكور {score}/100</b>",
        html.escape(item["title"][:200]),
        f"💰 {item['eff']:,.0f} ج.م بدل {item['old']:,.0f} ج.م (خصم {item['eff_discount']}%)",
    ]
    if item["coupon_price"]:
        lines.append(f"🎟️ بالكوبون: {item['eff']:,.0f} ج.م بدل {item['price']:,.0f} ج.م")
    if item["rating"]:
        count = f" ({item['reviews']:,} تقييم)" if item["reviews"] else ""
        lines.append(f"⭐ {item['rating']}{count}")
    if seller:
        lines.append(f"🏪 البايع: {SELLER_NAMES[seller]}")
    if kb:
        lines.append(f"📊 كانبكام: أقل سعر {kb['lowest']:,.0f} ج.م | أعلى سعر {kb['highest']:,.0f} ج.م")
    elif history:  # مفيش كانبكام، فبنعرض تاريخ البوت نفسه
        line = f"📊 من تاريخ البوت: أقل سعر {history['lowest']:,.0f} ج.م"
        if history["at"]:
            days = (now_cairo().date() - datetime.fromisoformat(history["at"]).date()).days
            line += f" — اتسجل من {days} يوم" if days else " — اتسجل النهارده"
        lines.append(line)
    else:
        lines.append("⚠️ مفيش بيانات من كانبكام، اتأكد من السعر بنفسك")
    if ref["source"]:
        where = "على كانبكام" if ref["source"] == "kanbkam" else "شافه البوت"
        lines.append(f"🎯 الخصم الحقيقي: {ref['real']}% من أعلى سعر {where}")
    if ref["atl"] and item["eff"] <= ref["atl"] * (1 + NEAR_LOWEST):
        lines.append("✅ أقل سعر في تاريخ المنتج أو قريب منه")
    if other := cheaper_elsewhere(item, kb):
        lines.append(f"⚡ أرخص في {STORE_NAMES[other['store']]} بـ {item['eff'] - other['price']:,.0f} جنيه")
    if lowest_ever:
        lines.append("📉 أقل سعر شفناه للمنتج ده")
    if AMAZON_TAG:
        lines.append("ℹ️ لينك أفلييت: بناخد عمولة صغيرة من غير ما السعر يزيد عليك")
    return "\n".join(lines)


def tg_call(method, payload):
    try:
        r = requests.post(f"https://api.telegram.org/bot{TOKEN}/{method}", json=payload, timeout=30)
        data = r.json()
        if data.get("ok"):
            return data.get("result") or True
        print(f"فشل تليجرام {method} ({r.status_code}): {data.get('description')}")
    except (requests.RequestException, ValueError) as e:
        print(f"فشل تليجرام {method}: {e}")
    return None


def send_text(chat_id, text, loud=False):
    if not (TOKEN and chat_id):
        print(text, "\n" + "-" * 40)
        return False
    return bool(tg_call("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                                        "disable_notification": not loud, "disable_web_page_preview": True}))


def send_deal(chat_id, deal, loud=False):
    """صورة + كابشن + زراير، ولو الصورة فشلت يبعتها رسالة عادية"""
    if not (TOKEN and chat_id):
        return False
    common = {"chat_id": chat_id, "parse_mode": "HTML", "disable_notification": not loud,
              "reply_markup": {"inline_keyboard": [deal["buttons"]]}}
    if deal["photo"] and tg_call("sendPhoto", {**common, "photo": deal["photo"], "caption": deal["caption"]}):
        return True
    return bool(tg_call("sendMessage", {**common, "text": deal["caption"], "disable_web_page_preview": True}))


def channel_threshold(history, now):
    """حد القناة: CHANNEL_MIN_SCORE_EARLY لحد ما يبقى عندنا HISTORY_WARMUP_DAYS يوم تاريخ، وبعدها CHANNEL_MIN_SCORE"""
    first = min((p[0][0] for p in history.values() if p), default=None)
    days = (now - datetime.fromisoformat(first)).days if first else 0
    return (CHANNEL_MIN_SCORE if days >= HISTORY_WARMUP_DAYS else CHANNEL_MIN_SCORE_EARLY), days


def for_channel(deal, channel_min):
    """القناة: السكور فوق الحد، والعنوان أكتر من كلمة (عنوان زي "adidas" لوحده ميفيدش حد)"""
    return deal["score"] >= channel_min and len(deal["title"].split()) > 1


def deliver(deal, channel_min, loud=False):
    """الخاص بياخد كل حاجة، والقناة بتاخد اللي سكوره عالي بس"""
    if not TOKEN:
        print(deal["caption"], "\n" + "-" * 40)
        return False
    ok = send_deal(CHAT_ID, deal, loud)
    if CHANNEL_ID and for_channel(deal, channel_min):
        ok = send_deal(CHANNEL_ID, deal, loud) or ok
    return ok


def digest_text(deals):
    lines = ["🌅 <b>صباح الخير — لقطات الليلة</b>", ""]
    for i, d in enumerate(deals, 1):
        lines.append(f"{i}. {d['label'].split()[0]} <a href=\"{d['url']}\">{html.escape(d['title'][:80])}</a>")
        lines.append(f"    💰 {d['eff']:,.0f} ج.م (خصم {d['real']}%) — سكور {d['score']}")
    return "\n".join(lines)


def send_digest(state, channel_min):
    """أول تشغيلة بعد ساعات الهدوء: ملخص واحد باللقطات اللي اتجمعت بالليل"""
    queue = state["_meta"].get("queue", {})
    if not queue:
        return
    top = sorted(queue.values(), key=lambda d: d["score"], reverse=True)[:DIGEST_SIZE]
    ok = send_text(CHAT_ID, digest_text(top), loud=True) if TOKEN else False
    channel_top = [d for d in top if for_channel(d, channel_min)]
    if TOKEN and CHANNEL_ID and channel_top:
        ok = send_text(CHANNEL_ID, digest_text(channel_top), loud=True) or ok
    if not TOKEN:
        print(digest_text(top))
    if not ok:
        print("⚠️ ملخص الصبح متبعتش، هنحاول التشغيلة الجاية")
        return
    shown = {d["asin"] for d in top}
    for asin, d in queue.items():
        rec = state.get(asin, {})
        if asin in shown:
            rec["notified_price"] = d["eff"]
        rec.pop("queued_price", None)
    state["_meta"]["queue"] = {}
    print(f"🌅 اتبعت ملخص الصبح بـ {len(top)} لقطة")


def weekly_report(state, now):
    """يوم الجمعة: تقرير بالأسبوع اللي فات (بعد ما يكمل أسبوع)"""
    meta = state["_meta"]
    week = meta.setdefault("week", {"start": now.date().isoformat(), "found": 0, "fakes": 0, "top": []})
    today = now.date().isoformat()
    if now.weekday() != 4 or now.hour < WEEKLY_REPORT_HOUR or meta.get("weekly_sent") == today:
        return
    if (now.date() - datetime.fromisoformat(week["start"]).date()).days < 6:
        return
    lines = [f"📊 <b>التقرير الأسبوعي</b> ({week['start']} ← {today})", "",
             f"🛍️ لقطات اتلقت: {week['found']}", f"🚫 خصومات وهمية اتكشفت: {week['fakes']}"]
    if week["top"]:
        lines += ["", "🏆 <b>أحسن 5 لقطات:</b>"]
        for i, d in enumerate(week["top"], 1):
            lines.append(f"{i}. <a href=\"{d['url']}\">{html.escape(d['title'][:70])}</a> — {d['eff']:,.0f} ج.م (سكور {d['score']})")
    text = "\n".join(lines)
    ok = send_text(CHAT_ID, text)
    if CHANNEL_ID and week["top"]:
        ok = send_text(CHANNEL_ID, text) or ok
    if ok:
        meta["weekly_sent"] = today
        meta["week"] = {"start": today, "found": 0, "fakes": 0, "top": []}
        print("📊 اتبعت التقرير الأسبوعي")


def track_week(state, deal=None, fake=False):
    week = state["_meta"].setdefault("week", {"start": now_cairo().date().isoformat(), "found": 0, "fakes": 0, "top": []})
    if fake:
        week["fakes"] += 1
    if deal:
        week["found"] += 1
        top = [d for d in week["top"] if d["asin"] != deal["asin"]]
        top.append({k: deal[k] for k in ("asin", "title", "eff", "score", "url")})
        week["top"] = sorted(top, key=lambda d: d["score"], reverse=True)[:5]


def selftest():
    """تجربة من الـ workflow: البوت شايف القناة؟ والصورة والزراير بيوصلوا للخاص؟ (من غير ما يلمس الحالة)"""
    me = tg_call("getMe", {})
    print(f"SELFTEST getMe: {me and me.get('username')}")
    if CHANNEL_ID and me:
        chat = tg_call("getChat", {"chat_id": CHANNEL_ID})
        member = tg_call("getChatMember", {"chat_id": CHANNEL_ID, "user_id": me["id"]})
        print(f"SELFTEST channel: title={chat and chat.get('title')} status={member and member.get('status')} "
              f"can_post={member and member.get('can_post_messages')}")
    else:
        print("SELFTEST channel: TELEGRAM_CHANNEL_ID مش متظبط")
    item = {"asin": "B0GD7K4M6Z", "title": "Fresh Air Fryer 5Litres FAF-D1500B, 1500W, With Digital Screen, Black",
            "price": 3079.0, "old": 6158.0, "rating": 4.7, "reviews": 16, "coupon_price": None,
            "image": "https://m.media-amazon.com/images/I/61wDkC2zDHL._AC_SL1000_.jpg", "url": amazon_link("B0GD7K4M6Z")}
    apply_effective(item)
    ref = {"atl": 3079.0, "real": 50, "source": "bot"}
    caption = "🧪 <b>رسالة تجربة (مش لقطة حقيقية)</b>\n" + format_msg(
        item, None, {"lowest": 3150.0, "at": now_cairo().isoformat(timespec="minutes")}, ref,
        deal_score(item, ref, "fba"), "fba", False, True)
    deal = {"caption": caption, "photo": item["image"], "score": 0,
            "buttons": [{"text": "📈 كان بكام", "url": kanbkam_url(item)}, {"text": "🛒 اشتري", "url": item["url"]}]}
    print(f"SELFTEST private sendPhoto+buttons: {send_deal(CHAT_ID, deal)}")


def announce(path):
    """ينشر رسالة من ملف في القناة ويثبتها من غير إشعار (زي رسالة الترحيب)"""
    file = Path(__file__).parent / path
    if not (TOKEN and CHANNEL_ID and file.is_file()):
        print(f"ANNOUNCE: مش هينفع (توكن/قناة/ملف ناقص): {path}")
        return
    msg = tg_call("sendMessage", {"chat_id": CHANNEL_ID, "text": file.read_text(encoding="utf-8").strip(),
                                  "parse_mode": "HTML", "disable_notification": True,
                                  "disable_web_page_preview": True})
    print(f"ANNOUNCE sent: message_id={msg and msg.get('message_id')}")
    if msg:
        pinned = tg_call("pinChatMessage", {"chat_id": CHANNEL_ID, "message_id": msg["message_id"],
                                            "disable_notification": True})
        print(f"ANNOUNCE pinned: {bool(pinned)}")


def main():
    if SELFTEST:
        return selftest()
    if ANNOUNCE:
        return announce(ANNOUNCE)
    state = load_json(STATE_FILE, {})
    history = load_json(HISTORY_FILE, {})
    meta = state.setdefault("_meta", {})
    now = now_cairo()
    quiet = QUIET_HOURS[0] <= now.hour < QUIET_HOURS[1]
    stamp, today = now.isoformat(timespec="minutes"), now.date().isoformat()
    print(f"🕐 {stamp} بتوقيت القاهرة{' (ساعات الهدوء)' if quiet else ''}")
    channel_min, history_days = channel_threshold(history, now)
    phase = "فترة التسخين" if channel_min == CHANNEL_MIN_SCORE_EARLY else "الحد العادي"
    print(f"📢 حد القناة: سكور {channel_min}+ ({phase}، عندنا {history_days} يوم تاريخ من {HISTORY_WARMUP_DAYS})")
    if not quiet:
        send_digest(state, channel_min)
        weekly_report(state, now)

    session = make_session()
    found = {}
    # زيارة الصفحة الرئيسية الأول زي أي حد طبيعي، عشان ياخد الكوكيز
    try:
        session.get("https://www.amazon.eg/", timeout=30)
    except Exception:
        pass
    time.sleep(random.uniform(2, 5))

    rotation_start = meta.get("next", 0)
    searches = pick_searches(state)
    print(f"📋 كلمات التشغيلة دي: {', '.join(searches)}")
    blocked = False
    try:
        for search in searches:
            for page in range(1, PAGES_PER_SEARCH + 1):
                print(f"🔎 {search} - صفحة {page}")
                page_html = fetch(session, build_url(search, page))
                if not page_html:
                    break
                for item in parse_results(page_html, search):
                    found.setdefault(item["asin"], item)
                time.sleep(random.uniform(3, 7))
    except AmazonBlocked:
        blocked = True
        print("🛑 أمازون حاجبنا (صفحة تحقق من البوتات)، هنوقف البحث ونحاول التشغيلة الجاية")
    if not found:
        meta["next"] = rotation_start  # الكلمات دي متتفوتش، هنبدأ بيها المرة الجاية
        reason = "أمازون رجّع صفحة تحقق من البوتات" if blocked else "ملقتش ولا منتج"
        print(f"⚠️ التشغيلة دي فاضية ({reason})، الدور هيفضل مكانه")
        if meta.get("blocked_notice") != today:  # رسالة واحدة في اليوم بالكتير
            if send_text(CHAT_ID, f"⚠️ <b>تنبيه إداري:</b> التشغيلة دي فاضية: {reason}. "
                                  "هنكمل عادي في المواعيد الجاية، والرسالة دي مش هتتكرر النهارده."):
                meta["blocked_notice"] = today

    candidates = []
    for asin, item in found.items():
        apply_effective(item)
        seen_before = asin in state
        rec = state.setdefault(asin, {"lowest": item["price"], "lowest_at": today})
        prev_lowest, prev_at = rec["lowest"], rec.get("lowest_at")
        if item["price"] < rec["lowest"]:
            rec["lowest"], rec["lowest_at"] = item["price"], today
        points = list(history.get(asin, []))  # التاريخ قبل التشغيلة دي
        record_price(history, asin, item["price"], stamp)
        if not is_deal(item):
            continue
        last = rec.get("notified_price") or rec.get("queued_price")
        if last is None or item["eff"] <= last * (1 - RENOTIFY_DROP):
            bot_history = {"lowest": prev_lowest, "at": prev_at} if seen_before else None
            lowest_ever = seen_before and item["eff"] <= prev_lowest
            candidates.append((item, bot_history, points, lowest_ever))

    candidates.sort(key=lambda c: c[0]["eff_discount"], reverse=True)
    kanbkam = Kanbkam(session)
    fakes = sent = queued = low_score = lookups = 0
    approved = []  # للداشبورد: كل لقطة عدّت، اتبعتت أو اتأجلت
    for item, bot_history, points, lowest_ever in candidates:
        if sent + queued >= MAX_ALERTS_PER_RUN:
            break
        asin, rec = item["asin"], state[item["asin"]]
        # البوت نفسه شاف المنتج بسعر أقل بكتير قبل كده
        if bot_history and item["eff"] > bot_history["lowest"] * (1 + MAX_ABOVE_LOWEST):
            print(f"  🚫 وهمي (البوت شافه بـ {bot_history['lowest']:,.0f}): {asin} بـ {item['eff']:,.0f}")
            fakes += 1
            track_week(state, fake=True)
            continue
        if rec.get("fake_price") == item["price"]:
            print(f"  🚫 وهمي (متفحوص قبل كده بنفس السعر): {asin}")
            fakes += 1
            continue
        if rec.get("low_score_price") == item["eff"]:
            low_score += 1  # سكوره كان قليل بنفس السعر، مش هنضيّع عليه فحص
            continue
        if lookups >= MAX_LOOKUPS:
            print("  ⏸️ خلصنا حد الفحص للتشغيلة دي، الباقي المرة الجاية")
            break
        if lookups:
            time.sleep(random.uniform(2, 4))
        lookups += 1

        details = fetch_details(session, asin) or {}
        if details.get("in_stock") is False:
            print(f"  📦 خلصان من المخزن: {asin}")
            continue
        if not item["coupon_price"] and (coupon := parse_coupon(details.get("coupon_text"), item["price"])):
            item["coupon_price"] = coupon
            apply_effective(item)
        kb = kanbkam.check(item)
        if kb and item["eff"] > kb["lowest"] * (1 + MAX_ABOVE_LOWEST):
            rec["fake_price"] = item["price"]
            print(f"  🚫 وهمي (أقل سعر على كانبكام {kb['lowest']:,.0f}): {asin} بـ {item['eff']:,.0f}")
            fakes += 1
            track_week(state, fake=True)
            continue
        if not kb:
            print(f"  ⚠️ مفيش بيانات من كانبكام: {asin}")
            if SKIP_IF_KANBKAM_FAILS:
                continue

        seller = details.get("seller")
        ref = price_reference(item, kb, bot_history, points)
        score = deal_score(item, ref, seller)
        glitch = is_glitch(item, ref)
        if score < MIN_SCORE and not glitch:
            print(f"  😐 سكور قليل ({score}): {asin}")
            rec["low_score_price"] = item["eff"]
            low_score += 1
            continue
        deal = {
            "asin": asin, "title": item["title"], "eff": item["eff"], "real": ref["real"],
            "score": score, "label": score_label(score), "url": item["url"], "photo": item["image"],
            "caption": format_msg(item, kb, bot_history, ref, score, seller, glitch, lowest_ever),
            "buttons": [{"text": "📈 كان بكام", "url": kanbkam_url(item)}, {"text": "🛒 اشتري", "url": item["url"]}],
        }
        print(f"  {'🚨 غلطة سعر' if glitch else '✨ لقطة'} سكور {score}: {asin} بـ {item['eff']:,.0f}")
        approved.append(dash_record(item, ref, score, seller, glitch, stamp))
        if quiet and not glitch:  # بالليل: نأجلها لملخص الصبح
            meta.setdefault("queue", {})[asin] = deal
            rec["queued_price"] = item["eff"]
            queued += 1
            track_week(state, deal)
            continue
        if deliver(deal, channel_min, loud=glitch):
            rec["notified_price"] = item["eff"]
            sent += 1
            track_week(state, deal)
        time.sleep(1)

    update_dashboard(approved, found, meta.get("queue", {}), history, now)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    save_history(history)
    print(f"✅ اتفحص {len(found)} منتج | مرشحين {len(candidates)} | وهمي اترفض {fakes} | "
          f"سكور قليل {low_score} | اتأجل للصبح {queued} | اتبعت {sent}")


if __name__ == "__main__":
    main()
