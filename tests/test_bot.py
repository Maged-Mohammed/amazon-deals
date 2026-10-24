"""
اختبارات offline للبوت على صفحات HTML صناعية (tests/fixtures/، بيولّدها tests/make_fixtures.py).
مفيش ولا طلب للنت: الـ sessions وتليجرام كلهم متبدّلين، وأي محاولة نت بترمي NetworkUsed.

التشغيل:  python -X utf8 -m unittest discover -s tests -v
"""
import contextlib
import importlib.util
import io
import json
import re
import shutil
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FX = Path(__file__).resolve().parent / "fixtures"


def fx(name):
    return (FX / name).read_text(encoding="utf-8")


class NetworkUsed(BaseException):
    """BaseException عشان ميتبلعش جوه except Exception في السكربت"""


def no_network(*args, **kwargs):
    raise NetworkUsed("test tried to use the network")


class Resp:
    def __init__(self, text, status_code=200):
        self.text, self.status_code = text, status_code


class FixtureSession:
    """بيرد بالـ fixtures وبيعد الطلبات، وممكن يبدأ يرجّع صفحة تحقق بعد عدد معين من الطلبات"""

    def __init__(self, challenge_after=None, product="product_fba.html", products=None):
        self.calls, self.kinds, self.dp_asins = 0, [], []
        self.challenge_after, self.product, self.products = challenge_after, product, products or {}

    def get(self, url, **kwargs):
        self.calls += 1
        kind = "dp" if "/dp/" in url else "search" if "/s?" in url else "home"
        self.kinds.append(kind)
        if self.challenge_after is not None and self.calls > self.challenge_after:
            return Resp(fx("challenge.html"))
        if kind == "search":
            return Resp(fx("search.html"))
        if kind == "dp":
            asin = url.split("/dp/")[1].split("?")[0]
            self.dp_asins.append(asin)
            return Resp(fx(self.products.get(asin, self.product)))
        return Resp("<html><body>home</body></html>")


def load_bot(tmp):
    """نسخة جديدة من السكربت لكل اختبار، ملفاتها في فولدر مؤقت ومن غير نت"""
    spec = importlib.util.spec_from_file_location("bot_under_test", ROOT / "amazon_eg_deals.py")
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    bot.STATE_FILE = tmp / "deals_state.json"
    bot.CONFIG_FILE = tmp / "config.json"
    bot.HISTORY_FILE = tmp / "data" / "history.json"
    bot.DOCS_DIR = tmp / "docs"
    bot.TOKEN = bot.CHAT_ID = bot.CHANNEL_ID = bot.AMAZON_TAG = ""
    bot.time = types.SimpleNamespace(sleep=lambda *_: None)
    bot.requests = types.SimpleNamespace(post=no_network, get=no_network, Session=no_network,
                                         RequestException=Exception)
    bot.make_session = no_network
    # الـ Worker وهمي: المتابعة والمكتوم والبلاغات (خاص، في D1) بيجوا من هنا، مش من الريبو
    bot.private_data = {"watch": {}, "muted_asins": [], "watch_state": {}, "reports": {}}
    bot.load_private = lambda: bot.private_data
    bot.save_watch_state = lambda states: True
    return bot


def at(bot, y, m, d, hh, mm=30):
    return datetime(y, m, d, hh, mm, tzinfo=bot.CAIRO)


class BotTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bot = load_bot(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def items(self, category="headphones"):
        out = self.bot.parse_results(fx("search.html"), category)
        for i in out:
            self.bot.apply_effective(i)
        return {i["asin"]: i for i in out}

    def run_main(self, session, when, sends=None):
        """تشغيلة كاملة offline: الـ session صناعية، وكانبكام محجوب زي Actions، وتليجرام بيتسجل بس"""
        b = self.bot
        b.make_session = lambda: session
        b.now_cairo = lambda: when
        b.Kanbkam.check = lambda self, item: None
        b.TOKEN, b.CHAT_ID = "test-token", "me"
        log = sends if sends is not None else []
        b.send_text = lambda chat, text, loud=False, buttons=None: log.append(("text", chat, text, buttons)) or True
        b.deliver = lambda deal, channel_min, loud=False: log.append(("deal", deal["asin"], loud)) or True
        b.send_deal = lambda chat, deal, loud=False: (log.append(("send_deal", chat, deal["asin"], deal["caption"]))
                                                      or {"message_id": 900 + len(log)})
        with contextlib.redirect_stdout(io.StringIO()):  # لوج البوت مش مهم هنا
            b.main()
        return json.loads(b.STATE_FILE.read_text(encoding="utf-8"))


# ======================= البحث =======================
class TestSearchParsing(BotTestCase):
    def test_cards_and_fields(self):
        it = self.items()
        self.assertNotIn("B0TEST0008", it, "card without a price is skipped")
        a = it["B0TEST0001"]
        self.assertEqual((a["price"], a["old"], a["rating"], a["reviews"]), (1499.0, 3999.0, 4.6, 2345))
        self.assertEqual(a["eff_discount"], 63)
        self.assertEqual(a["image"], "https://img.example.test/fake/B0TEST0001._AC_SL1000_.jpg")
        self.assertEqual(a["url"], "https://www.amazon.eg/dp/B0TEST0001")
        self.assertEqual(a["category"], "headphones")

    def test_coupons(self):
        it = self.items()
        self.assertEqual(it["B0TEST0004"]["coupon_price"], 850.0)
        self.assertEqual(it["B0TEST0004"]["eff_discount"], 55)
        p = it["B0TEST0005"]  # مفيش سعر قديم، الخصم كله من الكوبون
        self.assertEqual((p["old"], p["coupon_price"], p["eff_discount"]), (2000.0, 900.0, 55))
        self.assertIsNone(it["B0TEST0009"]["coupon_price"], "bank promo is not a coupon")

    def test_coupon_text_variants(self):
        pc = self.bot.parse_coupon
        self.assertEqual(pc("You pay EGP 1,708.14 with coupon", 1743), 1708.14)
        self.assertEqual(pc("Save 10% with coupon", 1000), 900)
        self.assertEqual(pc("EGP 34.86 off coupon applied", 1743), 1708.14)
        self.assertEqual(pc("Save EGP 50 with coupon", 500), 450)
        self.assertIsNone(pc("20% off Fake Bank Cards", 1000))
        self.assertIsNone(pc("Save 20% promo code: FAKE20", 1000))
        self.assertIsNone(pc("You pay EGP 2,000 with coupon", 1000), "coupon above price is ignored")

    def test_titles(self):
        it = self.items()
        self.assertEqual(it["B0TEST0006"]["title"], "Orbita mens Runner 2.0 Sneaker", "brand added when missing")
        self.assertEqual(it["B0TEST0007"]["title"], "Velora Women Classic Leather Handbag Brown",
                         "brand not duplicated")
        self.assertEqual(it["B0TEST0013"]["title"], "Zentro")

    def test_arabic_digits(self):
        a = self.items()["B0TEST0014"]
        self.assertEqual((a["price"], a["old"]), (550.0, 1300.0))

    def test_is_deal(self):
        it = self.items()
        self.assertTrue(self.bot.is_deal(it["B0TEST0001"]))
        self.assertFalse(self.bot.is_deal(it["B0TEST0003"]), "10% is below MIN_DISCOUNT")
        self.assertFalse(self.bot.is_deal(it["B0TEST0015"]), "rating below MIN_RATING")


# ======================= صفحة المنتج =======================
class TestProductPage(BotTestCase):
    def details(self, name):
        return self.bot.fetch_details(types.SimpleNamespace(get=lambda url, **kw: Resp(fx(name))), "B0TEST0001")

    def test_sellers_and_stock(self):
        a, f, t = self.details("product_amazon.html"), self.details("product_fba.html"), self.details("product_third.html")
        self.assertEqual((a["seller"], a["in_stock"]), ("amazon", True))
        self.assertEqual((f["seller"], f["in_stock"]), ("fba", True))
        self.assertEqual((t["seller"], t["in_stock"]), ("third", False))

    def test_coupon_from_product_page(self):
        self.assertEqual(self.bot.parse_coupon(self.details("product_amazon.html")["coupon_text"], 1000), 950)
        self.assertIsNone(self.bot.parse_coupon(self.details("product_fba.html")["coupon_text"], 1000))

    def test_challenge_raises(self):
        with self.assertRaises(self.bot.AmazonBlocked):
            self.details("challenge.html")


# ======================= كانبكام =======================
class TestKanbkam(BotTestCase):
    def test_parse(self):
        kb = self.bot.parse_kanbkam(fx("kanbkam_product.html"), "B0TEST0001")
        self.assertEqual((kb["lowest"], kb["highest"]), (1450.0, 4200.0))
        self.assertEqual([o["store"] for o in kb["others"]], ["noon", "jumia"], "amazon rows are not 'other stores'")

    def test_wrong_product_or_home(self):
        self.assertIsNone(self.bot.parse_kanbkam(fx("kanbkam_product.html"), "B0TEST0002"))
        self.assertIsNone(self.bot.parse_kanbkam(fx("kanbkam_home.html"), "B0TEST0001"))

    def test_cheaper_elsewhere(self):
        kb = self.bot.parse_kanbkam(fx("kanbkam_product.html"), "B0TEST0001")
        best = self.bot.cheaper_elsewhere(self.items()["B0TEST0001"], kb)
        self.assertEqual((best["store"], best["price"]), ("noon", 1300.0), "different product (jumia) ignored")
        self.assertIsNone(self.bot.cheaper_elsewhere(self.items()["B0TEST0001"], None))

    def test_same_product(self):
        sp = self.bot.same_product
        self.assertTrue(sp("Nebula Wireless Headphones X1 Black", "Nebula Wireless Headphones X1 - Black"))
        self.assertFalse(sp("Nebula Headphones X100 Black", "Nebula Headphones X200 Black"), "different model")

    def test_circuit_breaker(self):
        calls = []

        def forbidden(url, **kw):
            calls.append(url)
            return Resp("", 403)

        def timeout(url, **kw):
            calls.append(url)
            raise TimeoutError("simulated")

        item = {"asin": "B0TEST0001", "title": "Nebula"}
        for getter in (forbidden, timeout):
            calls.clear()
            k = self.bot.Kanbkam(types.SimpleNamespace(get=getter))
            self.assertIsNone(k.check(item))
            self.assertIsNone(k.check(item))
            self.assertEqual(len(calls), 1, f"{getter.__name__}: no more requests after the first failure")


# ======================= السكور =======================
class TestScoring(BotTestCase):
    def test_reference_sources(self):
        b, base = self.bot, {"eff": 1000, "eff_discount": 60}
        self.assertEqual(b.price_reference(base, {"lowest": 1000, "highest": 3000}, None, []),
                         {"atl": 1000, "real": 67, "source": "kanbkam"})
        r = b.price_reference(base, None, {"lowest": 1050, "at": "2026-10-01"}, [["2026-10-01T10:00+03:00", 2500.0]])
        self.assertEqual(r, {"atl": 1050, "real": 60, "source": "bot"})
        self.assertEqual(b.price_reference(base, None, None, []), {"atl": None, "real": 60, "source": None})

    def test_score_order_and_labels(self):
        b = self.bot
        base = {"eff": 1000, "eff_discount": 60, "rating": 4.6, "reviews": 1200}
        kb = b.deal_score(base, b.price_reference(base, {"lowest": 1000, "highest": 3000}, None, []), "amazon")
        bot_hist = b.deal_score(base, b.price_reference(base, None, {"lowest": 1050, "at": None},
                                                         [["2026-10-01T10:00+03:00", 2500.0]]), "amazon")
        unverified = b.deal_score(base, b.price_reference(base, None, None, []), "amazon")
        self.assertGreater(kb, bot_hist)
        self.assertGreater(bot_hist, unverified)
        self.assertTrue(0 <= unverified <= 100)
        self.assertEqual([b.score_label(s) for s in (80, 60, 30)], ["🔥 أسطورية", "👍 كويسة", "😐 عادية"])

    def test_glitch_rules(self):
        g = self.bot.is_glitch
        self.assertTrue(g({"eff": 100}, {"atl": 100, "real": 85, "source": "kanbkam"}))
        self.assertFalse(g({"eff": 120}, {"atl": 100, "real": 85, "source": "kanbkam"}), "above the low")
        self.assertFalse(g({"eff": 100}, {"atl": 100, "real": 70, "source": None}), "below 80%")
        self.assertFalse(g({"eff": 185}, {"atl": None, "real": 85, "source": None}), "never seen before")


# ======================= التاريخ والقناة =======================
class TestHistoryAndChannel(BotTestCase):
    def test_history_cap_and_dedupe(self):
        h = {}
        for d in range(1, 120):
            self.bot.record_price(h, "A", 100 + d, f"2026-06-{(d % 28) + 1:02d}T10:00+03:00")
        self.assertEqual(len(h["A"]), self.bot.HISTORY_CAP)
        n = len(h["A"])
        self.bot.record_price(h, "A", h["A"][-1][1], h["A"][-1][0])
        self.assertEqual(len(h["A"]), n, "same price same day adds nothing")

    def test_adaptive_threshold(self):
        b = self.bot
        hist = {"A": [["2026-10-23T00:56+03:00", 100.0]], "B": []}
        self.assertEqual(b.channel_threshold({}, at(b, 2026, 10, 23, 9)), (50, 0))
        self.assertEqual(b.channel_threshold(hist, at(b, 2026, 11, 5, 9)), (50, 13))
        self.assertEqual(b.channel_threshold(hist, at(b, 2026, 11, 6, 9)), (60, 14))

    def test_channel_routing(self):
        b = self.bot
        b.TOKEN, b.CHAT_ID, b.CHANNEL_ID = "test-token", "me", "@chan"
        sent = []
        b.send_deal = lambda chat, deal, loud=False: sent.append(chat) or True
        cases = [({"score": 55, "title": "Nebula X1 Headphones"}, 50, ["me", "@chan"]),
                 ({"score": 55, "title": "Nebula X1 Headphones"}, 60, ["me"]),
                 ({"score": 90, "title": "Zentro"}, 50, ["me"])]
        for deal, threshold, expected in cases:
            sent.clear()
            b.deliver({**deal, "caption": ""}, threshold)
            self.assertEqual(sent, expected, (deal, threshold))


# ======================= الرسايل =======================
class TestMessages(BotTestCase):
    def test_caption_fallbacks(self):
        b = self.bot
        item = self.items()["B0TEST0001"]
        ref = {"atl": None, "real": 63, "source": None}
        no_data = b.format_msg(item, None, None, ref, 50, None, False, False)
        self.assertIn("⚠️ مفيش بيانات من كانبكام", no_data)
        b.now_cairo = lambda: at(b, 2026, 10, 23, 12)
        hist = b.format_msg(item, None, {"lowest": 1450.0, "at": "2026-10-11"}, ref, 50, "fba", False, False)
        self.assertIn("📊 من تاريخ البوت: أقل سعر 1,450 ج.م — اتسجل من 12 يوم", hist)
        self.assertNotIn("⚠️ مفيش بيانات", hist)
        self.assertIn("🏪 البايع: شحن أمازون (FBA)", hist)

    def test_caption_with_kanbkam_and_glitch(self):
        b = self.bot
        item = self.items()["B0TEST0001"]
        kb = b.parse_kanbkam(fx("kanbkam_product.html"), "B0TEST0001")
        ref = b.price_reference(item, kb, None, [])
        cap = b.format_msg(item, kb, None, ref, b.deal_score(item, ref, "amazon"), "amazon", True, True)
        self.assertTrue(cap.startswith("🚨"))
        self.assertIn("⚡ أرخص في نون بـ 199 جنيه", cap)
        self.assertLess(len(cap), 1024, "Telegram caption limit")

    def test_welcome_message(self):
        w = (ROOT / "messages" / "welcome.html").read_text(encoding="utf-8")
        self.assertLess(len(w), 4096)
        self.assertEqual(w.count("<b>"), w.count("</b>"))
        self.assertLess(w.index("القناة دي مستقلة ومش تابعة لأمازون بأي شكل."), w.index("شارك القناة"))


# ======================= تشغيلة كاملة =======================
class TestFullRun(BotTestCase):
    def setUp(self):
        super().setUp()
        self.bot.MIN_SCORE = 0

    def test_night_queue_morning_digest_and_dashboard(self):
        b, sends = self.bot, []
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 23, 1), sends)
        queue = st["_meta"]["queue"]
        self.assertTrue(queue, "night deals are queued")
        self.assertFalse([s for s in sends if s[0] == "deal"], "nothing sent at night")
        self.assertTrue(all("queued_price" in st[a] for a in queue))
        hist = json.loads(b.HISTORY_FILE.read_text(encoding="utf-8"))
        self.assertIn("B0TEST0001", hist)
        dash = json.loads((b.DOCS_DIR / "deals.json").read_text(encoding="utf-8"))
        asins = {d["asin"] for d in dash["deals"]}
        self.assertNotIn("B0TEST0013", asins, "single-word titles stay off the public dashboard")
        self.assertTrue(all(d["category"] == "صوتيات وساعات وشواحن" or d["category"] in b.SEARCH_GROUPS
                            for d in dash["deals"]))
        dh = json.loads((b.DOCS_DIR / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(set(dh), asins)

        sends.clear()
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 23, 9), sends)
        self.assertEqual(sends[0][0], "text")
        self.assertIn("🌅", sends[0][2], "morning digest goes out first")
        self.assertFalse(st["_meta"]["queue"])
        self.assertTrue(all("queued_price" not in st[a] for a in queue))

    def test_weekly_report_after_a_full_week(self):
        b, sends = self.bot, []
        self.run_main(FixtureSession(), at(b, 2026, 10, 23, 12), sends)
        self.assertFalse([s for s in sends if s[0] == "text" and "التقرير الأسبوعي" in s[2]], "week too short")
        sends.clear()
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 30, 11), sends)
        self.assertTrue([s for s in sends if s[0] == "text" and "التقرير الأسبوعي" in s[2]])
        self.assertEqual(st["_meta"]["week"]["start"], "2026-10-30")


# ======================= حماية أمازون =======================
class TestAmazonProtection(BotTestCase):
    def test_backoff_6_12_24_then_reset(self):
        b = self.bot
        t0 = at(b, 2026, 10, 23, 12, 0)  # بالنهار: حجب الليل ليه قاعدة لوحده (TestAmazonBudget)
        h = lambda hours: (t0 + timedelta(hours=hours)).isoformat(timespec="minutes")
        sends = []
        s = FixtureSession(challenge_after=0)
        st = self.run_main(s, t0, sends)
        self.assertEqual(st["_meta"]["amazon_backoff"]["level"], 1)
        self.assertEqual(st["_meta"]["amazon_backoff"]["until"], h(6))
        self.assertEqual(s.calls, 1, "stopped at the homepage")
        self.assertIn("6 ساعات", [x for x in sends if x[0] == "text"][-1][2])

        s = FixtureSession()
        st = self.run_main(s, t0 + timedelta(hours=3))
        self.assertEqual(s.calls, 0, "no Amazon requests during backoff")
        self.assertEqual(st["_meta"]["next"], 0, "rotation does not move during backoff")

        for when, level, until in ((9, 2, 21), (22, 3, 46), (47, 3, 71)):  # 21:00، 10:00، 11:00
            st = self.run_main(FixtureSession(challenge_after=0), t0 + timedelta(hours=when))
            self.assertEqual(st["_meta"]["amazon_backoff"]["level"], level)
            self.assertEqual(st["_meta"]["amazon_backoff"]["until"], h(until))

        s = FixtureSession()
        st = self.run_main(s, t0 + timedelta(hours=72))
        self.assertNotIn("amazon_backoff", st["_meta"], "clean run resets the backoff")
        self.assertEqual(st["_meta"]["next"], 15)
        self.assertLessEqual(s.calls, b.MAX_AMAZON_REQUESTS)

    def test_admin_notice_once_per_day(self):
        b, sends = self.bot, []
        t0 = at(b, 2026, 10, 23, 3, 0)
        self.run_main(FixtureSession(challenge_after=0), t0, sends)
        self.run_main(FixtureSession(challenge_after=0), t0 + timedelta(hours=7), sends)
        self.assertEqual(len([s for s in sends if s[0] == "text" and "تنبيه إداري" in s[2]]), 1)

    def test_product_pages_capped_at_8(self):
        b = self.bot
        b.MIN_DISCOUNT, b.MIN_RATING, b.MAX_ALERTS_PER_RUN = 1, 0, 100
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 10, 23, 12))
        self.assertEqual(s.kinds.count("dp"), 8)
        self.assertEqual(s.calls, 1 + 30 + 8)
        self.assertLessEqual(s.calls, b.MAX_AMAZON_REQUESTS)

    def test_hard_request_cap(self):
        b = self.bot
        b.MAX_AMAZON_REQUESTS = 10
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 12))
        self.assertEqual(s.calls, 10)
        self.assertEqual(st["_meta"]["next"], 4, "1 home + 9 pages = 4 finished keywords")
        self.assertNotIn("amazon_backoff", st["_meta"], "hitting the cap is not a block")

    def test_partial_block_keeps_progress(self):
        b = self.bot
        s = FixtureSession(challenge_after=6)
        st = self.run_main(s, at(b, 2026, 10, 23, 12))
        self.assertEqual(s.calls, 7, "no requests after the challenge")
        self.assertEqual(st["_meta"]["next"], 2, "rotation resumes at the blocked keyword")
        self.assertEqual(st["_meta"]["amazon_backoff"]["level"], 1)
        self.assertIn("B0TEST0001", json.loads(b.HISTORY_FILE.read_text(encoding="utf-8")))

    def test_product_page_challenge(self):
        b = self.bot
        b.MIN_DISCOUNT, b.MIN_RATING, b.MAX_ALERTS_PER_RUN = 1, 0, 100
        s = FixtureSession(challenge_after=33)
        st = self.run_main(s, at(b, 2026, 10, 23, 12))
        self.assertEqual(st["_meta"]["amazon_backoff"]["level"], 1)
        self.assertEqual(s.kinds.count("dp"), 3, "product pages stop at the first challenge")
        self.assertEqual(st["_meta"]["next"], 15, "searches were clean, rotation advances")

    def test_challenge_detection_no_retry(self):
        s = FixtureSession(challenge_after=0)
        with self.assertRaises(self.bot.AmazonBlocked):
            self.bot.fetch(s, "https://www.amazon.eg/s?k=test")
        self.assertEqual(s.calls, 1)


# ======================= 🧮 ميزانية أمازون (كل أنواع التشغيلات) =======================
class TestAmazonBudget(BotTestCase):
    def usage(self, st):
        return st["_meta"]["amazon_usage"]

    def seed(self, meta):
        self.bot.STATE_FILE.write_text(json.dumps({"_meta": meta}), encoding="utf-8")

    def test_every_request_is_counted_per_cairo_day_and_rolling_hour(self):
        b = self.bot
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 12, 0))
        u = self.usage(st)
        self.assertEqual((u["day"], u["count"]), ("2026-10-23", s.calls))
        self.assertEqual(sum(n for _, n in u["recent"]), s.calls)
        # بعد ساعة وشوية: الساعة اتفضّت، واليوم لسه بيعد
        s2 = FixtureSession()
        st = self.run_main(s2, at(b, 2026, 10, 23, 15, 5))
        self.assertEqual(self.usage(st)["count"], s.calls + s2.calls)
        self.assertEqual(sum(n for _, n in self.usage(st)["recent"]), s2.calls)
        # يوم القاهرة الجديد بيبدأ من صفر
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 24, 9, 0))
        self.assertEqual(self.usage(st)["day"], "2026-10-24")
        self.assertLessEqual(self.usage(st)["count"], 40)

    def test_daily_cap_trims_then_stops_without_a_false_alarm(self):
        b = self.bot
        self.seed({"amazon_usage": {"day": "2026-10-23", "count": 250, "recent": []}})
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 20, 0))
        self.assertEqual(s.calls, 10, "260 - 250 = 10 left today")
        self.assertEqual(self.usage(st)["count"], 260)
        sends = []
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 23, 30), sends)
        self.assertEqual(s.calls, 0, "daily cap reached: no Amazon at all")
        self.assertFalse([x for x in sends if x[0] == "text" and "ملقتش" in x[2]], "a spent budget is not 'found nothing'")
        self.assertEqual(st["_meta"]["last_full_run"], "2026-10-23T20:00+03:00", "a skipped run is not a full run")

    def test_hourly_cap_counts_runs_of_every_type(self):
        b = self.bot
        # تشغيلة تانية (مثلًا /run أو GitHub) عملت 35 طلب من نص ساعة
        self.seed({"amazon_usage": {"day": "2026-10-23", "count": 35, "recent": [["2026-10-23T11:40+03:00", 35]]}})
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 10, 23, 12, 0))
        self.assertEqual(s.calls, 5, "40 per rolling hour across all runs")
        s = FixtureSession()
        self.seed({"amazon_usage": {"day": "2026-10-23", "count": 35, "recent": [["2026-10-23T10:55+03:00", 35]]}})
        self.run_main(s, at(b, 2026, 10, 23, 12, 0))
        self.assertGreater(s.calls, 5, "older than an hour: the full run cap applies again")
        self.assertLessEqual(s.calls, 40)

    def test_manual_run_uses_the_same_caps(self):
        b = self.bot
        b.BOT_TRIGGER = "manual"
        self.seed({"amazon_usage": {"day": "2026-10-23", "count": 255, "recent": []}})
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 10, 23, 16, 0))
        self.assertEqual(s.calls, 5)

    def test_requests_are_paced_5_to_10_seconds(self):
        b = self.bot
        waits = []
        b.time = types.SimpleNamespace(sleep=waits.append)
        s = FixtureSession()
        for _ in range(3):
            b.amazon_get(s, "https://www.amazon.eg/")
        self.assertEqual(len(waits), 2, "no wait before the first request")
        self.assertTrue(all(5 <= w <= 10 for w in waits), waits)

    def test_night_run_is_light(self):
        b = self.bot
        b.MIN_DISCOUNT, b.MIN_RATING, b.MAX_ALERTS_PER_RUN = 1, 0, 100
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 3, 5))
        self.assertEqual(s.kinds.count("search"), 10, "10 keywords, page 1 only")
        self.assertEqual(s.kinds.count("dp"), 4)
        self.assertLessEqual(s.calls, b.NIGHT_RUN_CAP)
        self.assertEqual(st["_meta"]["next"], 10)

    def test_night_block_pauses_night_runs_for_7_days_then_retries(self):
        b = self.bot
        sends = []
        st = self.run_main(FixtureSession(challenge_after=0), at(b, 2026, 10, 24, 0, 5), sends)
        self.assertEqual(st["_meta"]["night_pause"]["until"], "2026-10-31T00:05+02:00")
        self.assertIn("مفيش تشغيلات بالليل", [x for x in sends if x[0] == "text"][-1][2])
        # الليلة الجاية (فترة الراحة خلصت): مفيش أمازون بالليل
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 25, 3, 5))
        self.assertEqual(s.calls, 0, "night runs are paused")
        # بالنهار عادي
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 25, 11, 5))
        self.assertGreater(s.calls, 0, "day runs continue")
        self.assertIn("night_pause", st["_meta"])
        # بعد 7 أيام: بنجرّب بالليل تاني
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 31, 3, 5))
        self.assertGreater(s.calls, 0, "night runs try again after 7 days")
        self.assertNotIn("night_pause", st["_meta"])

    def test_day_block_does_not_pause_night_runs(self):
        st = self.run_main(FixtureSession(challenge_after=0), at(self.bot, 2026, 10, 23, 14, 5))
        self.assertNotIn("night_pause", st["_meta"])

    def test_github_schedule_is_only_a_fallback(self):
        b = self.bot
        b.BOT_TRIGGER = "schedule"
        when = at(b, 2026, 10, 23, 12, 0)
        b.private_data["cron_last"] = "2026-10-23T08:30:00Z"  # 11:30 القاهرة: الـ Worker شغّال
        s = FixtureSession()
        st = self.run_main(s, when)
        self.assertEqual(s.calls, 0, "the Worker cron ticked 30 minutes ago")
        self.assertNotIn("last_full_run", st["_meta"])
        b.private_data["cron_last"] = "2026-10-23T04:30:00Z"  # 07:30: من 4 ساعات ونص
        s = FixtureSession()
        self.run_main(s, when)
        self.assertGreater(s.calls, 0, "Worker cron silent for 4+ hours: GitHub takes over")
        b.load_private = lambda: None  # الـ Worker نفسه مبيردش
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 10, 23, 15, 30))
        self.assertGreater(s.calls, 0)

    def test_run_plan_priority(self):
        b = self.bot
        self.assertEqual(b.run_plan("event_day", 25, 0, 0),
                         {"pages": 1, "home": 1, "watch": 0, "reports": 0, "first": 10, "detail": 4, "rotation": 10})
        self.assertEqual(b.run_plan("event_day", 25, 9, 3),
                         {"pages": 1, "home": 1, "watch": 5, "reports": 2, "first": 10, "detail": 4, "rotation": 3})
        self.assertEqual(b.run_plan("event_day", 8, 9, 3),
                         {"pages": 1, "home": 1, "watch": 5, "reports": 2, "first": 0, "detail": 0, "rotation": 0},
                         "a small budget goes to the watchlist first")
        self.assertEqual(b.run_plan("event_night", 10, 0, 0),
                         {"pages": 1, "home": 1, "watch": 0, "reports": 0, "first": 9, "detail": 0, "rotation": 0})
        self.assertEqual(b.run_plan("night", 20, 0, 0),
                         {"pages": 1, "home": 1, "watch": 0, "reports": 0, "first": 10, "detail": 4, "rotation": 0})

    def test_white_friday_hourly_run_follows_the_priority(self):
        b = self.bot
        b.MIN_DISCOUNT, b.MIN_RATING, b.MAX_ALERTS_PER_RUN = 1, 0, 100
        b.CONFIG_FILE.write_text(json.dumps({"white_friday": {"start": "2026-11-20", "end": "2026-11-30"}}), encoding="utf-8")
        b.private_data["watch"] = {f"B0WATCH{i:03d}": {"target": 1} for i in range(7)}
        b.private_data["reports"] = {"r1": {"asin": "B0REPORT01", "at": "2026-11-25T08:00:00Z"}}
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 11, 25, 14, 5))
        self.assertLessEqual(s.calls, 25)
        watch_pages = [a for a in s.dp_asins if a.startswith("B0WATCH")]
        self.assertEqual(len(watch_pages), 5, "watchlist first (5 product pages)")
        self.assertIn("B0REPORT01", s.dp_asins, "then the reports")
        self.assertEqual(s.kinds.count("search"), 25 - 1 - 5 - 1 - 4, "10 top-category keywords + rotation, page 1")
        self.assertEqual(self.usage(st)["count"], s.calls)
        # الساعة اللي بعدها: ساعة كفاية بين البحثين في الموسم
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 11, 25, 15, 5))
        self.assertGreater(s.calls, 0)
        self.assertLessEqual(s.calls, 25)

    def test_white_friday_night_and_daily_cap(self):
        b = self.bot
        b.CONFIG_FILE.write_text(json.dumps({"white_friday": {"start": "2026-11-20", "end": "2026-11-30"}}), encoding="utf-8")
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 11, 25, 1, 5))
        self.assertLessEqual(s.calls, 10)
        self.seed({"amazon_usage": {"day": "2026-11-25", "count": 410, "recent": []}})
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 11, 25, 22, 5))
        self.assertEqual(s.calls, 10, "420 a day in season")

    def test_budget_numbers_match_the_worker(self):
        js = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
        for name, value in (("HOURLY_CAP", self.bot.HOURLY_CAP), ("DAILY_CAP", self.bot.DAILY_CAP),
                            ("EVENT_DAILY_CAP", self.bot.EVENT_DAILY_CAP)):
            self.assertRegex(js, rf"const {name} = {value};", name)


# ======================= المرحلة 3: config.json والمتابعة =======================
class TestConfigAndControl(BotTestCase):
    def config(self, **cfg):
        priv = self.bot.private_data
        if "watch" in cfg:
            priv["watch"] = cfg.pop("watch")
        if "muted_asins" in cfg:
            priv["muted_asins"] = cfg.pop("muted_asins")
        self.bot.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

    def test_commands_are_never_search_keywords(self):
        b = self.bot
        with contextlib.redirect_stdout(io.StringIO()):
            b.apply_config({"extra_keywords": ["/menu", " /start ", "desk fan"]})
        self.assertIn("desk fan", b.SEARCHES)
        self.assertFalse([k for k in b.SEARCHES if k.startswith("/")])

    def test_season_defaults_match_the_worker_and_config(self):
        js = (ROOT / "worker" / "src" / "index.js").read_text(encoding="utf-8")
        block = re.search(r"export const WF_DEFAULT = (\{[^}]*\});", js).group(1)
        worker_wf = json.loads(re.sub(r"(\w+):", r'"\1":', block))
        self.assertEqual(worker_wf, self.bot.WHITE_FRIDAY_DEFAULT, "bot and Worker must agree on the default dates")
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["white_friday"], self.bot.WHITE_FRIDAY_DEFAULT)
        self.assertTrue(cfg["white_friday"]["estimated"], "dates stay marked estimated until the owner confirms them")
        self.assertEqual(cfg["mini_events"], self.bot.MINI_EVENTS_DEFAULT)

    def test_apply_config_overrides(self):
        b = self.bot
        default = list(b.SEARCHES)
        with contextlib.redirect_stdout(io.StringIO()):
            b.apply_config({"min_discount": 40, "min_score": 55, "extra_keywords": ["Air Purifier", "air purifier"],
                            "removed_keywords": ["smartphone"]})
        self.assertEqual((b.MIN_DISCOUNT, b.MIN_SCORE), (40, 55))
        self.assertNotIn("smartphone", b.SEARCHES)
        self.assertEqual(b.SEARCHES[-1], "air purifier", "added once, lowercased, at the end")
        self.assertEqual(len(b.SEARCHES), len(default))
        self.assertEqual(b.CATEGORY_OF["air purifier"], "إضافات")

    def test_apply_config_rejects_bad_values(self):
        b = self.bot
        before = (b.MIN_DISCOUNT, b.MIN_SCORE, list(b.SEARCHES))
        with contextlib.redirect_stdout(io.StringIO()):
            b.apply_config({"min_discount": 0, "min_score": "high", "removed_keywords": list(b.SEARCHES)})
        self.assertEqual((b.MIN_DISCOUNT, b.MIN_SCORE, list(b.SEARCHES)), before, "nothing usable -> defaults")

    def test_mute_private_only(self):
        b, log = self.bot, []
        b.MIN_SCORE, b.CHANNEL_ID = 0, "@chan"
        self.config(mute_until="2026-10-23T14:00:00+03:00")
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 23, 12), log)
        self.assertFalse([x for x in log if x[0] == "deal"], "private gets nothing while muted")
        chan = [x for x in log if x[0] == "send_deal" and x[1] == "@chan"]
        self.assertTrue(chan, "channel keeps posting during a mute")
        self.assertTrue(all(d["queued_for"] == "mute" for d in st["_meta"]["queue"].values()))
        log.clear()
        self.run_main(FixtureSession(), at(b, 2026, 10, 23, 15), log)  # الكتم خلص (والتشغيلة قبلها من 3 ساعات)
        digests = [x for x in log if x[0] == "text" and x[1] == "me"]
        self.assertTrue(digests and "🔔" in digests[0][2], "private digest after the mute")
        self.assertFalse([x for x in log if x[0] == "text" and x[1] == "@chan"], "channel already had them")

    def test_muted_asin_is_skipped(self):
        b, log = self.bot, []
        b.MIN_SCORE = 0
        self.config(muted_asins=["B0TEST0001"])
        self.run_main(FixtureSession(), at(b, 2026, 10, 23, 12), log)
        sent = {x[1] for x in log if x[0] == "deal"}
        self.assertTrue(sent)
        self.assertNotIn("B0TEST0001", sent)

    def test_full_scan_every_3_hours_outside_events(self):
        b = self.bot
        b.CONFIG_FILE.write_text(json.dumps({"white_friday": {"mode": "off"}}), encoding="utf-8")
        s1 = FixtureSession()
        self.run_main(s1, at(b, 2026, 10, 23, 12, 0))
        s2 = FixtureSession()
        st = self.run_main(s2, at(b, 2026, 10, 23, 14, 54))  # 3 ساعات إلا 6 دقايق
        self.assertGreater(s1.calls, 0)
        self.assertEqual(s2.calls, 0, "hourly Worker runs in between make no Amazon requests")
        self.assertEqual(st["_meta"]["last_full_run"], "2026-10-23T12:00+03:00")
        s3 = FixtureSession()
        self.run_main(s3, at(b, 2026, 10, 23, 14, 55))  # جوه سماحية الـ 5 دقايق
        self.assertGreater(s3.calls, 0, "3h (minus 5 min tolerance) later is a full run again")

    def test_watchlist_alerts(self):
        b, log = self.bot, []
        self.config(watch={
            "B0TEST0002": {"target": 2000},  # في نتايج البحث بـ 1800: تنبيه من غير صفحة منتج
            "B0TEST0050": {"target": 3000},  # صفحة منتج (fba) بـ 2750: تنبيه
            "B0TEST0099": {"target": 100},   # خلصان: مفيش تنبيه
        })
        s = FixtureSession(products={"B0TEST0099": "product_third.html"})
        st = self.run_main(s, at(b, 2026, 10, 23, 12), log)
        alerts = [x[2] for x in log if x[0] == "text" and "👀" in x[2]]
        self.assertEqual(len(alerts), 2, alerts)
        self.assertTrue(any("1,800" in a and "🎯" in a for a in alerts))
        self.assertTrue(any("2,750" in a and "Quanta" in a for a in alerts))
        # المتابعة بتتفحص قبل المرشحين: أول صفحتين بس ليها، و0002 اتلقى في البحث فمتفتحتش صفحته
        self.assertEqual(s.dp_asins[:2], ["B0TEST0050", "B0TEST0099"])
        self.assertFalse(self.bot.private_data["watch_state"]["B0TEST0099"]["in_stock"])
        self.assertNotIn("watch_state", st["_meta"], "watch prices stay out of the public state")

        log.clear()  # بعد 3 ساعات: اللمبة رجعت المخزن، والباقي نفس السعر
        s = FixtureSession(products={"B0TEST0099": "product_amazon.html"})
        self.run_main(s, at(b, 2026, 10, 23, 15), log)
        alerts = [x[2] for x in log if x[0] == "text" and "👀" in x[2]]
        self.assertEqual(len(alerts), 1, "same prices do not alert again")
        self.assertIn("📦", alerts[0])

    def test_watchlist_cap_and_unwatch_cleanup(self):
        b = self.bot
        self.config(watch={f"B0WATCH{i:03d}": {"target": 1} for i in range(8)})
        s = FixtureSession()
        st = self.run_main(s, at(b, 2026, 10, 23, 12))
        watch_pages = [a for a in s.dp_asins if a.startswith("B0WATCH")]
        self.assertEqual(len(watch_pages), b.MAX_WATCH_CHECKS)
        self.assertLessEqual(s.calls, b.MAX_AMAZON_REQUESTS)
        self.config(watch={"B0WATCH000": {"target": 1}})
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 23, 15))
        self.assertEqual(set(self.bot.private_data["watch_state"]), {"B0WATCH000"}, "unwatched items are forgotten")

    def test_disabled_categories(self):
        b = self.bot
        with contextlib.redirect_stdout(io.StringIO()):
            b.apply_config({"disabled_categories": ["رياضة", "قسم مش موجود"]})
        self.assertNotIn("dumbbells", b.SEARCHES)
        self.assertNotIn("treadmill", b.SEARCHES)
        self.assertIn("smartphone", b.SEARCHES)

    def test_watch_stock_only(self):
        b, log = self.bot, []
        self.config(watch={"B0TEST0099": {"target": None, "stock_only": True}})
        self.run_main(FixtureSession(products={"B0TEST0099": "product_third.html"}), at(b, 2026, 10, 23, 12), log)
        self.assertFalse([x for x in log if x[0] == "text" and "👀" in x[2]], "out of stock: nothing yet")
        log.clear()
        self.run_main(FixtureSession(products={"B0TEST0099": "product_amazon.html"}), at(b, 2026, 10, 23, 15), log)
        alerts = [x[2] for x in log if x[0] == "text" and "👀" in x[2]]
        self.assertEqual(len(alerts), 1)
        self.assertIn("📦", alerts[0])
        self.assertNotIn("🎯", alerts[0], "no price target for stock-only watches")

    def test_private_lists_come_from_the_worker_and_state_goes_back(self):
        b, log, saved = self.bot, [], []
        b.save_watch_state = lambda states: saved.append(json.loads(json.dumps(states))) or True
        self.config(watch={"B0TEST0002": {"target": 2000}}, muted_asins=["B0TEST0001"])
        b.MIN_SCORE = 0
        st = self.run_main(FixtureSession(), at(b, 2026, 10, 23, 12), log)
        self.assertNotIn("B0TEST0001", {x[1] for x in log if x[0] == "deal"}, "muted list from the Worker")
        self.assertTrue(saved and saved[-1]["B0TEST0002"]["price"] == 1800.0, "watch prices sent back to the Worker")
        public = b.STATE_FILE.read_text(encoding="utf-8")
        self.assertNotIn("watch_state", public)
        self.assertFalse(json.loads(b.CONFIG_FILE.read_text(encoding="utf-8")).get("watch"))

    def test_worker_unreachable_alerts_once_and_runs_without_private_lists(self):
        b, log = self.bot, []
        b.load_private = lambda: None
        b.MIN_SCORE = 0
        self.run_main(FixtureSession(), at(b, 2026, 10, 23, 12), log)
        alerts = [x[2] for x in log if x[0] == "text" and "الـ Worker" in x[2]]
        self.assertEqual(len(alerts), 1)
        self.assertTrue([x for x in log if x[0] == "deal"], "deals still go out")
        self.run_main(FixtureSession(), at(b, 2026, 10, 23, 15, 30), log)
        self.assertEqual(len([x for x in log if x[0] == "text" and "الـ Worker" in x[2]]), 1, "once a day")

    def test_load_private_needs_the_shared_secret(self):
        spec = importlib.util.spec_from_file_location("raw_bot", ROOT / "amazon_eg_deals.py")
        raw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(raw)
        raw.BOT_SHARED_SECRET = ""
        raw.requests = types.SimpleNamespace(get=no_network, put=no_network, RequestException=Exception)
        self.assertIsNone(raw.load_private(), "no secret: never calls the Worker")
        self.assertFalse(raw.save_watch_state({}))
        seen = {}

        def fake_get(url, headers=None, timeout=None):
            seen.update(url=url, headers=headers)
            return JsonResp(200, {"watch": {"B0X": {"target": 1}}, "muted_asins": ["B0Y"], "watch_state": {}})
        raw.BOT_SHARED_SECRET = "s3cret"
        raw.requests = types.SimpleNamespace(get=fake_get, RequestException=Exception)
        self.assertEqual(raw.load_private()["muted_asins"], ["B0Y"])
        self.assertEqual(raw.load_private()["reports"], {}, "no reports from the Worker: an empty queue")
        self.assertEqual(seen["url"], "https://loata-bot.loata-bot-worker.workers.dev/bot/private")
        self.assertEqual(seen["headers"], {"Authorization": "Bearer s3cret"})

    def test_mute_button_only_in_private(self):
        b = self.bot
        b.TOKEN, b.CHAT_ID, b.CHANNEL_ID = "test-token", "me", "@chan"
        calls = []
        b.tg_call = lambda method, payload: calls.append(payload) or True
        deal = {"asin": "B0TEST0001", "caption": "x", "photo": "p", "buttons": [{"text": "a", "url": "u"}]}
        b.send_deal("me", deal)
        b.send_deal("@chan", deal)
        private, channel = calls[0]["reply_markup"]["inline_keyboard"], calls[1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(private[1][0]["callback_data"], "mute:B0TEST0001")
        self.assertFalse([b for row in channel for b in row if "callback_data" in b], "no owner buttons in the channel")
        self.assertEqual(channel[-1][0], {"text": "🚨 بلّغ عن لقطة", "url": "https://t.me/loatamazonbot?start=report"})
        self.assertFalse([b for row in private for b in row if b.get("text") == "🚨 بلّغ عن لقطة"], "not in private")


# ======================= 🚨 بلاغات المتابعين =======================
class TestReports(BotTestCase):
    """البلاغات: D1 (عن طريق الـ Worker) ← التشغيلة بتفحص وتنشر أو تبعتلك مراجعة"""

    def setUp(self):
        super().setUp()
        self.bot.CHANNEL_ID = "@chan"
        self.now = at(self.bot, 2026, 10, 23, 12)
        # تاريخ أسعار عمره 20 يوم: حد القناة 60، فالنشر التلقائي محتاج 70
        hist = {"B0TEST0001": [["2026-10-03T10:00+03:00", 3000.0], ["2026-10-15T10:00+03:00", 2600.0]]}
        self.bot.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.bot.HISTORY_FILE.write_text(json.dumps(hist), encoding="utf-8")
        self.bot.STATE_FILE.write_text(json.dumps({"_meta": {}, "B0TEST0001": {"lowest": 1250.0, "lowest_at": "2026-10-20"}}),
                                       encoding="utf-8")

    def reports(self, **reps):
        self.bot.private_data["reports"] = json.loads(json.dumps(reps))

    def run_bot(self, session=None, when=None, log=None):
        log = [] if log is None else log
        st = self.run_main(session or FixtureSession(products=self.products), when or self.now, log)
        return st, log

    products = {"B0TEST0001": "product_amazon.html", "B0NEWNEW01": "product_fba.html", "B0TEST0060": "product_nodiscount.html",
                "B0TEST0061": "product_lowrated.html", "B0TEST0099": "product_third.html", "B0NOTPAGE1": "kanbkam_home.html"}

    def test_auto_publish_when_every_check_passes(self):
        self.reports(r1={"asin": "B0TEST0001", "at": "2026-10-23T08:00:00Z", "note": "<b>user text</b>"})
        st, log = self.run_bot()
        res = st["_meta"]["reports"]["r1"]
        self.assertEqual(res["status"], "published", res)
        posts = [x for x in log if x[0] == "send_deal" and x[1] == "@chan" and x[2] == "B0TEST0001"]
        self.assertEqual(len(posts), 1)
        caption = posts[0][3]
        self.assertIn("🙏 شكرًا لأحد المتابعين", caption)
        self.assertNotIn("user text", caption, "never reuse anything the user wrote")
        self.assertIn("https://www.amazon.eg/dp/B0TEST0001", next(  # اللينك مبني من الـ ASIN بس
            d for d in [self.bot.amazon_link("B0TEST0001")]))
        self.assertEqual(res["channel_message_id"], 900 + log.index(posts[0]) + 1)
        owner = [x for x in log if x[0] == "text" and "اتنشر بلاغ تلقائي" in x[2]]
        self.assertEqual(owner[0][3], [[{"text": "🗑️ احذف من القناة", "callback_data": "rd:r1"}]])
        self.assertTrue(st["B0TEST0001"]["notified_at"])

    def test_opt_in_credit_uses_username_only(self):
        self.reports(r1={"asin": "B0TEST0001", "at": "2026-10-23T08:00:00Z", "credit": "deal_hunter"})
        _, log = self.run_bot()
        self.assertIn("🙏 شكرًا لـ @deal_hunter", [x for x in log if x[0] == "send_deal" and x[1] == "@chan"][0][3])
        self.setUp()
        self.reports(r1={"asin": "B0TEST0001", "at": "2026-10-23T08:00:00Z", "credit": "<b>evil</b>"})
        _, log = self.run_bot()
        caption = [x for x in log if x[0] == "send_deal" and x[1] == "@chan"][0][3]
        self.assertIn("🙏 شكرًا لأحد المتابعين", caption)
        self.assertNotIn("evil", caption)

    def test_review_lists_exactly_the_failed_checks(self):
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z"})
        st, log = self.run_bot()
        res = st["_meta"]["reports"]["r2"]
        self.assertEqual(res["status"], "review")
        self.assertEqual(res["reasons"][0], "مفيش تاريخ أسعار")
        self.assertIn("التقييم 3.6 أقل من 4", res["reasons"])
        self.assertIn("البياع مش أمازون", res["reasons"])
        self.assertTrue(any(r.startswith("السكور ") and "أقل من المطلوب (70)" in r for r in res["reasons"]))
        self.assertFalse([x for x in log if x[0] == "send_deal" and x[1] == "@chan"], "nothing published")
        msg = [x for x in log if x[0] == "text" and "بلاغ محتاج رأيك" in x[2]][0]
        self.assertIn("450 ج.م بدل 1,500 ج.م (خصم 70%)", msg[2])
        self.assertIn("• البياع مش أمازون", msg[2])
        self.assertEqual([b["callback_data"] for b in msg[3][0]], ["rv:p:r2", "rv:x:r2", "rv:w:r2"])

    def test_broken_reports_are_dropped_silently(self):
        self.reports(a={"asin": "B0TEST0060", "at": "1"}, b={"asin": "B0TEST0099", "at": "2"},
                     c={"asin": "B0NOTPAGE1", "at": "3"}, d={"asin": "not-an-asin", "at": "4"})
        st, log = self.run_bot()
        res = st["_meta"]["reports"]
        self.assertEqual({k: v["reasons"][0] for k, v in res.items()},
                         {"a": "no_discount", "b": "out_of_stock", "c": "not_product"})
        self.assertFalse([x for x in log if x[0] == "text" and "بلاغ" in x[2]], "no review requests for broken reports")

    def test_autopublish_off_sends_everything_to_review(self):
        self.bot.CONFIG_FILE.write_text(json.dumps({"report_autopublish": False}), encoding="utf-8")
        self.reports(r1={"asin": "B0TEST0001", "at": "2026-10-23T08:00:00Z"})
        st, _ = self.run_bot()
        self.assertEqual(st["_meta"]["reports"]["r1"]["reasons"], ["النشر التلقائي للبلاغات مقفول"])

    def test_recently_posted_needs_review(self):
        st = json.loads(self.bot.STATE_FILE.read_text(encoding="utf-8"))
        st["B0TEST0001"]["notified_at"] = "2026-10-21T10:00+03:00"
        self.bot.STATE_FILE.write_text(json.dumps(st), encoding="utf-8")
        self.reports(r1={"asin": "B0TEST0001", "at": "2026-10-23T08:00:00Z"})
        st, _ = self.run_bot()
        self.assertEqual(st["_meta"]["reports"]["r1"]["reasons"], ["اتنشر في آخر 7 أيام"])

    def test_review_notices_capped_per_day_and_checks_per_run(self):
        b = self.bot
        b.MAX_REPORT_CHECKS, b.MAX_AMAZON_REQUESTS, b.HOURLY_CAP = 20, 100, 100  # هنا بنختبر حد الـ 10 رسايل بس
        self.reports(**{f"q{i:02d}": {"asin": "B0TEST0061", "at": f"2026-10-23T08:{i:02d}:00Z"} for i in range(12)})
        st, log = self.run_bot()
        res = st["_meta"]["reports"]
        self.assertEqual(sum(v["notified"] for v in res.values()), 10)
        self.assertEqual(len([x for x in log if x[0] == "text" and "بلاغ محتاج رأيك" in x[2]]), 10)
        self.setUp()
        b = self.bot
        b.MAX_REPORT_CHECKS = 5
        self.reports(**{f"q{i:02d}": {"asin": "B0TEST0061", "at": f"2026-10-23T08:{i:02d}:00Z"} for i in range(12)})
        s = FixtureSession(products=self.products)
        st, _ = self.run_bot(session=s)
        self.assertEqual(len(st["_meta"]["reports"]), 5)
        self.assertLessEqual(s.calls, b.MAX_AMAZON_REQUESTS)

    def test_decisions_wait_for_undo_window_then_apply(self):
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z"})
        self.run_bot()
        # دوست ✅ انشر من 5 دقايق: لسه في فترة التراجع
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z", "decision": "publish",
                         "decided_at": "2026-10-23T14:55:00+03:00"})
        st, log = self.run_bot(when=at(self.bot, 2026, 10, 23, 15, 0))
        self.assertEqual(st["_meta"]["reports"]["r2"]["status"], "review")
        # بعد 3 ساعات: اتنشر بعلامة "اتأكد بنفسك" (مكانش ليه تاريخ وقت البلاغ)
        st, log = self.run_bot(when=at(self.bot, 2026, 10, 23, 18, 0))
        self.assertEqual(st["_meta"]["reports"]["r2"]["status"], "published")
        post = [x for x in log if x[0] == "send_deal" and x[1] == "@chan" and x[2] == "B0TEST0061"]
        self.assertTrue(post and "🙏 شكرًا لأحد المتابعين" in post[0][3])

    def test_publish_decision_expires_if_price_went_up(self):
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z"})
        st, _ = self.run_bot()
        st["_meta"]["reports"]["r2"]["price"] = 300.0  # كان أرخص وقت المراجعة
        self.bot.STATE_FILE.write_text(json.dumps(st), encoding="utf-8")
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z", "decision": "publish",
                         "decided_at": "2026-10-23T12:00:00+03:00"})
        st, log = self.run_bot(when=at(self.bot, 2026, 10, 23, 15, 30))
        self.assertEqual(st["_meta"]["reports"]["r2"]["status"], "expired")
        self.assertTrue([x for x in log if x[0] == "text" and "منشرتش" in x[2] and "السعر زاد" in x[2]])

    def test_reject_and_watch_apply_without_amazon(self):
        self.reports(r2={"asin": "B0TEST0061", "at": "2026-10-23T08:00:00Z"}, r3={"asin": "B0TEST0061", "at": "2026-10-23T08:01:00Z"})
        self.run_bot()
        self.reports(r2={"asin": "B0TEST0061", "at": "1", "decision": "reject", "decided_at": "2026-10-23T12:10:00+03:00"},
                     r3={"asin": "B0TEST0061", "at": "2", "decision": "watch", "decided_at": "2026-10-23T12:10:00+03:00"})
        s = FixtureSession(products=self.products)
        st, _ = self.run_bot(session=s, when=at(self.bot, 2026, 10, 23, 13, 0))  # من ساعة: قاعدة الساعتين، مفيش أمازون
        self.assertEqual(s.calls, 0)
        self.assertEqual(st["_meta"]["reports"]["r2"]["status"], "rejected")
        self.assertEqual(st["_meta"]["reports"]["r3"]["status"], "watched")


# ======================= Creators API =======================
def fxj(name):
    return json.loads(fx(name))


class JsonResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeCreatorsHttp:
    """Creators API وهمي: بيرد بالـ fixtures اللي شكلها زي أمثلة الدوكس، وبيسجل كل طلب"""

    def __init__(self, token=(200, "creators_token.json"), search=None, getitems=None):
        self.calls = []
        self.token = token
        self.search = list(search or [(200, "creators_search.json")])
        self.getitems = list(getitems or [(200, "creators_getitems.json")])

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if url.endswith("/auth/o2/token"):
            status, name = self.token
        else:
            queue = self.search if url.endswith("/searchItems") else self.getitems
            status, name = queue.pop(0) if len(queue) > 1 else queue[0]
        return JsonResp(status, fxj(name))


class FakeClock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class TestCreatorsApi(BotTestCase):
    def client(self, http, meta=None):
        clock = FakeClock()
        c = self.bot.CreatorsClient("cred-id", "cred-secret", "testtag-21", meta if meta is not None else {},
                                    http=http, clock=clock, sleep=clock.sleep, today=lambda: "2026-10-23")
        return c, clock

    def test_token_request_and_caching_follow_the_docs(self):
        http = FakeCreatorsHttp()
        c, clock = self.client(http)
        for _ in range(3):
            c.search("headphones", 1)
        token_calls = [x for x in http.calls if x["url"].endswith("/token")]
        self.assertEqual(len(token_calls), 1, "token reused until it expires")
        t = token_calls[0]
        self.assertEqual(t["url"], "https://api.amazon.co.uk/auth/o2/token", "EG is in the EU region (version 3.2)")
        self.assertEqual(t["json"], {"grant_type": "client_credentials", "client_id": "cred-id",
                                     "client_secret": "cred-secret", "scope": "creatorsapi::default"})
        clock.t += 3600
        c.search("headphones", 1)
        self.assertEqual(len([x for x in http.calls if x["url"].endswith("/token")]), 2, "refreshed after expiry")

    def test_search_request_shape(self):
        http = FakeCreatorsHttp()
        c, _ = self.client(http)
        c.search("air fryer", 2)
        call = [x for x in http.calls if x["url"].endswith("/searchItems")][0]
        self.assertEqual(call["url"], "https://creatorsapi.amazon/catalog/v1/searchItems")
        self.assertEqual(call["headers"], {"Authorization": "Bearer Atc|TEST-ACCESS-TOKEN",
                                           "Content-Type": "application/json", "x-marketplace": "www.amazon.eg"})
        body = call["json"]
        self.assertEqual((body["keywords"], body["itemCount"], body["itemPage"]), ("air fryer", 10, 2))
        self.assertEqual((body["partnerTag"], body["marketplace"]), ("testtag-21", "www.amazon.eg"))
        self.assertIn("offersV2.listings.price", body["resources"])

    def test_mapping_into_the_scorer_item_shape(self):
        raw = fxj("creators_search.json")["searchResult"]["items"]
        items = [self.bot.creators_item(r, "headphones") for r in raw]
        a, b, none, oos = items
        self.assertEqual({k: a[k] for k in ("asin", "title", "price", "old", "seller", "in_stock", "rating_known")},
                         {"asin": "B0API00001", "title": "Nebula Wireless Headphones X1 Black", "price": 1499.0,
                          "old": 3999.0, "seller": "amazon", "in_stock": True, "rating_known": False})
        self.assertEqual(a["url"], "https://www.amazon.eg/dp/B0API00001?tag=testtag-21&linkCode=osi&th=1&psc=1",
                         "detailPageURL kept exactly (do not edit URL parameters)")
        self.assertEqual(a["image"], "https://img.example.test/fake/B0API00001._SL500_.jpg")
        self.assertEqual((b["price"], b["seller"]), (1800.0, None), "buy box listing; third-party seller unknown (no FBA flag)")
        self.assertIsNone(none, "no savingBasis = no discount = not a deal")
        self.assertFalse(oos["in_stock"])
        self.bot.apply_effective(a)
        self.assertTrue(self.bot.is_deal(a), "missing rating doesn't block API items")
        ref = {"atl": None, "real": a["eff_discount"], "source": None}
        self.assertEqual(self.bot.deal_score(a, ref, "amazon"),
                         round(min(a["eff_discount"], 70) / 70 * 40 * 0.6 + 10 + 12.5 + 10), "neutral rating/review points")

    def test_token_bucket_one_request_per_second(self):
        http = FakeCreatorsHttp()
        c, clock = self.client(http)
        for _ in range(5):
            c.search("x", 1)
        self.assertAlmostEqual(sum(clock.slept), 4.0, places=3, msg="5 calls at 1 TPS take 4 seconds of waiting")

    def test_daily_limit(self):
        http = FakeCreatorsHttp()
        meta = {"creators_usage": {"day": "2026-10-23", "count": self.bot.CREATORS_TPD}}
        c, _ = self.client(http, meta)
        with self.assertRaises(self.bot.CreatorsError):
            c.search("x", 1)
        self.assertFalse([x for x in http.calls if "searchItems" in x["url"]], "nothing sent past the daily limit")
        meta["creators_usage"]["day"] = "2026-10-22"
        c.search("x", 1)
        self.assertEqual(meta["creators_usage"], {"day": "2026-10-23", "count": 1}, "resets on a new Cairo day")

    def test_throttle_retry_and_no_results(self):
        http = FakeCreatorsHttp(search=[(429, "creators_error_429.json"), (200, "creators_search.json")])
        c, clock = self.client(http)
        self.assertEqual(len(c.search("x", 1)), 4)
        self.assertIn(1, clock.slept, "exponential backoff after 429")
        c2, _ = self.client(FakeCreatorsHttp(search=[(404, "creators_error_404.json")]))
        self.assertEqual(c2.search("Mystery Novels Non Existing", 1), [])

    def test_auth_errors(self):
        c, _ = self.client(FakeCreatorsHttp(search=[(401, "creators_error_401.json")]))
        with self.assertRaises(self.bot.CreatorsAuthError) as e:
            c.search("x", 1)
        self.assertIn("UnauthorizedException", str(e.exception))
        http = FakeCreatorsHttp(search=[(401, "creators_error_401_expired.json"), (200, "creators_search.json")])
        c, _ = self.client(http)
        self.assertEqual(len(c.search("x", 1)), 4, "expired token: fetch a new one and retry once")
        self.assertEqual(len([x for x in http.calls if x["url"].endswith("/token")]), 2)
        c, _ = self.client(FakeCreatorsHttp(token=(401, "creators_error_401.json")))
        with self.assertRaises(self.bot.CreatorsAuthError):
            c.search("x", 1)

    def test_get_items_batches_10_and_skips_inaccessible(self):
        http = FakeCreatorsHttp()
        c, _ = self.client(http)
        d = self.bot.CreatorsDetails(c)
        d.prefetch([f"B0WATCH{i:03d}" for i in range(1, 13)] + ["B0APINOPE1"])
        calls = [x for x in http.calls if x["url"].endswith("/getItems")]
        self.assertEqual([len(x["json"]["itemIds"]) for x in calls], [10, 3])
        self.assertEqual(calls[0]["json"]["itemIdType"], "ASIN")
        self.assertEqual(d("B0WATCH001")["price"], 450.0)
        self.assertFalse(d("B0WATCH002")["in_stock"])
        self.assertIsNone(d("B0APINOPE1"), "ItemNotAccessible")
        self.assertEqual(len([x for x in http.calls if x["url"].endswith("/getItems")]), 2, "cached, no extra calls")

    # ---- تشغيلة كاملة بالـ API
    def api_run(self, http, when=None, creds=("id", "secret", "testtag-21"), log=None):
        b = self.bot
        b.CREATORS_ID, b.CREATORS_SECRET, b.AMAZON_TAG = creds
        extra = dict(getattr(self, "extra_cfg", {}))
        if "watch" in extra:
            b.private_data["watch"] = extra.pop("watch")
        b.CONFIG_FILE.write_text(json.dumps({"source": "creators_api", **extra}), encoding="utf-8")
        clock = FakeClock()
        b.make_creators_client = lambda meta: b.CreatorsClient(b.CREATORS_ID, b.CREATORS_SECRET, b.AMAZON_TAG, meta,
                                                               http=http, clock=clock, sleep=clock.sleep,
                                                               today=lambda: "2026-10-23")
        session = FixtureSession()
        log = [] if log is None else log
        st = self.run_main(session, when or at(b, 2026, 10, 23, 12), log)
        return st, session, log

    def test_full_run_uses_the_api_and_never_scrapes(self):
        self.bot.MIN_SCORE = 0
        http = FakeCreatorsHttp()
        st, session, log = self.api_run(http)
        self.assertEqual(session.calls, 0, "no amazon.eg page requests at all")
        searches = [x for x in http.calls if x["url"].endswith("/searchItems")]
        self.assertEqual(len(searches), 15 * 2, "SearchItems for every keyword and page")
        self.assertIn("B0API00001", {x[1] for x in log if x[0] == "deal"})
        self.assertEqual(st["_meta"]["creators_status"], "ok")
        self.assertTrue(st["_meta"]["creators_ready"])

    def test_watch_and_reports_use_get_items(self):
        self.extra_cfg = {"watch": {"B0WATCH001": {"target": 500}}}
        self.bot.private_data["reports"] = {"rx": {"asin": "B0WATCH002", "at": "1"}}
        http = FakeCreatorsHttp()
        st, session, log = self.api_run(http)
        gets = [x for x in http.calls if x["url"].endswith("/getItems")]
        self.assertEqual(len(gets), 1, "watch + reports in one GetItems call")
        self.assertEqual(sorted(gets[0]["json"]["itemIds"]), ["B0WATCH001", "B0WATCH002"])
        self.assertTrue([x for x in log if x[0] == "text" and "👀" in x[2] and "450" in x[2]], "watch alert from API price")
        self.assertEqual(st["_meta"]["reports"]["rx"]["reasons"], ["out_of_stock"])
        self.assertEqual(session.calls, 0)

    def test_missing_credentials_alert_once_a_day_and_stop(self):
        http = FakeCreatorsHttp()
        log = []
        st, session, _ = self.api_run(http, creds=("", "", ""), log=log)
        self.assertEqual((session.calls, len(http.calls)), (0, 0), "no scraping fallback, no API calls")
        alerts = [x[2] for x in log if x[0] == "text" and "Creators API" in x[2]]
        self.assertEqual(len(alerts), 1)
        self.assertIn("AMAZON_CREATORS_CREDENTIAL_ID", alerts[0])
        self.assertEqual(st["_meta"].get("next", 0), 0, "rotation untouched")
        self.assertEqual((st["_meta"]["creators_status"], st["_meta"]["creators_ready"]), ("missing", False))
        self.api_run(http, when=at(self.bot, 2026, 10, 23, 18), creds=("", "", ""), log=log)
        self.assertEqual(len([x for x in log if x[0] == "text" and "Creators API" in x[2]]), 1, "once a day")

    def test_rejected_credentials_alert_and_stop_without_scraping(self):
        http = FakeCreatorsHttp(token=(401, "creators_error_401.json"))
        st, session, log = self.api_run(http)
        self.assertEqual(session.calls, 0)
        self.assertEqual(st["_meta"]["creators_status"], "rejected")
        alert = [x[2] for x in log if x[0] == "text" and "Creators API" in x[2]]
        self.assertTrue(alert and "رفض" in alert[0])
        self.assertFalse([x for x in log if x[0] == "deal"])

    def test_report_checks_in_api_mode_use_amazon_buy_box_instead_of_rating(self):
        b, now = self.bot, at(self.bot, 2026, 10, 23, 12)
        rec = {"lowest": 1000.0, "lowest_at": "2026-10-01"}
        points = [["2026-10-01T10:00+03:00", 3000.0]]
        ref = {"atl": 1000.0, "real": 66, "source": "bot"}
        base = {"eff": 1000.0, "rating": None, "rating_known": False}
        ok = b.report_checks({**base, "buybox": True}, rec, points, None, ref, 90, "amazon", 60, True, now)
        self.assertEqual(ok, [], "sold by Amazon with the buy box: no rating needed")
        third = b.report_checks({**base, "buybox": True}, rec, points, None, ref, 90, None, 60, True, now)
        self.assertEqual(third, ["مش متباع من أمازون نفسه (buy box)"], "one clear reason, no duplicate seller line")
        nobox = b.report_checks({**base, "buybox": False}, rec, points, None, ref, 90, "amazon", 60, True, now)
        self.assertEqual(nobox, ["مش متباع من أمازون نفسه (buy box)"])
        scraper = b.report_checks({"eff": 1000.0, "rating": 3.5}, rec, points, None, ref, 90, "fba", 60, True, now)
        self.assertEqual(scraper, ["التقييم 3.5 أقل من 4"], "scraper mode still checks the rating")
        raw = fxj("creators_search.json")["searchResult"]["items"][1]
        self.assertTrue(b.creators_details(raw)["buybox"], "buy box listing chosen")

    def test_scraper_is_still_the_default(self):
        self.bot.MIN_SCORE = 0
        s = FixtureSession()
        st = self.run_main(s, at(self.bot, 2026, 10, 23, 12))
        self.assertGreater(s.calls, 0)
        self.assertFalse(st["_meta"]["creators_ready"], "no credentials in tests")


# ======================= 🛍️ الوايت فرايداي (المرحلة 4) =======================
class TestWhiteFriday(BotTestCase):
    def cfg(self, **c):
        self.bot.CONFIG_FILE.write_text(json.dumps(c), encoding="utf-8")

    def test_event_status_dates_and_modes(self):
        b = self.bot
        st = lambda cfg, *d: b.event_status(cfg, at(b, *d))
        self.assertEqual(st({}, 2026, 10, 15, 12)["upcoming"]["name"], "11.11", "nearest upcoming within 30 days")
        self.assertIsNone(st({}, 2026, 10, 15, 12)["active"])
        self.assertEqual(st({}, 2026, 11, 11, 12)["active"]["name"], "11.11", "mini event day")
        self.assertEqual(st({}, 2026, 11, 12, 12)["upcoming"]["name"], "الوايت فرايداي")
        wf = st({}, 2026, 11, 27, 12)["active"]
        self.assertEqual((wf["name"], wf["estimated"], wf["black_friday"]), ("الوايت فرايداي", True, "2026-11-27"))
        self.assertIsNone(st({}, 2026, 12, 1, 12)["active"])
        self.assertIsNone(st({"white_friday": {"mode": "off"}}, 2026, 11, 25, 12)["active"], "manual off")
        on = st({"white_friday": {"mode": "on"}}, 2026, 10, 25, 12)["active"]
        self.assertTrue(on["manual"])
        self.assertEqual(b.event_start_day(on, at(b, 2026, 10, 25, 12)).isoformat(), "2026-10-25")
        moved = st({"white_friday": {"start": "2026-11-21", "end": "2026-11-29", "estimated": False}}, 2026, 11, 20, 12)
        self.assertIsNone(moved["active"], "edited dates are used")

    def test_hourly_full_scan_during_the_event_never_more(self):
        b = self.bot
        s = [FixtureSession() for _ in range(4)]
        self.run_main(s[0], at(b, 2026, 11, 25, 12, 0))
        self.run_main(s[1], at(b, 2026, 11, 25, 12, 40))   # GitHub backup بعد 40 دقيقة
        self.run_main(s[2], at(b, 2026, 11, 25, 12, 56))   # ساعة إلا 4 دقايق: جوه السماحية
        st = self.run_main(s[3], at(b, 2026, 11, 25, 13, 30))
        self.assertGreater(s[0].calls, 0)
        self.assertEqual(s[1].calls, 0, "not more than hourly")
        self.assertGreater(s[2].calls, 0)
        self.assertEqual(s[3].calls, 0)
        for x in s:
            self.assertLessEqual(x.calls, b.MAX_AMAZON_REQUESTS, "request cap still applies in event mode")

    def test_backoff_still_applies_in_event_mode(self):
        b = self.bot
        self.run_main(FixtureSession(challenge_after=0), at(b, 2026, 11, 25, 12, 0))
        s = FixtureSession()
        self.run_main(s, at(b, 2026, 11, 25, 13, 0))
        self.assertEqual(s.calls, 0, "6h backoff beats the hourly event schedule")

    def test_event_searches_focus_on_top_categories(self):
        b = self.bot
        b.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (b.DOCS_DIR / "deals.json").write_text(json.dumps({"deals": [
            {"asin": f"A{i}", "title": "t t", "category": "رياضة", "found": "2026-11-24T12:00+02:00"} for i in range(5)] + [
            {"asin": "B1", "title": "t t", "category": "عطور وتجميل", "found": "2026-11-24T12:00+02:00"}]},
            ensure_ascii=False), encoding="utf-8")
        state = {"_meta": {}}
        picked, start, total = b.pick_event_searches(state)
        focus = [k for k, kind, _ in picked if kind == "focus"]
        self.assertEqual(focus[:2], ["dumbbells", "treadmill"], "most deals first (رياضة)")
        self.assertEqual(len(focus), b.EVENT_FOCUS_KEYWORDS)
        self.assertEqual(len(picked), b.SEARCHES_PER_RUN, "same number of searches per run")
        self.assertEqual(len({k for k, *_ in picked}), len(picked), "no keyword twice")

    def history(self, asin, points):
        b = self.bot
        b.HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        h = json.loads(b.HISTORY_FILE.read_text(encoding="utf-8")) if b.HISTORY_FILE.exists() else {}
        h[asin] = points
        b.HISTORY_FILE.write_text(json.dumps(h), encoding="utf-8")

    def test_price_rise_is_honest_about_short_history(self):
        b = self.bot
        pts = [["2026-10-23T12:00+03:00", 1000.0], ["2026-11-05T12:00+03:00", 1200.0], ["2026-11-15T12:00+03:00", 1400.0]]
        first = b.parse_day("2026-10-23")
        rise = b.price_rise(pts, b.parse_day("2026-11-20"), 1400.0, first)
        self.assertEqual(rise["text"], "سعره اترفع 40% من 23 أكتوبر (1,000 ← 1,400 ج.م)")
        self.assertTrue(rise["short_history"], "30-day window starts before our history")
        self.assertIsNone(b.price_rise(pts, b.parse_day("2026-11-20"), 1100.0, first), "10% is under the threshold")

    def test_event_deal_flags_fake_discount_loudly(self):
        b = self.bot
        b.MIN_SCORE, b.CHANNEL_ID = 0, "@chan"
        # B0TEST0001 بيظهر في البحث بـ 1,499: كان 1,450 قبل الوايت فرايداي، وبعدين اترفع
        self.history("B0TEST0001", [["2026-10-23T12:00+03:00", 1450.0], ["2026-11-10T12:00+03:00", 3999.0]])
        b.STATE_FILE.write_text(json.dumps({"_meta": {}, "B0TEST0001": {"lowest": 1450.0, "lowest_at": "2026-10-23"}}),
                                encoding="utf-8")
        log = []
        self.run_main(FixtureSession(), at(b, 2026, 11, 25, 12), log)
        dash = {d["asin"]: d for d in json.loads((b.DOCS_DIR / "deals.json").read_text(encoding="utf-8"))["deals"]}
        d = dash["B0TEST0001"]
        self.assertEqual(d["event_flag"], "fake")
        self.assertIn("خصم وهمي في الوايت فرايداي", d["event_note"])
        self.assertEqual(d["real"], 0, "real discount measured against pre-event prices, not the inflated one")
        captions = [x for x in log if x[0] == "deal" and x[1] == "B0TEST0001"]
        self.assertTrue(captions, "still posted, with the warning on top")
        # الكابشن نفسه: أول سطر هو التحذير
        b.deliver = lambda deal, channel_min, loud=False: log.append(("caption", deal["caption"])) or True
        b.STATE_FILE.write_text(json.dumps({"_meta": {}, "B0TEST0001": {"lowest": 1450.0, "lowest_at": "2026-10-23"}}),
                                encoding="utf-8")
        log.clear()
        b.make_session = lambda: FixtureSession()
        b.now_cairo = lambda: at(b, 2026, 11, 25, 15)
        with contextlib.redirect_stdout(io.StringIO()):
            b.main()
        cap = [x[1] for x in log if x[0] == "caption" and "Nebula" in x[1]][0]
        self.assertTrue(cap.startswith("🚨 <b>خصم وهمي في الوايت فرايداي!</b>"), cap[:80])
        self.assertIn("⚠️ <b>سعره اترفع 176% قبل الوايت فرايداي</b>", cap)
        self.assertIn("(تاريخنا بيبدأ من 23 أكتوبر)", cap)

    def test_event_deal_without_pre_event_history_says_so(self):
        b = self.bot
        item = {"eff": 1000.0}
        chk = b.event_check(item, [], {"name": "الوايت فرايداي", "start": "2026-11-20"}, at(b, 2026, 11, 25, 12), None)
        self.assertEqual((chk["flag"], chk["ref_high"]), (None, None))
        self.assertEqual(chk["lines"], ["ℹ️ معندناش تاريخ سعر للمنتج ده قبل الوايت فرايداي، فمش قادرين نقارن"])

    def test_pre_event_alert_once_a_day_private_only(self):
        b, log = self.bot, []
        b.DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (b.DOCS_DIR / "deals.json").write_text(json.dumps({"deals": [
            {"asin": "B0RISE0001", "title": "Kivo Gaming Mouse RGB 7200", "category": "x", "price": 750, "listed": 750,
             "found": "2026-10-30T08:00+03:00"}]}), encoding="utf-8")
        self.history("B0RISE0001", [["2026-10-23T12:00+03:00", 500.0], ["2026-10-30T12:00+03:00", 750.0]])
        b.CHANNEL_ID = "@chan"
        self.run_main(FixtureSession(), at(b, 2026, 10, 30, 9), log)
        self.assertFalse([x for x in log if x[0] == "text" and "منتجات سعرها اترفع" in x[2]], "waits for 10:00")
        self.run_main(FixtureSession(), at(b, 2026, 10, 30, 12), log)
        alerts = [x for x in log if x[0] == "text" and "منتجات سعرها اترفع" in x[2]]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][1], "me", "private chat, not the channel")
        self.assertIn("قبل 11.11", alerts[0][2], "nearest upcoming event")
        self.assertIn("سعره اترفع 50% من 23 أكتوبر", alerts[0][2])
        self.assertIn("تاريخ الأسعار عندنا بيبدأ من 23 أكتوبر", alerts[0][2])
        self.run_main(FixtureSession(), at(b, 2026, 10, 30, 16), log)
        self.assertEqual(len([x for x in log if x[0] == "text" and "منتجات سعرها اترفع" in x[2]]), 1, "once a day")

    def test_reminder_on_nov_5_with_panel_button(self):
        b, log = self.bot, []
        self.run_main(FixtureSession(), at(b, 2026, 11, 4, 12), log)
        self.assertFalse([x for x in log if x[0] == "text" and "تواريخ الوايت فرايداي" in x[2]])
        self.run_main(FixtureSession(), at(b, 2026, 11, 5, 12), log)
        rem = [x for x in log if x[0] == "text" and "تواريخ الوايت فرايداي" in x[2]]
        self.assertEqual(len(rem), 1)
        self.assertEqual(rem[0][3], [[{"text": "🛍️ افتح شاشة الوايت فرايداي", "callback_data": "p:wf"}]])
        self.run_main(FixtureSession(), at(b, 2026, 11, 6, 12), log)
        self.assertEqual(len([x for x in log if x[0] == "text" and "تواريخ الوايت فرايداي" in x[2]]), 1, "only once")

    def test_no_reminder_once_dates_are_confirmed(self):
        b, log = self.bot, []
        self.cfg(white_friday={"estimated": False})
        self.run_main(FixtureSession(), at(b, 2026, 11, 5, 12), log)
        self.assertFalse([x for x in log if x[0] == "text" and "تواريخ الوايت فرايداي" in x[2]])


class TestArabicCounts(BotTestCase):
    def test_agreement_rules(self):
        c = self.bot.ar_count
        self.assertEqual([c(n, "ساعة") for n in (1, 2, 3, 10, 11, 24, 103, 111)],
                         ["ساعة", "ساعتين", "3 ساعات", "10 ساعات", "11 ساعة", "24 ساعة", "103 ساعات", "111 ساعة"])
        self.assertEqual([c(n, "دقيقة") for n in (1, 2, 5, 45)], ["دقيقة", "دقيقتين", "5 دقايق", "45 دقيقة"])
        self.assertEqual([c(n, "يوم") for n in (1, 2, 7, 14)], ["يوم", "يومين", "7 أيام", "14 يوم"])
        self.assertEqual([c(n, "لقطة") for n in (1, 2, 9, 20)], ["لقطة", "لقطتين", "9 لقطات", "20 لقطة"])
        self.assertEqual([c(n, "منتج") for n in (2, 3, 463)], ["منتجين", "3 منتجات", "463 منتج"])
        self.assertEqual([c(n, "كلمة") for n in (1, 4, 60)], ["كلمة", "4 كلمات", "60 كلمة"])
        self.assertEqual(c(30.0, "دقيقة"), "30 دقيقة", "floats from timedelta math")

    def test_history_line_uses_agreement(self):
        b = self.bot
        item = self.items()["B0TEST0001"]
        ref = {"atl": None, "real": 63, "source": None}
        b.now_cairo = lambda: at(b, 2026, 10, 23, 12)
        line = lambda day: b.format_msg(item, None, {"lowest": 1450.0, "at": day}, ref, 50, None, False, False)
        self.assertIn("اتسجل من 3 أيام", line("2026-10-20"))
        self.assertIn("اتسجل من يومين", line("2026-10-21"))
        self.assertIn("اتسجل من يوم", line("2026-10-22"))


class TestNoPersonalDataInRepo(unittest.TestCase):
    """الريبو عام: المتابعة والمكتوم والبلاغات في D1 بس، ولو رجعوا هنا الاختبار ده يفشل"""

    def test_config_has_no_watchlist_or_muted_list(self):
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
        for key in ("watch", "watchlist", "muted", "muted_asins", "reports"):
            self.assertNotIn(key, cfg, f"config.json must not contain {key!r}: it lives in D1")
        self.assertIsNone(re.search(r"B0[A-Z0-9]{8}", json.dumps(cfg)), "no ASINs in config.json")

    def test_no_reports_file_in_repo(self):
        self.assertFalse((ROOT / "reports.json").exists(), "reports live in D1, not in the public repo")

    def test_bot_never_writes_personal_data_to_the_repo(self):
        src = (ROOT / "amazon_eg_deals.py").read_text(encoding="utf-8")
        self.assertNotIn("reports.json", src)


class TestFixturesAreSynthetic(unittest.TestCase):
    def test_no_real_assets(self):
        for f in FX.glob("*.html"):
            t = f.read_text(encoding="utf-8")
            self.assertNotIn("<script", t.lower(), f.name)
            self.assertNotIn("media-amazon.com", t, f.name)
            self.assertNotIn("ssl-images-amazon", t, f.name)


if __name__ == "__main__":
    unittest.main()
