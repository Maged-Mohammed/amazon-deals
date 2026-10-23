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
    bot.REPORTS_FILE = tmp / "reports.json"
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
        t0 = at(b, 2026, 10, 23, 3, 0)
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


# ======================= المرحلة 3: config.json والمتابعة =======================
class TestConfigAndControl(BotTestCase):
    def config(self, **cfg):
        self.bot.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

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

    def test_no_two_full_runs_within_2_hours(self):
        b = self.bot
        s1 = FixtureSession()
        self.run_main(s1, at(b, 2026, 10, 23, 12, 0))
        s2 = FixtureSession()
        st = self.run_main(s2, at(b, 2026, 10, 23, 13, 59))
        self.assertGreater(s1.calls, 0)
        self.assertEqual(s2.calls, 0, "second run within 2h makes no Amazon requests")
        self.assertEqual(st["_meta"]["last_full_run"], "2026-10-23T12:00+03:00")
        s3 = FixtureSession()
        self.run_main(s3, at(b, 2026, 10, 23, 14, 0))
        self.assertGreater(s3.calls, 0, "exactly 2h later is a full run again")

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
        self.assertFalse(st["_meta"]["watch_state"]["B0TEST0099"]["in_stock"])

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
        self.assertEqual(set(st["_meta"]["watch_state"]), {"B0WATCH000"}, "unwatched items are forgotten")

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
    """البلاغات: reports.json (الـ Worker) ← التشغيلة بتفحص وتنشر أو تبعتلك مراجعة"""

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
        self.bot.REPORTS_FILE.write_text(json.dumps({"reports": reps}, ensure_ascii=False), encoding="utf-8")

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
        b.MAX_REPORT_CHECKS, b.MAX_AMAZON_REQUESTS = 20, 100  # هنا بنختبر حد الـ 10 رسايل بس
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
        st, log = self.run_bot(when=at(self.bot, 2026, 10, 23, 15, 0))
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


class TestFixturesAreSynthetic(unittest.TestCase):
    def test_no_real_assets(self):
        for f in FX.glob("*.html"):
            t = f.read_text(encoding="utf-8")
            self.assertNotIn("<script", t.lower(), f.name)
            self.assertNotIn("media-amazon.com", t, f.name)
            self.assertNotIn("ssl-images-amazon", t, f.name)


if __name__ == "__main__":
    unittest.main()
