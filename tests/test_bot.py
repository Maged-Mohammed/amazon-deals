"""
اختبارات offline للبوت على صفحات HTML صناعية (tests/fixtures/، بيولّدها tests/make_fixtures.py).
مفيش ولا طلب للنت: الـ sessions وتليجرام كلهم متبدّلين، وأي محاولة نت بترمي NetworkUsed.

التشغيل:  python -X utf8 -m unittest discover -s tests -v
"""
import contextlib
import importlib.util
import io
import json
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

    def __init__(self, challenge_after=None, product="product_fba.html"):
        self.calls, self.kinds = 0, []
        self.challenge_after, self.product = challenge_after, product

    def get(self, url, **kwargs):
        self.calls += 1
        kind = "dp" if "/dp/" in url else "search" if "/s?" in url else "home"
        self.kinds.append(kind)
        if self.challenge_after is not None and self.calls > self.challenge_after:
            return Resp(fx("challenge.html"))
        if kind == "search":
            return Resp(fx("search.html"))
        if kind == "dp":
            return Resp(fx(self.product))
        return Resp("<html><body>home</body></html>")


def load_bot(tmp):
    """نسخة جديدة من السكربت لكل اختبار، ملفاتها في فولدر مؤقت ومن غير نت"""
    spec = importlib.util.spec_from_file_location("bot_under_test", ROOT / "amazon_eg_deals.py")
    bot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bot)
    bot.STATE_FILE = tmp / "deals_state.json"
    bot.HISTORY_FILE = tmp / "data" / "history.json"
    bot.DOCS_DIR = tmp / "docs"
    bot.TOKEN = bot.CHAT_ID = bot.CHANNEL_ID = bot.AMAZON_TAG = ""
    bot.time = types.SimpleNamespace(sleep=lambda *_: None)
    bot.requests = types.SimpleNamespace(post=no_network, get=no_network, Session=no_network,
                                         RequestException=Exception)
    bot.make_session = no_network
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
        b.send_text = lambda chat, text, loud=False: log.append(("text", chat, text)) or True
        b.deliver = lambda deal, channel_min, loud=False: log.append(("deal", deal["asin"], loud)) or True
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
        t0 = at(b, 2026, 10, 23, 3, 0)
        h = lambda hours: (t0 + timedelta(hours=hours)).isoformat(timespec="minutes")
        sends = []
        s = FixtureSession(challenge_after=0)
        st = self.run_main(s, t0, sends)
        self.assertEqual(st["_meta"]["amazon_backoff"]["level"], 1)
        self.assertEqual(st["_meta"]["amazon_backoff"]["until"], h(6))
        self.assertEqual(s.calls, 1, "stopped at the homepage")
        self.assertIn("6 ساعة", [x for x in sends if x[0] == "text"][-1][2])

        s = FixtureSession()
        st = self.run_main(s, t0 + timedelta(hours=3))
        self.assertEqual(s.calls, 0, "no Amazon requests during backoff")
        self.assertEqual(st["_meta"]["next"], 0, "rotation does not move during backoff")

        for when, level, until in ((9, 2, 21), (22, 3, 46), (47, 3, 71)):
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


class TestFixturesAreSynthetic(unittest.TestCase):
    def test_no_real_assets(self):
        for f in FX.glob("*.html"):
            t = f.read_text(encoding="utf-8")
            self.assertNotIn("<script", t.lower(), f.name)
            self.assertNotIn("media-amazon.com", t, f.name)
            self.assertNotIn("ssl-images-amazon", t, f.name)


if __name__ == "__main__":
    unittest.main()
