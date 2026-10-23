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
from datetime import datetime, timedelta
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
MAX_DETAIL_PAGES = 8     # أقصى صفحات منتجات (للبايع والتوفر) في التشغيلة، لأحسن المرشحين بس
MAX_AMAZON_REQUESTS = 40  # حد أقصى لكل طلبات أمازون في التشغيلة (الرئيسية + البحث + صفحات المنتجات + الإعادات)
BACKOFF_HOURS = (6, 12, 24)  # لو أمازون حجبنا: نبطّل نكلمه 6 ساعات، ولو اتكرر 12، وبعدين 24
MIN_HOURS_BETWEEN_FULL_RUNS = 2  # تشغيلتين قريبين (Worker + GitHub): التانية مبتكلمش أمازون
MAX_WATCH_CHECKS = 5     # أقصى منتجات من قايمة المتابعة (/watch) تتفحص في التشغيلة (بالدور)
# 🚨 بلاغات المتابعين (reports.json، الـ Worker بيكتبه)
MAX_REPORT_CHECKS = 5    # أقصى صفحات منتجات للبلاغات في التشغيلة
REPORT_MIN_RATING = 4.0  # النشر التلقائي: أقل تقييم
REPORT_SCORE_BONUS = 10  # النشر التلقائي: السكور لازم يبقى حد القناة + 10
REPORT_REPOST_DAYS = 7   # النشر التلقائي: ميكونش اتنشر في آخر 7 أيام
REVIEW_NOTICES_PER_DAY = 10  # أقصى رسايل مراجعة ليك في اليوم (الباقي في 📥 بلاغات مستنية)
REPORT_UNDO_MINUTES = 10     # قرارك على بلاغ بيتنفذ بعد ما فترة ↩️ تراجع تخلص
BOT_USERNAME = "loatamazonbot"
# ============================================================

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")          # الشات الخاص: كل حاجة + رسايل الإدارة
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")    # القناة: اللقطات المختارة بس
AMAZON_TAG = os.getenv("AMAZON_TAG", "")             # تاج الأفلييت (مقفول لو فاضي)، ولازم لـ Creators API
CREATORS_ID = os.getenv("AMAZON_CREATORS_CREDENTIAL_ID", "")         # Creators API (مقفول لو فاضي)
CREATORS_SECRET = os.getenv("AMAZON_CREATORS_CREDENTIAL_SECRET", "")
BOT_SHARED_SECRET = os.getenv("BOT_SHARED_SECRET", "")  # عشان نقرا المتابعة والمكتوم من الـ Worker
SELFTEST = os.getenv("SELFTEST", "").lower() == "true"
ANNOUNCE = os.getenv("ANNOUNCE", "")  # ملف رسالة (HTML) يتنشر في القناة ويتثبت، من غير بحث
ANNOUNCE_EDIT = os.getenv("ANNOUNCE_EDIT", "")  # رقم رسالة في القناة نعدّلها مكانها بدل بوست جديد
STATE_FILE = Path(__file__).with_name("deals_state.json")
CONFIG_FILE = Path(__file__).with_name("config.json")  # إعدادات من تليجرام (الـ Worker بيكتبه، البوت بيقراه بس)
HISTORY_FILE = Path(__file__).with_name("data") / "history.json"
DOCS_DIR = Path(__file__).with_name("docs")  # الداشبورد على GitHub Pages
CHANNEL_URL = "https://t.me/loata_amazon"
REPORTS_FILE = Path(__file__).with_name("reports.json")  # البلاغات وقراراتك (الـ Worker بيكتبه، البوت بيقراه بس)
REPORT_URL = f"https://t.me/{BOT_USERNAME}?start=report"
MAX_LOOKUPS = MAX_ALERTS_PER_RUN * 2
CAIRO = ZoneInfo("Africa/Cairo")
STORE_NAMES = {"noon": "نون", "jumia": "جوميا", "jumai": "جوميا"}
SELLER_NAMES = {"amazon": "أمازون نفسه", "fba": "شحن أمازون (FBA)", "third": "بايع خارجي"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
]
ASIN_RE = re.compile(r"[A-Z0-9]{10}")
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")
AMAZON_HEADERS = {"Accept-Language": "en-US,en;q=0.9,ar;q=0.8", "Referer": "https://www.amazon.eg/"}


def now_cairo():
    return datetime.now(CAIRO)


# المفرد، المثنى، الجمع (3-10). 11 وأكتر بيرجع للمفرد: "11 ساعة"
AR_WORDS = {
    "ساعة": ("ساعة", "ساعتين", "ساعات"), "دقيقة": ("دقيقة", "دقيقتين", "دقايق"),
    "يوم": ("يوم", "يومين", "أيام"), "لقطة": ("لقطة", "لقطتين", "لقطات"),
    "منتج": ("منتج", "منتجين", "منتجات"), "كلمة": ("كلمة", "كلمتين", "كلمات"),
}


def ar_count(n, word):
    """عدد بالعربي صح: ساعة، ساعتين، 3 ساعات، 11 ساعة (ونفس الكلام للكلمات التانية)"""
    one, two, many = AR_WORDS[word]
    n = int(n)
    if n == 1:
        return one
    if n == 2:
        return two
    return f"{n} {many if 3 <= n % 100 <= 10 else one}"


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


def apply_config(cfg):
    """config.json (من أوامر تليجرام) بيغيّر الإعدادات الافتراضية اللي فوق"""
    global SEARCHES, MIN_DISCOUNT, MIN_SCORE
    changes = []
    if isinstance(cfg.get("min_discount"), (int, float)) and 1 <= cfg["min_discount"] <= 95:
        MIN_DISCOUNT = cfg["min_discount"]
        changes.append(f"أقل خصم {MIN_DISCOUNT}%")
    if isinstance(cfg.get("min_score"), (int, float)) and 0 <= cfg["min_score"] <= 100:
        MIN_SCORE = cfg["min_score"]
        changes.append(f"أقل سكور {MIN_SCORE}")
    removed = {k.strip().lower() for k in cfg.get("removed_keywords", []) if isinstance(k, str)}
    extra = [k.strip().lower() for k in cfg.get("extra_keywords", []) if isinstance(k, str) and k.strip()]
    disabled = [c for c in cfg.get("disabled_categories") or [] if c in SEARCH_GROUPS]  # 🗂️ الأقسام
    if disabled:
        removed |= {k for c in disabled for k in SEARCH_GROUPS[c]}
        changes.append(f"أقسام مقفولة: {'، '.join(disabled)}")
    searches = [s for s in SEARCHES if s not in removed]
    searches += [k for k in dict.fromkeys(extra) if k not in searches and k not in removed]
    if searches:
        if searches != SEARCHES:
            changes.append(f"كلمات البحث {len(searches)} (+{len(set(searches) - set(SEARCHES))} "
                           f"-{len(set(SEARCHES) - set(searches))})")
        SEARCHES = searches
        for k in extra:
            CATEGORY_OF.setdefault(k, "إضافات")
    else:
        print("⚠️ config.json شال كل كلمات البحث، هنكمل بالافتراضي")
    if changes:
        print(f"⚙️ من config.json: {'، '.join(changes)}")


def config_mute_until(cfg, now):
    """/mute: لحد إمتى البوت ساكت (None لو مش مكتوم)"""
    try:
        until = datetime.fromisoformat(cfg["mute_until"]) if cfg.get("mute_until") else None
    except (TypeError, ValueError):
        return None
    return until if until and until > now else None


def make_session():
    if browser_requests:
        print("🌐 وضع المتصفح الحقيقي شغال")
        return browser_requests.Session(impersonate="chrome")
    s = requests.Session()
    s.headers["User-Agent"] = random.choice(USER_AGENTS)
    return s


class AmazonBlocked(Exception):
    """أمازون رجّع صفحة تحقق من البوتات (كابتشا أو bm-verify بتاعة Akamai)"""


class AmazonBudgetSpent(Exception):
    """خلصنا MAX_AMAZON_REQUESTS طلب في التشغيلة دي"""


AMAZON_CALLS = 0  # عداد طلبات أمازون في التشغيلة (main بيصفّره)


def amazon_get(session, url):
    """كل طلب لأمازون بيعدي من هنا عشان الحد الأقصى يتطبق على الكل"""
    global AMAZON_CALLS
    if AMAZON_CALLS >= MAX_AMAZON_REQUESTS:
        raise AmazonBudgetSpent()
    AMAZON_CALLS += 1
    return session.get(url, headers=AMAZON_HEADERS, timeout=30)


def is_challenge(text):
    return "validateCaptcha" in text or "bm-verify" in text


def fetch(session, url):
    for attempt in range(3):
        try:
            r = amazon_get(session, url)
            if r.status_code == 200 and is_challenge(r.text):
                # منكررش: التكرار بيطوّل التشغيلة وبيخلي الحجب أسوأ
                raise AmazonBlocked()
            if r.status_code == 200:
                return r.text
            print(f"  ⚠️ أمازون رجّع {r.status_code} (محاولة {attempt + 1})")
        except (AmazonBlocked, AmazonBudgetSpent):
            raise
        except Exception as e:
            print(f"  ⚠️ خطأ اتصال: {e}")
        time.sleep(random.uniform(15, 30))
    return None


def backoff_active(meta, now):
    """فترة الراحة من أمازون لسه شغالة؟"""
    b = meta.get("amazon_backoff")
    return b if b and now < datetime.fromisoformat(b["until"]) else None


def record_block(meta, now):
    """أمازون حجبنا: فترة الراحة بتطول كل مرة (6 ← 12 ← 24 ساعة) لحد تشغيلة نضيفة"""
    prev = meta.get("amazon_backoff") or {}
    level = min(prev.get("level", 0) + 1, len(BACKOFF_HOURS))
    hours = BACKOFF_HOURS[level - 1]
    until = now + timedelta(hours=hours)
    meta["amazon_backoff"] = {"level": level, "since": prev.get("since") or now.isoformat(timespec="minutes"),
                              "until": until.isoformat(timespec="minutes")}
    print(f"🛑 أمازون حاجبنا (مرة رقم {level}): هنبطّل نكلمه {ar_count(hours, "ساعة")}، لحد {until:%Y-%m-%d %H:%M} بتوقيت القاهرة")
    return hours, until


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
    print(f"🖥️ الداشبورد: {ar_count(len(listing), 'لقطة')}")


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
    # Creators API مفيهاش تقييمات (rating_known = False): مبنرفضش على التقييم اللي مش معروف
    if MIN_RATING and item.get("rating_known", True) and (item["rating"] is None or item["rating"] < MIN_RATING):
        return False
    return True


def fetch_details(session, asin):
    """صفحة المنتج: مين البايع، متوفر ولا لأ، وكوبون لو مكانش ظاهر في نتايج البحث"""
    try:
        r = amazon_get(session, f"https://www.amazon.eg/dp/{asin}?language=en_AE")
    except AmazonBudgetSpent:
        raise
    except Exception as e:
        print(f"  ⚠️ صفحة المنتج وقعت: {e}")
        return None
    if r.status_code == 200 and is_challenge(r.text):
        raise AmazonBlocked()
    if r.status_code != 200:
        print(f"  ⚠️ صفحة المنتج رجّعت {r.status_code}")
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
    price = next((p for el in soup.select("#corePrice_feature_div span.a-price span.a-offscreen")
                  if (p := parse_number(el.get_text()))), None)
    image = soup.select_one("#landingImage")
    list_el = soup.select_one("span.basisPrice span.a-offscreen")  # السعر القديم (بيظهر بس لو فيه خصم)
    rating_el = soup.select_one("#acrPopover")
    rating = parse_number(rating_el.get("title") or rating_el.get_text()) if rating_el else None
    reviews = parse_number(text("#acrCustomerReviewText"))
    return {
        "seller": seller,
        "in_stock": not re.search(r"unavailable|out of stock|غير متوفر", availability, re.I) if availability else None,
        "coupon_text": text("#promoPriceBlockMessage_feature_div"),
        "price": price,  # للمتابعة (/watch) والبلاغات
        "list_price": parse_number(list_el.get_text()) if list_el else None,
        "rating": rating if rating and rating <= 5 else None,
        "reviews": int(reviews) if reviews else None,
        "title": text("#productTitle"),
        "image": (image.get("data-old-hires") or image.get("src")) if image else None,
    }



# ================== 🔒 المتابعة والمكتوم (خاص، في KV عند الـ Worker) ==================
# الريبو عام، فالمتابعة والمكتوم مش في config.json: البوت بيقراهم من الـ Worker بسر مشترك
WORKER_URL = "https://loata-bot.loata-bot-worker.workers.dev"


def load_private():
    """{watch, muted_asins, watch_state} من الـ Worker، أو None لو السر مش موجود أو الطلب فشل"""
    if not BOT_SHARED_SECRET:
        return None
    try:
        r = requests.get(f"{WORKER_URL}/bot/private", headers={"Authorization": f"Bearer {BOT_SHARED_SECRET}"}, timeout=20)
        if r.status_code == 200:
            data = r.json()
            return {"watch": data.get("watch") or {}, "muted_asins": data.get("muted_asins") or [],
                    "watch_state": data.get("watch_state") or {}}
        print(f"⚠️ الـ Worker رد بـ {r.status_code} على المتابعة والمكتوم")
    except (requests.RequestException, ValueError) as e:
        print(f"⚠️ مقدرتش أوصل للـ Worker: {e}")
    return None


def save_watch_state(states):
    """حالة منتجات المتابعة (السعر والتوفر) بترجع للـ Worker، مش لـ deals_state.json العام"""
    if not BOT_SHARED_SECRET:
        return False
    try:
        r = requests.put(f"{WORKER_URL}/bot/watch_state", json={"watch_state": states},
                         headers={"Authorization": f"Bearer {BOT_SHARED_SECRET}"}, timeout=20)
        return r.status_code == 200
    except requests.RequestException as e:
        print(f"⚠️ حالة المتابعة متحفظتش في الـ Worker: {e}")
        return False


# ================== قايمة المتابعة (/watch) ==================
def check_watchlist(session, cfg, meta, found, now, quiet_or_muted, details=None, states=None):
    """منتجات المتابعة: تنبيه لو السعر وصل للهدف أو أقل، أو لو رجع في المخزن (للخاص بس)"""
    # target = تنبيه لما السعر يوصله، stock_only = تنبيه لما يرجع المخزن بس
    watch = {a: w for a, w in (cfg.get("watch") or {}).items()
             if isinstance(w, dict) and (w.get("target") or w.get("stock_only"))}
    # من الـ Worker (خاص)؛ ولو مفيش Worker (تجربة محلية) في الحالة
    states = states if states is not None else meta.setdefault("watch_state", {})
    for asin in list(states):  # اللي اتشال بـ /unwatch
        if asin not in watch:
            del states[asin]
    if not watch:
        return 0
    asins = sorted(watch)
    start = meta.get("watch_next", 0) % len(asins)
    order = asins[start:] + asins[:start]
    fetched = checked = 0
    for asin in order:
        w, st = watch[asin], states.setdefault(asin, {})
        if asin in found:  # ظهر في البحث، من غير ما نفتح صفحته
            info = {"price": found[asin]["eff"], "in_stock": True, "title": found[asin]["title"]}
        else:
            if fetched >= MAX_WATCH_CHECKS:
                break
            fetched += 1
            # ممكن ترمي AmazonBlocked/AmazonBudgetSpent (السكرابر) أو CreatorsAuthError (الـ API)
            info = (details or (lambda a: fetch_details(session, a)))(asin)
            if info is None:
                continue
        checked += 1
        title = info.get("title") or w.get("title") or asin
        price, in_stock = info.get("price"), info.get("in_stock")
        link = amazon_link(asin)
        alerts = []
        if st.get("in_stock") is False and in_stock:
            alerts.append("📦 <b>رجع في المخزن!</b>")
        target = w.get("target")
        if target and in_stock is not False and price and price <= target:
            if not st.get("alerted_price") or price < st["alerted_price"]:
                alerts.append(f"🎯 <b>السعر وصل للي انت عايزه!</b> {price:,.0f} ج.م (هدفك {target:,.0f})")
                st["alerted_price"] = price
        elif target and price and price > target:
            st.pop("alerted_price", None)  # غلي تاني، فلو نزل تاني ننبّه
        if alerts:
            text = "\n".join(["👀 <b>متابعة</b>", *alerts, html.escape(title[:200]),
                              f"💰 السعر دلوقتي: {price:,.0f} ج.م" if price else "", link])
            print(f"  👀 تنبيه متابعة: {asin}")
            send_text(CHAT_ID, "\n".join(l for l in text.split("\n") if l), loud=not quiet_or_muted)
        st.update({"price": price, "in_stock": in_stock, "checked": now.isoformat(timespec="minutes")})
    meta["watch_next"] = (start + fetched) % len(asins)
    print(f"👀 المتابعة: اتفحص {checked} من {ar_count(len(watch), 'منتج')} ({fetched} صفحة منتج)")
    return fetched


# ================== 🚨 بلاغات المتابعين ==================
# reports.json (الـ Worker): {"reports": {id: {asin, at, credit?, decision?, decided_at?}}}
# _meta.reports (البوت): {id: {asin, status, reasons, title, price, old, discount, score, ...}}
# status: dropped (بلاغ بايظ، مبيوصلكش) | published | review | rejected | watched | expired
def report_item(asin, info):
    """منتج البلاغ من صفحة أمازون، أو (None, السبب) لو البلاغ بايظ. اللينك بيتبني من الـ ASIN بس"""
    if not info or not info.get("title"):
        return None, "not_product"
    if info.get("in_stock") is False:
        return None, "out_of_stock"
    if not info.get("price"):
        return None, "not_product"
    price, old = info["price"], info.get("list_price")
    coupon = parse_coupon(info.get("coupon_text"), price)
    if (not old or old <= price) and not coupon:
        return None, "no_discount"
    item = {"asin": asin, "title": info["title"], "price": price, "old": old if old and old > price else price,
            "rating": info.get("rating"), "reviews": info.get("reviews"), "coupon_price": coupon,
            "image": info.get("image"), "category": "", "url": amazon_link(asin),
            "rating_known": info.get("rating_known", True), "buybox": info.get("buybox")}
    apply_effective(item)
    return item, None


def report_checks(item, rec, points, kb, ref, score, seller, channel_min, autopublish, now):
    """كل شرط من شروط النشر التلقائي اللي فشل، بالعربي (فاضية = ينفع يتنشر لوحده)"""
    failed = []
    has_history = bool(rec) or bool(points)
    if not has_history:
        failed.append("مفيش تاريخ أسعار")
    if rec and item["eff"] > rec["lowest"] * (1 + MAX_ABOVE_LOWEST):
        failed.append(f"شكله خصم وهمي: البوت شافه بـ {rec['lowest']:,.0f} ج.م")
    elif kb and item["eff"] > kb["lowest"] * (1 + MAX_ABOVE_LOWEST):
        failed.append(f"شكله خصم وهمي: أقل سعر على كانبكام {kb['lowest']:,.0f} ج.م")
    need = channel_min + REPORT_SCORE_BONUS
    if score < need:
        failed.append(f"السكور {score} أقل من المطلوب ({need})")
    if not item.get("rating_known", True):
        # Creators API مفيهاش تقييمات: بدل التقييم، لازم يبقى أمازون نفسه البايع وكسبان الـ buy box
        # (وده بيغطي شرط البايع كمان)
        if not (seller == "amazon" and item.get("buybox")):
            failed.append("مش متباع من أمازون نفسه (buy box)")
    else:
        if item["rating"] is None:
            failed.append("مفيش تقييم")
        elif item["rating"] < REPORT_MIN_RATING:
            failed.append(f"التقييم {item['rating']} أقل من {REPORT_MIN_RATING:g}")
        if seller not in ("amazon", "fba"):
            failed.append("البياع مش أمازون" if seller == "third" else "مش عارفين مين البياع")
    posted = rec and rec.get("notified_at")
    if posted and now - datetime.fromisoformat(posted) < timedelta(days=REPORT_REPOST_DAYS):
        failed.append(f"اتنشر في آخر {ar_count(REPORT_REPOST_DAYS, 'يوم')}")
    if not autopublish:
        failed.append("النشر التلقائي للبلاغات مقفول")
    return failed


def publish_report(rid, rep, item, kb, bot_history, ref, score, seller, rec, approved, stamp, by_owner):
    """ينشر البلاغ في القناة (+ شكر)، ويبعتلك رسالة ساكتة فيها 🗑️ احذف من القناة"""
    caption = format_msg(item, kb, bot_history, ref, score, seller, False, False)
    if not bot_history:
        caption = "⚠️ <b>اتأكد بنفسك:</b> لسه معندناش تاريخ أسعار للمنتج ده\n" + caption
    credit = rep.get("credit")  # @username بس، ولو صاحبه وافق
    thanks = f"🙏 شكرًا لـ @{credit}" if credit and re.fullmatch(r"[A-Za-z0-9_]{5,32}", credit) else "🙏 شكرًا لأحد المتابعين"
    deal = {"asin": item["asin"], "title": item["title"], "eff": item["eff"], "real": ref["real"], "score": score,
            "label": score_label(score), "url": item["url"], "photo": item["image"],
            "caption": f"{caption}\n{thanks}",
            "buttons": [{"text": "📈 كان بكام", "url": kanbkam_url(item)}, {"text": "🛒 اشتري", "url": item["url"]}]}
    sent = send_deal(CHANNEL_ID, deal) if (TOKEN and CHANNEL_ID) else None
    if not sent:
        print(f"  ⚠️ بلاغ {rid}: النشر في القناة فشل")
        return None
    rec["notified_price"], rec["notified_at"] = item["eff"], stamp
    approved.append(dash_record(item, ref, score, seller, False, stamp))
    head = "✅ <b>نشرت البلاغ اللي وافقت عليه</b>" if by_owner else "🚨 <b>اتنشر بلاغ تلقائي</b>"
    send_text(CHAT_ID, f"{head}\n{html.escape(item['title'][:120])}\n💰 {item['eff']:,.0f} ج.م — سكور {score}",
              buttons=[[{"text": "🗑️ احذف من القناة", "callback_data": f"rd:{rid}"}]])
    print(f"  🚨 بلاغ {rid} اتنشر في القناة: {item['asin']}")
    return sent


def message_id(sent):
    return sent.get("message_id") if isinstance(sent, dict) else None


def review_text(res):
    lines = ["📥 <b>بلاغ محتاج رأيك</b>", html.escape(res["title"][:150]),
             f"💰 {res['price']:,.0f} ج.م بدل {res['old']:,.0f} ج.م (خصم {res['discount']}%)",
             f"⭐ سكور {res['score']}/100", "", "<b>ليه متنشرش لوحده:</b>"]
    lines += [f"• {html.escape(r)}" for r in res["reasons"]]
    return "\n".join(lines)


def review_buttons(rid, asin):
    return [[{"text": "✅ انشر", "callback_data": f"rv:p:{rid}"}, {"text": "❌ ارفض", "callback_data": f"rv:x:{rid}"},
             {"text": "👁️ تابعه بس", "callback_data": f"rv:w:{rid}"}],
            [{"text": "🛒 شوفه على أمازون", "url": amazon_link(asin)}]]


def process_reports(session, cfg, meta, state, history, now, stamp, channel_min, amazon_ok, approved, details=None):
    """البلاغات: قراراتك على اللي اتراجع، وبعدين البلاغات الجديدة (لحد MAX_REPORT_CHECKS صفحة)"""
    reports = load_json(REPORTS_FILE, {}).get("reports", {})
    results = meta.setdefault("reports", {})
    for rid in [r for r, v in results.items()  # نشيل أي حاجة أقدم من 30 يوم
                if now - datetime.fromisoformat(v.get("checked") or v.get("at") or stamp) > timedelta(days=30)]:
        del results[rid]
    if not reports:
        return
    autopublish = cfg.get("report_autopublish", True) is not False
    kanbkam, pages = Kanbkam(session), 0
    notices = meta.setdefault("review_notices", {})
    if notices.get("day") != now.date().isoformat():
        notices.update({"day": now.date().isoformat(), "count": 0})

    def page(asin):
        nonlocal pages
        pages += 1
        return (details or (lambda a: fetch_details(session, a)))(asin)  # ممكن ترمي AmazonBlocked/AmazonBudgetSpent

    # 1) قراراتك (بعد ما فترة ↩️ تراجع تخلص)
    for rid, rep in reports.items():
        res, decision = results.get(rid), rep.get("decision")
        if not res or res["status"] != "review" or not decision:
            continue
        decided = datetime.fromisoformat(rep.get("decided_at") or stamp)
        if now - decided < timedelta(minutes=REPORT_UNDO_MINUTES):
            continue
        if decision == "reject":
            res["status"] = "rejected"
        elif decision == "watch":
            res["status"] = "watched"  # الـ Worker ضافه للمتابعة ساعة ما دوست
        elif decision == "publish" and amazon_ok and pages < MAX_REPORT_CHECKS:
            item, broken = report_item(rep["asin"], page(rep["asin"]))
            if broken or item["eff"] > res["price"]:
                res["status"] = "expired"
                why = "خلص من المخزن أو الصفحة اتغيرت" if broken else f"السعر زاد لـ {item['eff']:,.0f} ج.م"
                send_text(CHAT_ID, f"⏭️ <b>منشرتش البلاغ اللي وافقت عليه</b>\n{html.escape(res['title'][:120])}\n{why}")
                continue
            rec = state.get(rep["asin"])
            points = history.get(rep["asin"], [])
            bot_history = {"lowest": rec["lowest"], "at": rec.get("lowest_at")} if rec else None
            ref = price_reference(item, None, bot_history, points)
            sent = publish_report(rid, rep, item, None, bot_history, ref, res["score"], res.get("seller"),
                                  state.setdefault(rep["asin"], {"lowest": item["price"], "lowest_at": now.date().isoformat()}),
                                  approved, stamp, by_owner=True)
            if sent:
                res.update({"status": "published", "channel_message_id": message_id(sent)})

    # 2) البلاغات الجديدة
    for rid, rep in sorted(reports.items(), key=lambda kv: kv[1].get("at", "")):
        if rid in results or not ASIN_RE.fullmatch(str(rep.get("asin", ""))):
            continue
        if not amazon_ok or pages >= MAX_REPORT_CHECKS:
            break
        asin = rep["asin"]
        info = page(asin)
        item, broken = report_item(asin, info)
        res = {"asin": asin, "at": rep.get("at"), "checked": stamp}
        if broken:  # بلاغ بايظ: مبيوصلكش خالص
            results[rid] = {**res, "status": "dropped", "reasons": [broken]}
            print(f"  🗑️ بلاغ {rid} بايظ ({broken}): {asin}")
            continue
        rec = state.get(asin)
        points = list(history.get(asin, []))
        bot_history = {"lowest": rec["lowest"], "at": rec.get("lowest_at")} if rec else None
        kb = kanbkam.check(item)
        seller = info.get("seller")
        ref = price_reference(item, kb, bot_history, points)
        score = deal_score(item, ref, seller)
        failed = report_checks(item, rec, points, kb, ref, score, seller, channel_min, autopublish, now)
        # السعر اللي شفناه دلوقتي داتا حقيقية: بتدخل التاريخ زي أي منتج
        record_price(history, asin, item["price"], stamp)
        rec = state.setdefault(asin, {"lowest": item["price"], "lowest_at": now.date().isoformat()})
        if item["price"] < rec["lowest"]:
            rec["lowest"], rec["lowest_at"] = item["price"], now.date().isoformat()
        res.update({"title": item["title"][:150], "price": item["eff"], "old": item["old"],
                    "discount": item["eff_discount"], "score": score, "seller": seller,
                    "has_history": bool(bot_history or points)})
        if not failed:
            sent = publish_report(rid, rep, item, kb, bot_history, ref, score, seller, rec, approved, stamp, by_owner=False)
            if sent:
                results[rid] = {**res, "status": "published", "channel_message_id": message_id(sent)}
                continue
            failed = ["النشر في القناة فشل"]
        results[rid] = {**res, "status": "review", "reasons": failed, "notified": False}
        if notices["count"] < REVIEW_NOTICES_PER_DAY:
            if send_text(CHAT_ID, review_text(results[rid]), buttons=review_buttons(rid, asin)) or not TOKEN:
                notices["count"] += 1
                results[rid]["notified"] = True
        print(f"  📥 بلاغ {rid} محتاج رأيك ({'، '.join(failed)}): {asin}")
    if pages:
        print(f"🚨 البلاغات: {ar_count(pages, 'منتج')} اتفحص")


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


# ================== مصدر الداتا: Creators API (بديل السكرابر الرسمي) ==================
# كل حاجة هنا من الدوكس الرسمية (affiliate-program.amazon.com/creatorsapi/docs/en-us):
#  - migrating-to-creatorsapi-from-paapi + get-started/using-curl: الـ API على https://creatorsapi.amazon/catalog/v1
#    (POST + JSON)، والتوكن OAuth 2.0 (client_credentials، scope "creatorsapi::default") صالح 3600 ثانية.
#  - using-curl "Regional Endpoints": مصر (EG) في منطقة EU (Version 3.2) → api.amazon.co.uk/auth/o2/token
#  - concepts/common-request-headers-and-parameters: Authorization: Bearer، Content-Type: application/json،
#    x-marketplace، و marketplace + partnerTag في الـ body.
#  - concepts/api-rates: في الأول 1 TPS و 8640 TPD، وGetItems بـ 10 ASIN = طلب واحد، والزيادة = 429.
#  - api-reference/resources/offersV2: مفيش IsAmazonFulfilled، ومفيش تقييمات في أي resource.
CREATORS_API_URL = "https://creatorsapi.amazon/catalog/v1"
CREATORS_TOKEN_URL = "https://api.amazon.co.uk/auth/o2/token"
CREATORS_MARKETPLACE = "www.amazon.eg"
CREATORS_TPS = 1
CREATORS_TPD = 8640
CREATORS_RESOURCES = ["images.primary.large", "itemInfo.title", "offersV2.listings.availability",
                      "offersV2.listings.condition", "offersV2.listings.isBuyBoxWinner",
                      "offersV2.listings.merchantInfo", "offersV2.listings.price"]
IN_STOCK_TYPES = {"IN_STOCK", "IN_STOCK_SCARCE"}
OUT_OF_STOCK_TYPES = {"OUT_OF_STOCK", "UNAVAILABLE"}


class CreatorsAuthError(Exception):
    """الكريدنشيالز ناقصة أو اترفضت (401/403): بنوقف ومنرجعش للسكرابر"""


class CreatorsError(Exception):
    """أي غلطة تانية من Creators API (بعد الإعادات)"""


class TokenBucket:
    """حد الطلبات في الثانية (TPS): كل طلب بياخد token، ولو مفيش بنستنى"""

    def __init__(self, rate, capacity=1, clock=None, sleep=None):
        self.rate, self.capacity = rate, capacity
        self.clock, self.sleep = clock or time.monotonic, sleep or time.sleep
        self.tokens, self.last = capacity, self.clock()

    def take(self):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens < 1:
            self.sleep((1 - self.tokens) / self.rate)
            self.tokens, self.last = 0, self.clock()
        else:
            self.tokens -= 1


def creators_error(r):
    """(النوع، الرسالة) من رد الغلط، زي {"__type": "com.amazon.creators#ResourceNotFoundException", "message": ...}"""
    try:
        data = r.json()
    except ValueError:
        return "", ""
    return str(data.get("__type", "")).split("#")[-1], str(data.get("message", ""))[:150]


class CreatorsClient:
    def __init__(self, cred_id, cred_secret, tag, meta, http=None, clock=None, sleep=None, today=None):
        self.cred_id, self.cred_secret, self.tag, self.meta = cred_id, cred_secret, tag, meta
        self.http, self.clock, self.sleep = http or requests, clock or time.monotonic, sleep or time.sleep
        self.bucket = TokenBucket(CREATORS_TPS, 1, self.clock, self.sleep)
        self.today = today or (lambda: now_cairo().date().isoformat())
        self.token, self.token_until = None, 0

    def _token(self):
        if self.token and self.clock() < self.token_until:
            return self.token  # التوكن بيتخزن لحد قبل ما يخلص بدقيقة (best-programming-practices)
        r = self.http.post(CREATORS_TOKEN_URL, json={"grant_type": "client_credentials", "client_id": self.cred_id,
                                                     "client_secret": self.cred_secret, "scope": "creatorsapi::default"},
                           headers={"Content-Type": "application/json"}, timeout=30)
        if r.status_code != 200:
            kind, msg = creators_error(r)
            raise CreatorsAuthError(f"token {r.status_code} {kind} {msg}".strip())
        data = r.json()
        self.token = data["access_token"]
        self.token_until = self.clock() + int(data.get("expires_in", 3600)) - 60
        return self.token

    def _count(self):
        """حد الطلبات في اليوم (TPD)، محفوظ في الحالة عشان يتحسب على كل التشغيلات"""
        usage = self.meta.setdefault("creators_usage", {})
        if usage.get("day") != self.today():
            usage.update({"day": self.today(), "count": 0})
        if usage["count"] >= CREATORS_TPD:
            raise CreatorsError(f"وصلنا حد اليوم ({CREATORS_TPD} طلب)")
        usage["count"] += 1

    def call(self, op, body):
        for attempt in range(4):
            self._count()
            self.bucket.take()
            r = self.http.post(f"{CREATORS_API_URL}/{op}",
                               json={**body, "partnerTag": self.tag, "marketplace": CREATORS_MARKETPLACE},
                               headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json",
                                        "x-marketplace": CREATORS_MARKETPLACE}, timeout=30)
            if r.status_code == 200:
                return r.json()
            kind, msg = creators_error(r)
            if r.status_code == 401 and attempt == 0 and "expired" in msg.lower():
                self.token = None  # TokenExpired: توكن جديد ونعيد مرة
                continue
            if r.status_code in (401, 403):
                raise CreatorsAuthError(f"{r.status_code} {kind}: {msg}")
            if r.status_code == 404:
                return {}  # ResourceNotFoundException = مفيش نتايج
            if r.status_code == 429 or r.status_code >= 500:
                self.sleep(2 ** attempt)  # ThrottleException/InternalServerException: exponential backoff
                continue
            raise CreatorsError(f"{r.status_code} {kind}: {msg}")
        raise CreatorsError(f"{op}: فشل بعد الإعادات (429/5xx)")

    def search(self, keywords, page):
        # search-items: itemCount من 1 لـ 10، itemPage من 1 لـ 10
        data = self.call("searchItems", {"keywords": keywords, "itemCount": 10, "itemPage": page,
                                         "resources": CREATORS_RESOURCES})
        return (data.get("searchResult") or {}).get("items") or []

    def get_items(self, asins):
        # get-items: لحد 10 itemIds في الطلب الواحد (وده طلب واحد في الحساب)
        items = []
        for i in range(0, len(asins), 10):
            data = self.call("getItems", {"itemIds": asins[i:i + 10], "itemIdType": "ASIN",
                                          "resources": CREATORS_RESOURCES})
            items += (data.get("itemResults") or {}).get("items") or []
        return items


def creators_listing(raw):
    listings = ((raw.get("offersV2") or {}).get("listings")) or []
    return next((l for l in listings if l.get("isBuyBoxWinner")), listings[0] if listings else None)


def creators_details(raw):
    """منتج من Creators API بنفس شكل fetch_details (عشان المتابعة والبلاغات والمرشحين)"""
    listing = creators_listing(raw) or {}
    price_box = listing.get("price") or {}
    price = (price_box.get("money") or {}).get("amount")
    basis = ((price_box.get("savingBasis") or {}).get("money") or {}).get("amount")
    avail = (listing.get("availability") or {}).get("type")
    merchant = (listing.get("merchantInfo") or {}).get("name") or ""
    primary = (raw.get("images") or {}).get("primary") or {}
    image = next(((primary.get(size) or {}).get("url") for size in ("large", "medium", "small")
                  if (primary.get(size) or {}).get("url")), None)
    return {
        # البايع: الاسم بس (مفيش IsAmazonFulfilled في Creators API)، فـ FBA مش معروف
        "seller": "amazon" if "amazon" in merchant.lower() else None,
        "in_stock": True if avail in IN_STOCK_TYPES else False if avail in OUT_OF_STOCK_TYPES else None,
        "coupon_text": "",
        "price": float(price) if price else None,
        "list_price": float(basis) if basis and price and basis > price else None,
        "rating": None, "reviews": None, "rating_known": False,
        "buybox": bool(listing.get("isBuyBoxWinner")),
        "title": (((raw.get("itemInfo") or {}).get("title")) or {}).get("displayValue") or "",
        "image": image,
        # concepts/api-rates: "Do not edit any of the URL parameters" فاللينك زي ما هو
        "url": raw.get("detailPageURL") or amazon_link(raw.get("asin", "")),
    }


def creators_item(raw, category):
    """نتيجة بحث من Creators API بنفس شكل parse_results، أو None لو مفيهاش خصم"""
    d = creators_details(raw)
    if not (raw.get("asin") and d["title"] and d["price"] and d["list_price"]):
        return None
    return {"asin": raw["asin"], "title": d["title"], "price": d["price"], "old": d["list_price"],
            "rating": None, "reviews": None, "rating_known": False, "image": d["image"], "coupon_price": None,
            "category": category, "url": d["url"], "seller": d["seller"], "in_stock": d["in_stock"]}


class CreatorsDetails:
    """صفحات المنتجات من GetItems: بنجمع لحد 10 ASIN في الطلب الواحد"""

    def __init__(self, client):
        self.client, self.cache = client, {}

    def prefetch(self, asins):
        need = [a for a in dict.fromkeys(asins) if a not in self.cache]
        for raw in self.client.get_items(need):
            self.cache[raw.get("asin")] = creators_details(raw)
        for a in need:
            self.cache.setdefault(a, None)  # ItemNotAccessible أو مش موجود

    def __call__(self, asin):
        if asin not in self.cache:
            self.prefetch([asin])
        return self.cache.get(asin)


def make_creators_client(meta):
    return CreatorsClient(CREATORS_ID, CREATORS_SECRET, AMAZON_TAG, meta)


def creators_missing():
    names = [("AMAZON_CREATORS_CREDENTIAL_ID", CREATORS_ID), ("AMAZON_CREATORS_CREDENTIAL_SECRET", CREATORS_SECRET),
             ("AMAZON_TAG", AMAZON_TAG)]
    return [n for n, v in names if not v]


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
    if item.get("rating_known", True):
        # 15: التقييم (3.5 = صفر، 5 = كامل)
        points += 15 * min(1, max(0, ((item["rating"] or 0) - 3.5) / 1.5))
        # 10: عدد التقييمات (1000 أو أكتر = كامل)
        points += 10 * min(1, math.log10((item["reviews"] or 0) + 1) / 3)
    else:
        points += 7.5 + 5  # Creators API مفيهاش تقييمات: نص النقط، لا مكافأة ولا عقاب
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
            line += f" — اتسجل من {ar_count(days, 'يوم')}" if days else " — اتسجل النهارده"
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


def send_text(chat_id, text, loud=False, buttons=None):
    if not (TOKEN and chat_id):
        print(text, "\n" + "-" * 40)
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "disable_notification": not loud, "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return bool(tg_call("sendMessage", payload))


def send_deal(chat_id, deal, loud=False):
    """صورة + كابشن + زراير، ولو الصورة فشلت يبعتها رسالة عادية"""
    if not (TOKEN and chat_id):
        return False
    """بيرجّع الرسالة اللي اتبعتت (فيها message_id) أو None"""
    rows = [deal["buttons"]]
    if chat_id == CHAT_ID and deal.get("asin"):  # في الخاص بس: زرار الكتم بيروح للـ Worker
        rows.append([{"text": "🔕 متبعتليش عن ده تاني", "callback_data": f"mute:{deal['asin']}"}])
    if chat_id == CHANNEL_ID:  # في القناة: أي حد يبلّغ عن لقطة
        rows.append([{"text": "🚨 بلّغ عن لقطة", "url": REPORT_URL}])
    common = {"chat_id": chat_id, "parse_mode": "HTML", "disable_notification": not loud,
              "reply_markup": {"inline_keyboard": rows}}
    if deal["photo"] and (sent := tg_call("sendPhoto", {**common, "photo": deal["photo"], "caption": deal["caption"]})):
        return sent
    return tg_call("sendMessage", {**common, "text": deal["caption"], "disable_web_page_preview": True})


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


DIGEST_TITLES = {"quiet": "🌅 <b>صباح الخير — لقطات الليلة</b>",
                 "mute": "🔔 <b>اللقطات اللي اتجمعت وانت عامل كتم</b>"}


def digest_text(deals, reason="quiet"):
    lines = [DIGEST_TITLES.get(reason, DIGEST_TITLES["quiet"]), ""]
    for i, d in enumerate(deals, 1):
        glitch = "🚨 " if d.get("glitch") else ""
        lines.append(f"{i}. {glitch}{d['label'].split()[0]} <a href=\"{d['url']}\">{html.escape(d['title'][:80])}</a>")
        lines.append(f"    💰 {d['eff']:,.0f} ج.م (خصم {d['real']}%) — سكور {d['score']}")
    return "\n".join(lines)


def send_digest(state, channel_min):
    """أول تشغيلة بعد ساعات الهدوء (أو بعد ما الكتم يخلص): ملخص واحد باللقطات اللي اتجمعت"""
    queue = state["_meta"].get("queue", {})
    if not queue:
        return
    top = sorted(queue.values(), key=lambda d: d["score"], reverse=True)[:DIGEST_SIZE]
    # "mute" = اتأجلت للخاص بس (القناة خدتها ساعتها)، "quiet" = ساعات الهدوء (للاتنين)
    reason = "mute" if all(d.get("queued_for") == "mute" for d in top) else "quiet"
    ok = send_text(CHAT_ID, digest_text(top, reason), loud=True) if TOKEN else False
    channel_top = [d for d in top if d.get("queued_for") != "mute" and for_channel(d, channel_min)]
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
    print(f"🌅 اتبعت الملخص ({reason}) بـ {ar_count(len(top), 'لقطة')}")


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


def announce(path, edit_id=""):
    """رسالة من ملف للقناة وتحتها زرار 🚨 بلّغ عن لقطة: لو edit_id موجود بنعدّل الرسالة دي مكانها
    (من غير بوست جديد ولا إشعار)، غير كده بننشر رسالة جديدة ونثبتها من غير إشعار"""
    file = Path(__file__).parent / path
    if not (TOKEN and CHANNEL_ID and file.is_file()):
        print(f"ANNOUNCE: مش هينفع (توكن/قناة/ملف ناقص): {path}")
        return
    body = {"chat_id": CHANNEL_ID, "text": file.read_text(encoding="utf-8").strip(), "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[{"text": "🚨 بلّغ عن لقطة", "url": REPORT_URL}]]}}
    if edit_id:
        edited = tg_call("editMessageText", {**body, "message_id": int(edit_id)})
        print(f"ANNOUNCE edited message_id={edit_id}: {bool(edited)}")
        return
    msg = tg_call("sendMessage", {**body, "disable_notification": True})
    print(f"ANNOUNCE sent: message_id={msg and msg.get('message_id')}")
    if msg:
        pinned = tg_call("pinChatMessage", {"chat_id": CHANNEL_ID, "message_id": msg["message_id"],
                                            "disable_notification": True})
        print(f"ANNOUNCE pinned: {bool(pinned)}")


def main():
    if SELFTEST:
        return selftest()
    if ANNOUNCE:
        return announce(ANNOUNCE, ANNOUNCE_EDIT)
    state = load_json(STATE_FILE, {})
    history = load_json(HISTORY_FILE, {})
    cfg = load_json(CONFIG_FILE, {})
    apply_config(cfg)
    private = load_private()
    if private is not None:  # المتابعة والمكتوم من الـ Worker، مش من config.json العام
        cfg["watch"], cfg["muted_asins"] = private["watch"], private["muted_asins"]
    meta = state.setdefault("_meta", {})
    now = now_cairo()
    quiet = QUIET_HOURS[0] <= now.hour < QUIET_HOURS[1]
    stamp, today = now.isoformat(timespec="minutes"), now.date().isoformat()
    print(f"🕐 {stamp} بتوقيت القاهرة{' (ساعات الهدوء)' if quiet else ''}")
    muted_until = config_mute_until(cfg, now)
    if muted_until:
        print(f"🔇 الخاص مكتوم لحد {muted_until.astimezone(CAIRO):%Y-%m-%d %H:%M} (القناة شغالة عادي)")
    muted_asins = set(cfg.get("muted_asins") or [])
    if private is None and TOKEN:  # على GitHub Actions ومقدرناش نجيبهم: نقول مرة في اليوم ونكمل من غيرهم
        cfg["watch"], muted_asins = {}, set()
        print("⚠️ المتابعة والمكتوم مش متاحين من الـ Worker، التشغيلة دي من غيرهم")
        if meta.get("private_alert") != today and send_text(
                CHAT_ID, "⚠️ <b>تنبيه إداري:</b> البوت مقدرش يجيب المتابعة والمنتجات المكتومة من الـ Worker "
                         "(BOT_SHARED_SECRET ناقص أو الـ Worker مبيردش). التشغيلة كملت من غيرهم."):
            meta["private_alert"] = today
    if private is not None:
        meta.pop("watch_state", None)  # كان زمان في الحالة العامة
    channel_min, history_days = channel_threshold(history, now)
    phase = "فترة التسخين" if channel_min == CHANNEL_MIN_SCORE_EARLY else "الحد العادي"
    print(f"📢 حد القناة: سكور {channel_min}+ ({phase}، عندنا {ar_count(history_days, 'يوم')} تاريخ من {HISTORY_WARMUP_DAYS})")
    if not quiet and not muted_until:
        send_digest(state, channel_min)
    if not quiet:
        weekly_report(state, now)

    global AMAZON_CALLS
    AMAZON_CALLS = 0
    found = {}
    blocked = False
    session = make_session()
    backoff = backoff_active(meta, now)
    ran_amazon = False  # التشغيلة دي كلمت أمازون فعلًا (مش فترة راحة ولا قاعدة الساعتين)
    # مصدر الداتا (⚙️ في لوحة التحكم): scraper = صفحات أمازون، creators_api = الـ API الرسمي
    source = cfg.get("source") if cfg.get("source") in ("scraper", "creators_api") else "scraper"
    meta["creators_ready"] = not creators_missing()  # الـ Worker بيشوف ده قبل ما يسمح بالتحويل
    creators, creators_stop = None, None
    if source == "creators_api":
        missing = creators_missing()
        if missing:
            creators_stop = ("missing", f"الكريدنشيالز ناقصة في GitHub Secrets: {'، '.join(missing)}")
        else:
            creators = make_creators_client(meta)
    print(f"🔌 مصدر الداتا: {'Creators API' if source == 'creators_api' else 'السكرابر'}")
    details_fn = CreatorsDetails(creators) if creators else None
    last_full = meta.get("last_full_run")
    since_full = now - datetime.fromisoformat(last_full) if last_full else None
    if creators_stop:  # ممنوع نرجع للسكرابر من غير ما صاحب البوت يقول
        print(f"🛑 Creators API: {creators_stop[1]}، التشغيلة دي من غير بحث")
    elif backoff and not creators:
        print(f"⏸️ أمازون في فترة راحة (مرة رقم {backoff['level']}) لحد {backoff['until']}، "
              "التشغيلة دي من غير أمازون خالص")
    elif since_full is not None and since_full < timedelta(hours=MIN_HOURS_BETWEEN_FULL_RUNS):
        # الـ Worker والـ GitHub schedule ممكن يشغّلوا تشغيلتين ورا بعض: التانية مبتكلمش أمازون
        print(f"⏭️ فيه تشغيلة كاملة من {ar_count(since_full.total_seconds() // 60, 'دقيقة')} ({last_full})، "
              f"التشغيلة دي من غير أمازون (أقل من {ar_count(MIN_HOURS_BETWEEN_FULL_RUNS, 'ساعة')})")
    else:
        ran_amazon = True
        meta["last_full_run"] = stamp
        rotation_start = meta.get("next", 0)
        searches = pick_searches(state)
        print(f"📋 كلمات التشغيلة دي: {', '.join(searches)}")
        completed = 0  # الكلمات اللي خلصت فعلًا، عشان الدور يكمل منها لو وقفنا في النص
        try:
            if not creators:
                # زيارة الصفحة الرئيسية الأول زي أي حد طبيعي، عشان ياخد الكوكيز
                try:
                    home = amazon_get(session, "https://www.amazon.eg/")
                    if home.status_code == 200 and is_challenge(home.text):
                        raise AmazonBlocked()
                except (AmazonBlocked, AmazonBudgetSpent):
                    raise
                except Exception:
                    pass
                time.sleep(random.uniform(2, 5))
            for search in searches:
                for page in range(1, PAGES_PER_SEARCH + 1):
                    print(f"🔎 {search} - صفحة {page}")
                    if creators:  # SearchItems
                        page_items = [i for raw in creators.search(search, page) if (i := creators_item(raw, search))]
                    else:
                        page_html = fetch(session, build_url(search, page))
                        if not page_html:
                            break
                        page_items = parse_results(page_html, search)
                        time.sleep(random.uniform(3, 7))
                    for item in page_items:
                        found.setdefault(item["asin"], item)
                completed += 1
        except CreatorsAuthError as e:
            creators_stop = ("rejected", f"Creators API رفض الطلب ({e})")
            print(f"🛑 {creators_stop[1]}، وقفنا")
        except CreatorsError as e:
            print(f"⚠️ Creators API: {e}، وقفنا البحث")
        except AmazonBlocked:
            blocked = True
            print("🛑 أمازون رجّع صفحة تحقق من البوتات، وقفنا البحث")
        except AmazonBudgetSpent:
            print(f"🧮 وصلنا الحد الأقصى لطلبات أمازون ({MAX_AMAZON_REQUESTS})، وقفنا البحث")
        meta["next"] = (rotation_start + completed) % len(SEARCHES)
        if completed < len(searches):
            print(f"↩️ الدور هيكمل من كلمة رقم {meta['next']} ({SEARCHES[meta['next']]}) المرة الجاية")
        for item in found.values():
            apply_effective(item)
        if not blocked and not creators_stop:  # المتابعة قبل صفحات المرشحين: طلب صريح من صاحب البوت
            try:
                if details_fn:  # GetItems: لحد 10 ASIN في الطلب الواحد
                    reports = load_json(REPORTS_FILE, {}).get("reports", {})
                    done = meta.get("reports", {})
                    details_fn.prefetch([a for a in (cfg.get("watch") or {}) if a not in found] +
                                        [r.get("asin") for rid, r in reports.items()
                                         if rid not in done and ASIN_RE.fullmatch(str(r.get("asin", "")))][:MAX_REPORT_CHECKS])
                check_watchlist(session, cfg, meta, found, now, quiet or bool(muted_until), details_fn,
                                private["watch_state"] if private is not None else None)
                if private is not None:
                    save_watch_state(private["watch_state"])
            except CreatorsAuthError as e:
                creators_stop = ("rejected", f"Creators API رفض الطلب ({e})")
            except CreatorsError as e:
                print(f"⚠️ Creators API في المتابعة: {e}")
            except AmazonBlocked:
                blocked = True
                print("🛑 صفحة منتج في المتابعة رجّعت تحقق من البوتات")
            except AmazonBudgetSpent:
                print(f"🧮 وصلنا الحد الأقصى لطلبات أمازون ({MAX_AMAZON_REQUESTS}) في المتابعة")

    approved = []  # للداشبورد: كل لقطة عدّت، اتبعتت أو اتأجلت (ومنها البلاغات)
    try:  # البلاغات قبل صفحات المرشحين: حد بعتها بنفسه
        process_reports(session, cfg, meta, state, history, now, stamp, channel_min,
                        ran_amazon and not blocked and not creators_stop, approved, details_fn)
    except CreatorsAuthError as e:
        creators_stop = ("rejected", f"Creators API رفض الطلب ({e})")
    except CreatorsError as e:
        print(f"⚠️ Creators API في البلاغات: {e}")
    except AmazonBlocked:
        blocked = True
        print("🛑 صفحة منتج في البلاغات رجّعت تحقق من البوتات")
    except AmazonBudgetSpent:
        print(f"🧮 وصلنا الحد الأقصى لطلبات أمازون ({MAX_AMAZON_REQUESTS}) في البلاغات")

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
        if asin in muted_asins:  # زرار 🔕 في تليجرام
            print(f"  🔕 مكتوم: {asin}")
            continue
        last = rec.get("notified_price") or rec.get("queued_price")
        if last is None or item["eff"] <= last * (1 - RENOTIFY_DROP):
            bot_history = {"lowest": prev_lowest, "at": prev_at} if seen_before else None
            lowest_ever = seen_before and item["eff"] <= prev_lowest
            candidates.append((item, bot_history, points, lowest_ever))

    candidates.sort(key=lambda c: c[0]["eff_discount"], reverse=True)
    kanbkam = Kanbkam(session)
    fakes = sent = queued = low_score = lookups = details_pages = 0
    details_on = not blocked  # صفحات المنتجات: لأحسن MAX_DETAIL_PAGES مرشح بس، وتقف لو أمازون حجبنا
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

        details = {}
        if "in_stock" in item:  # Creators API: البايع والتوفر جايين مع نتيجة البحث، من غير صفحة منتج
            details = {"seller": item.get("seller"), "in_stock": item["in_stock"], "coupon_text": ""}
        elif details_on and details_pages >= MAX_DETAIL_PAGES:
            print(f"  📄 خلصنا حد صفحات المنتجات ({MAX_DETAIL_PAGES})، الباقي من غير البايع")
            details_on = False
        if not details and details_on:
            details_pages += 1
            try:
                details = fetch_details(session, asin) or {}
            except AmazonBlocked:
                blocked, details_on = True, False
                print("  🛑 صفحة المنتج رجّعت تحقق من البوتات، هنكمل من غير صفحات منتجات")
            except AmazonBudgetSpent:
                details_on = False
                print(f"  🧮 وصلنا الحد الأقصى لطلبات أمازون ({MAX_AMAZON_REQUESTS})، الباقي من غير البايع")
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
        deal["glitch"] = glitch
        if quiet and not glitch:  # بالليل: نأجلها لملخص الصبح (الخاص والقناة)
            meta.setdefault("queue", {})[asin] = {**deal, "queued_for": "quiet"}
            rec["queued_price"] = item["eff"]
            queued += 1
            track_week(state, deal)
            continue
        if muted_until:  # /mute: القناة عادي، والخاص يستنى ملخص بعد الكتم
            if TOKEN and CHANNEL_ID and for_channel(deal, channel_min):
                send_deal(CHANNEL_ID, deal, loud=glitch)
            meta.setdefault("queue", {})[asin] = {**deal, "queued_for": "mute"}
            rec["queued_price"] = item["eff"]
            queued += 1
            track_week(state, deal)
            continue
        if deliver(deal, channel_min, loud=glitch):
            rec["notified_price"], rec["notified_at"] = item["eff"], stamp
            sent += 1
            track_week(state, deal)
        time.sleep(1)

    def admin_notice(text):  # رسالة إدارية واحدة في اليوم بالكتير
        if meta.get("blocked_notice") != today and send_text(CHAT_ID, f"⚠️ <b>تنبيه إداري:</b> {text}"):
            meta["blocked_notice"] = today

    if creators_stop:  # الـ API مش شغال: تنبيه مرة في اليوم، ومنرجعش للسكرابر لوحدنا
        meta["creators_status"] = creators_stop[0]
        if meta.get("creators_alert") != today and send_text(
                CHAT_ID, f"⚠️ <b>Creators API:</b> {html.escape(creators_stop[1])}.\n"
                         "البوت وقف البحث (ومش هيرجع للسكرابر لوحده). حط الكريدنشيالز في GitHub Secrets، "
                         "أو رجّع المصدر للسكرابر من ⚙️ الإعدادات في /menu."):
            meta["creators_alert"] = today
    elif blocked:
        hours, until = record_block(meta, now)
        admin_notice(f"أمازون رجّع صفحة تحقق من البوتات. البوت هيبطّل يكلم أمازون {ar_count(hours, 'ساعة')} "
                     f"(لحد {until:%H:%M} يوم {until:%d/%m} بتوقيت القاهرة) وبعدين يحاول لوحده.")
    elif found and (creators or meta.get("amazon_backoff")):
        if creators:
            meta["creators_status"] = "ok"
        elif meta.pop("amazon_backoff", None):
            print("✅ تشغيلة نضيفة: أمازون رجع طبيعي، وفترة الراحة اتصفّرت")
    elif ran_amazon and not found:  # (قاعدة الساعتين وفترة الراحة مش "ملقتش")
        print("⚠️ التشغيلة دي ملقتش ولا منتج")
        admin_notice("التشغيلة دي ملقتش ولا منتج من أمازون.")

    update_dashboard(approved, found, meta.get("queue", {}), history, now)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    save_history(history)
    print(f"✅ اتفحص {ar_count(len(found), 'منتج')} | مرشحين {len(candidates)} | وهمي اترفض {fakes} | "
          f"سكور قليل {low_score} | اتأجل للصبح {queued} | اتبعت {sent} | "
          f"صفحات منتجات {details_pages}/{MAX_DETAIL_PAGES} | طلبات أمازون {AMAZON_CALLS}/{MAX_AMAZON_REQUESTS}")


if __name__ == "__main__":
    main()
