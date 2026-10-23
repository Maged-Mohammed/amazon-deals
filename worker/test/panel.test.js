// اختبارات offline للوحة التحكم بالزراير: GitHub وتليجرام وهميين، ومفيش ولا طلب للنت
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker, { arCount, duration, parseScriptMeta, renderScreen } from "../src/index.js";

const OWNER = "111222333";
const SECRET = "test-webhook-secret";
const env = {
  TG_TOKEN: "test-bot-token", OWNER_CHAT_ID: OWNER, GITHUB_TOKEN: "test-gh-token", TG_WEBHOOK_SECRET: SECRET,
  GITHUB_REPO: "owner/repo", WORKFLOW_FILE: "deals.yml", DASHBOARD_URL: "https://example.test/dash/",
};

// نسخة صناعية صغيرة من شكل السكربت: الأقسام والقيم الافتراضية بس
const SCRIPT = `
MIN_DISCOUNT = 50        # comment
MIN_SCORE = 40
SEARCH_GROUPS = {
    "موبايلات وكمبيوتر": ["smartphone", "tablet", "laptop"],
    "رياضة": ["dumbbells", "treadmill"],
    "عطور وتجميل": ["perfume men",
                    "makeup"],
    # "أخرى": ["https://example.test"],
}
SEARCHES = []
`;

const minutesAgo = (m) => new Date(Date.now() - m * 60e3).toISOString();
let repo, calls, dispatches;
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
const unb64 = (s) => Buffer.from(s, "base64").toString("utf8");

function freshRepo(lastFullMinutesAgo = 30) {
  return {
    "config.json": { text: JSON.stringify({ min_discount: null, min_score: null, extra_keywords: [], removed_keywords: [],
                                            mute_until: null, muted_asins: [], watch: {} }), sha: "c1" },
    "deals_state.json": { text: JSON.stringify({
      _meta: { last_full_run: minutesAgo(lastFullMinutesAgo), queue: {}, watch_state: { B0TEST0002: { price: 1850, in_stock: true } } },
      B0TEST0001: { lowest: 1450.0 } }), sha: "s1" },
    "amazon_eg_deals.py": { text: SCRIPT, sha: "p1" },
    "data/history.json": { text: JSON.stringify({ B0TEST0001: [["2026-10-20T10:00+03:00", 1600.0], [minutesAgo(90), 1499.0]] }), sha: "h1" },
    "docs/deals.json": { text: JSON.stringify({ deals: [{ asin: "B0TEST0001", title: "Nebula Wireless Headphones X1 Black", price: 1499 }] }), sha: "d1" },
  };
}

beforeEach(() => {
  repo = freshRepo();
  calls = [];
  dispatches = 0;
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: String(url), method: init.method || "GET", body });
    if (u.hostname === "api.telegram.org") return Response.json({ ok: true, result: { message_id: 77 } });
    if (u.hostname === "api.github.com") {
      if (!init.headers["User-Agent"] || init.headers.Authorization !== "Bearer test-gh-token") {
        return Response.json({ message: "Bad credentials" }, { status: 401 });
      }
      const path = u.pathname.replace("/repos/owner/repo", "");
      if (path.startsWith("/contents/")) {
        const name = decodeURIComponent(path.slice("/contents/".length));
        if ((init.method || "GET") === "GET") {
          const f = repo[name];
          return f ? Response.json({ content: b64(f.text), sha: f.sha }) : new Response("", { status: 404 });
        }
        assert.equal(name, "config.json", "the worker only ever writes config.json");
        assert.equal(body.sha, repo[name].sha);
        repo[name] = { text: unb64(body.content), sha: repo[name].sha + "+" };
        return Response.json({});
      }
      if (path === "/actions/workflows/deals.yml/dispatches") { dispatches++; return new Response(null, { status: 204 }); }
      if (path.startsWith("/actions/workflows/deals.yml/runs")) {
        return Response.json({ workflow_runs: [{ status: "completed", conclusion: "success", created_at: minutesAgo(20), event: "schedule" }] });
      }
    }
    if (u.hostname === "amzn.eu") return new Response(null, { status: 301, headers: { location: "https://www.amazon.eg/dp/B0TEST0001" } });
    throw new Error(`unexpected fetch: ${url}`);
  };
});

const cfg = () => JSON.parse(repo["config.json"].text);
const tgCalls = (method) => calls.filter((c) => c.url.includes("api.telegram.org") && c.url.endsWith(`/${method}`));
const writes = () => calls.filter((c) => c.method === "PUT").length;
const buttons = (kb) => kb.flat().map((b) => b.text);

async function post(payload) {
  return worker.fetch(new Request("https://bot.example.test/telegram", {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET },
  }), env, {});
}
const say = (text, extra = {}) => post({ message: { message_id: 5, text, chat: { id: Number(OWNER) }, from: { id: Number(OWNER) }, ...extra } });
const tap = (data, from = OWNER) => post({ callback_query: {
  id: `cb-${data}`, data, from: { id: Number(from) },
  message: { message_id: 50, chat: { id: Number(OWNER) }, reply_markup: { inline_keyboard: [] } } } });
const lastEdit = () => tgCalls("editMessageText").at(-1)?.body;
const lastToast = () => tgCalls("answerCallbackQuery").at(-1)?.body;
const lastSend = () => tgCalls("sendMessage").at(-1)?.body;

// ---------- القايمة الرئيسية ----------
test("/menu and /start open the panel with status line and 8 buttons", async () => {
  for (const cmd of ["/menu", "/start"]) {
    calls = [];
    await say(cmd);
    const m = lastSend();
    assert.match(m.text, /لوحة التحكم/);
    assert.match(m.text, /🟢 أمازون طبيعي · آخر بحث من 30 دقيقة · 🔔 مش مكتوم/);
    assert.deepEqual(buttons(m.reply_markup.inline_keyboard),
      ["📊 الحالة", "▶️ شغّل دلوقتي", "🎯 الخصم", "⭐ السكور", "🗂️ الأقسام", "👁️ المتابعة (0)", "🔕 كتم", "🔍 كلمات البحث"]);
  }
});

test("navigation edits the same message and every tap gets a toast", async () => {
  for (const screen of ["status", "disc", "score", "mute", "cats", "kw", "watch", "run", "main"]) {
    calls = [];
    await tap(`p:${screen}`);
    assert.equal(tgCalls("sendMessage").length, 0, `${screen}: no new message`);
    assert.equal(lastEdit().message_id, 50, `${screen}: edits the panel message`);
    assert.equal(tgCalls("answerCallbackQuery").length, 1, `${screen}: answered once`);
    assert.equal(writes(), 0, `${screen}: navigation never writes`);
  }
});

test("every screen except main has a way back", () => {
  const data = { cfg: { watch: { B0TEST0001: { target: 1 } }, extra_keywords: ["x"] }, state: {}, script: parseScriptMeta(SCRIPT) };
  for (const s of ["status", "disc", "score", "mute", "cats", "kw", "watch", "run"]) {
    const kb = renderScreen(s, data, new Date()).keyboard.flat();
    assert.ok(kb.some((b) => b.callback_data === "p:main"), `${s} has ⬅️ رجوع / ❌ لأ`);
  }
});

// ---------- الخصم والسكور ----------
test("discount screen marks the current value and saves taps", async () => {
  await tap("p:disc");
  let kb = buttons(lastEdit().reply_markup.inline_keyboard);
  assert.deepEqual(kb.slice(0, 5), ["40%", "45%", "✅ 50%", "55%", "60%"], "default 50 read from the script");
  assert.ok(kb.includes("✅ ↩️ الافتراضي (50%)"));
  await tap("d:45");
  assert.equal(cfg().min_discount, 45);
  assert.equal(lastToast().text, "✅ اتحفظ");
  kb = buttons(lastEdit().reply_markup.inline_keyboard);
  assert.ok(kb.includes("✅ 45%") && kb.includes("50%"));
  await tap("d:r");
  assert.equal(cfg().min_discount, null);
});

test("score screen", async () => {
  await tap("s:60");
  assert.equal(cfg().min_score, 60);
  assert.deepEqual(buttons(lastEdit().reply_markup.inline_keyboard).slice(0, 4), ["40", "50", "✅ 60", "70"]);
});

// ---------- الكتم ----------
test("mute buttons and remaining time in headers", async () => {
  await tap("m:3h");
  assert.ok(Math.abs(new Date(cfg().mute_until) - Date.now() - 3 * 3600e3) < 10e3);
  assert.equal(lastToast().text, "🔇 اتكتم 3 ساعات");
  assert.match(lastEdit().text, /مكتوم، فاضل 3 ساعات/);
  await tap("p:main");
  assert.match(lastEdit().text, /🔇 مكتوم \(فاضل 3 ساعات\)/);
  await tap("m:30m");
  assert.match(lastEdit().text, /فاضل 30 دقيقة/);
  await tap("m:1d");
  assert.match(lastEdit().text, /فاضل يوم/);
  await tap("m:off");
  assert.equal(cfg().mute_until, null);
  assert.match(lastEdit().text, /مش مكتوم/);
});

// ---------- الأقسام ----------
test("categories toggle on and off", async () => {
  await tap("p:cats");
  assert.deepEqual(buttons(lastEdit().reply_markup.inline_keyboard).slice(0, 3),
    ["✅ موبايلات وكمبيوتر (3 كلمات)", "✅ رياضة (كلمتين)", "✅ عطور وتجميل (كلمتين)"], "commented-out groups ignored");
  await tap("c:1");
  assert.deepEqual(cfg().disabled_categories, ["رياضة"]);
  assert.equal(lastToast().text, "⬜ القسم اتقفل");
  assert.ok(buttons(lastEdit().reply_markup.inline_keyboard).includes("⬜ رياضة (كلمتين)"));
  await tap("c:1");
  assert.deepEqual(cfg().disabled_categories, []);
  await tap("c:0"); await tap("c:1");
  const before = writes();
  await tap("c:2");
  assert.equal(writes(), before, "can't disable every category");
  assert.match(lastToast().text, /مينفعش/);
});

// ---------- كلمات البحث ----------
test("keywords: remove with ❌ and add through force_reply", async () => {
  repo["config.json"].text = JSON.stringify({ ...cfg(), extra_keywords: ["air purifier", "desk fan"] });
  await tap("p:kw");
  assert.deepEqual(buttons(lastEdit().reply_markup.inline_keyboard).slice(0, 3), ["❌ air purifier", "❌ desk fan", "➕ ضيف كلمة"]);
  await tap("k:x:0");
  assert.deepEqual(cfg().extra_keywords, ["desk fan"]);
  await tap("k:add");
  const prompt = lastSend();
  assert.equal(prompt.reply_markup.force_reply, true);
  await say("Robot Vacuum", { reply_to_message: { text: prompt.text, from: { is_bot: true } } });
  assert.deepEqual(cfg().extra_keywords, ["desk fan", "robot vacuum"]);
  assert.match(lastSend().text, /ضفت «robot vacuum»/);
});

// ---------- المتابعة ----------
test("watch screen: empty state, list, and unwatch", async () => {
  await tap("p:watch");
  assert.match(lastEdit().text, /ابعتلي لينك أي منتج من أمازون وأنا أتابعهولك/);
  repo["config.json"].text = JSON.stringify({ ...cfg(), watch: {
    B0TEST0002: { target: 1500, title: "Quanta Air Fryer 6L Digital" }, B0TEST0003: { target: null, stock_only: true } } });
  await tap("p:watch");
  const e = lastEdit();
  assert.match(e.text, /Quanta Air Fryer 6L Digital\n   💰 دلوقتي: 1,850 ج.م \| 🎯 هدفك: 1,500 ج.م/);
  assert.match(e.text, /📦 لما يرجع المخزن/);
  assert.ok(buttons(e.reply_markup.inline_keyboard).includes("❌ بطّل: Quanta Air Fryer 6L Digital"));
  await tap("w:x:B0TEST0002");
  assert.deepEqual(Object.keys(cfg().watch), ["B0TEST0003"]);
  assert.equal(lastToast().text, "✅ بطّلت أتابعه");
});

test("a bare Amazon link offers watch prices from what the bot has seen", async () => {
  await say("بص على ده https://www.amazon.eg/-/en/Nebula/dp/B0TEST0001?ref=sr_1_3");
  const m = lastSend();
  assert.match(m.text, /Nebula Wireless Headphones X1 Black/);
  assert.match(m.text, /آخر سعر شفناه: 1,499 ج.م \(من ساعتين\)/);
  assert.match(m.text, /أقل سعر شفناه: 1,450 ج.م/);
  const kb = m.reply_markup.inline_keyboard.flat();
  assert.deepEqual(kb.map((b) => b.text),
    ["📉 أقل سعر شفناه 1,450", "-10% ← 1,349", "-20% ← 1,199", "✏️ سعر تاني", "📦 لما يرجع المخزن بس"]);
  assert.deepEqual(kb.map((b) => b.callback_data),
    ["w:t:B0TEST0001:1450", "w:t:B0TEST0001:1349", "w:t:B0TEST0001:1199", "w:p:B0TEST0001", "w:s:B0TEST0001"]);
  await tap("w:t:B0TEST0001:1349");
  const w = cfg().watch.B0TEST0001;
  assert.equal(w.target, 1349);
  assert.equal(w.title, "Nebula Wireless Headphones X1 Black");
  assert.match(lastEdit().text, /هتابعلك «Nebula Wireless Headphones X1 Black»/);
  assert.equal(lastToast().text, "👀 هتابعه");
});

test("unknown product, short link, stock-only and custom price", async () => {
  await say("amzn.eu/d/xyz123");
  assert.match(lastSend().text, /آخر سعر شفناه/, "short link resolved to B0TEST0001");
  await say("https://www.amazon.eg/dp/B0NEVER001");
  const m = lastSend();
  assert.match(m.text, /لسه مشفناش المنتج ده/);
  assert.deepEqual(m.reply_markup.inline_keyboard.flat().map((b) => b.text), ["✏️ سعر تاني", "📦 لما يرجع المخزن بس"]);
  await tap("w:s:B0NEVER001");
  assert.deepEqual(cfg().watch.B0NEVER001.stock_only, true);
  assert.equal(cfg().watch.B0NEVER001.target, null);
  await tap("w:p:B0NEVER001");
  const prompt = lastSend();
  assert.equal(prompt.reply_markup.force_reply, true);
  await say("١٬٢٠٠ جنيه", { reply_to_message: { text: prompt.text } });
  assert.equal(cfg().watch.B0NEVER001.target, 1200);
  assert.equal(cfg().watch.B0NEVER001.stock_only, undefined);
});

// ---------- التشغيل ----------
test("run asks first and respects the 2-hour rule", async () => {
  await tap("p:run");
  assert.match(lastEdit().text, /أشغّل البوت دلوقتي/);
  assert.match(lastEdit().text, /آخر بحث كامل كان من 30 دقيقة، فالتشغيلة دي هتبعت الملخص/);
  assert.equal(dispatches, 0, "showing the question does not run anything");
  await tap("r:go");
  assert.equal(dispatches, 1);
  assert.equal(lastToast().text, "🚀 اتشغّل (من غير أمازون)");
  assert.match(lastEdit().text, /من غير أمازون/);
  repo = freshRepo(180);
  await tap("p:run");
  assert.match(lastEdit().text, /هيدوّر على أمازون/);
});

// ---------- الأمان ----------
test("invalid or foreign callback data is rejected without writing", async () => {
  for (const data of ["d:33", "p:hack", "w:t:short:1", "c:x", "k:x:", "s:", "x".repeat(65), "w:t:B0TEST0001:99999999"]) {
    calls = [];
    await tap(data);
    assert.equal(writes(), 0, data);
    assert.equal(lastToast().text, "زرار قديم", data);
  }
  calls = [];
  await tap("d:45", "999");
  assert.equal(writes(), 0);
  assert.equal(cfg().min_discount, null);
});

test("slash commands keep working alongside the panel", async () => {
  await say("/discount 55");
  assert.equal(cfg().min_discount, 55);
  await say("/help");
  assert.match(lastSend().text, /\/menu — لوحة التحكم/);
});

test("/setup puts /menu first in the command list", async () => {
  await worker.fetch(new Request("https://bot.example.test/setup"), env, {});
  assert.equal(tgCalls("setMyCommands")[0].body.commands[0].command, "menu");
});

// ---------- العربي ----------
test("Arabic number agreement", () => {
  assert.deepEqual([1, 2, 3, 10, 11, 25, 103].map((n) => arCount(n, "ساعة")),
    ["ساعة", "ساعتين", "3 ساعات", "10 ساعات", "11 ساعة", "25 ساعة", "103 ساعات"]);
  assert.deepEqual([1, 2, 5, 40].map((n) => arCount(n, "دقيقة")), ["دقيقة", "دقيقتين", "5 دقايق", "40 دقيقة"]);
  assert.deepEqual([1, 2, 4, 30].map((n) => arCount(n, "يوم")), ["يوم", "يومين", "4 أيام", "30 يوم"]);
  assert.deepEqual([1, 3, 12].map((n) => arCount(n, "لقطة", true)), ["لقطة واحدة", "3 لقطات", "12 لقطة"]);
  assert.deepEqual([1, 2, 7].map((n) => arCount(n, "منتج", true)), ["منتج واحد", "منتجين", "7 منتجات"]);
  assert.deepEqual([2, 9, 60].map((n) => arCount(n, "كلمة")), ["كلمتين", "9 كلمات", "60 كلمة"]);
  assert.equal(duration(3 * 3600e3), "3 ساعات");
  assert.equal(duration(2 * 3600e3), "ساعتين");
  assert.equal(duration(90 * 60e3), "ساعتين", "rounds to the nearest hour");
  assert.equal(duration(30 * 60e3), "30 دقيقة");
  assert.equal(duration(86400e3), "يوم");
});
