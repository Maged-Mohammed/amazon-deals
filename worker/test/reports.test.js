// اختبارات offline للبلاغات: KV وGitHub وتليجرام وهميين، ومفيش ولا طلب للنت
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker, { pendingReports } from "../src/index.js";

const OWNER = "111222333";
const ALICE = 444555666; // متابع عادي
const BOB = 777888999;
const SECRET = "test-webhook-secret";

let repo, calls, kv, kvPuts, env;
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
const unb64 = (s) => Buffer.from(s, "base64").toString("utf8");
const iso = (minutesAgo) => new Date(Date.now() - minutesAgo * 60e3).toISOString();

beforeEach(() => {
  kv = new Map();
  kvPuts = [];
  env = {
    TG_TOKEN: "test-bot-token", OWNER_CHAT_ID: OWNER, GITHUB_TOKEN: "test-gh-token", TG_WEBHOOK_SECRET: SECRET,
    GITHUB_REPO: "owner/repo", WORKFLOW_FILE: "deals.yml", TG_CHANNEL_ID: "@chan",
    REPORTS_KV: {
      get: async (k) => (kv.has(k) ? kv.get(k) : null),
      put: async (k, v, opts) => { kv.set(k, v); kvPuts.push({ k, opts }); },
    },
  };
  repo = {
    "config.json": { text: JSON.stringify({ watch: {}, muted_asins: [] }), sha: "c1" },
    "reports.json": { text: JSON.stringify({ reports: {} }), sha: "r1" },
    "deals_state.json": { text: JSON.stringify({ _meta: { reports: {} } }), sha: "s1" },
    "docs/deals.json": { text: JSON.stringify({ deals: [] }), sha: "d1" },
  };
  calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: String(url), method: init.method || "GET", body });
    if (u.hostname === "api.telegram.org") {
      if (u.pathname.endsWith("/deleteMessage") && body.message_id === 404) {
        return Response.json({ ok: false, description: "Bad Request: message can't be deleted" });
      }
      return Response.json({ ok: true, result: { message_id: 1 } });
    }
    if (u.hostname === "api.github.com") {
      const path = u.pathname.replace("/repos/owner/repo", "");
      const name = decodeURIComponent(path.slice("/contents/".length));
      if ((init.method || "GET") === "GET") {
        const f = repo[name];
        return f ? Response.json({ content: b64(f.text), sha: f.sha }) : new Response("", { status: 404 });
      }
      assert.ok(["config.json", "reports.json"].includes(name), `worker only writes config/reports, not ${name}`);
      repo[name] = { text: unb64(body.content), sha: repo[name].sha + "+" };
      return Response.json({});
    }
    if (u.hostname === "amzn.eu") return new Response(null, { status: 301, headers: { location: "https://www.amazon.eg/dp/B0SHORT001" } });
    throw new Error(`unexpected fetch: ${url}`);
  };
});

const json = (name) => JSON.parse(repo[name].text);
const privData = () => ({ watch: {}, muted: {}, ...JSON.parse(kv.get("owner:private") || "{}") });
const reports = () => json("reports.json").reports;
const setState = (meta) => { repo["deals_state.json"].text = JSON.stringify({ _meta: meta }); };
const setReports = (r) => { repo["reports.json"].text = JSON.stringify({ reports: r }); };
const tg = (method) => calls.filter((c) => c.url.includes("api.telegram.org") && c.url.endsWith(`/${method}`));
const writes = (name) => calls.filter((c) => c.method === "PUT" && c.url.includes(`/contents/${name}`)).length;
const lastSend = () => tg("sendMessage").at(-1)?.body;
const lastToast = () => tg("answerCallbackQuery").at(-1)?.body.text;

async function post(payload) {
  return worker.fetch(new Request("https://bot.example.test/telegram", {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": SECRET } }), env, {});
}
const say = (from, text, username) => post({ message: { message_id: 3, text, chat: { id: from, type: "private" },
                                                         from: { id: from, ...(username ? { username } : {}) } } });
const tap = (from, data, message = {}, username) => post({ callback_query: {
  id: `cb-${data}`, data, from: { id: Number(from), ...(username ? { username } : {}) },
  message: { message_id: 70, chat: { id: Number(from) }, ...message } } });

// ---------- غير صاحب البوت ----------
test("other users only get /start, /help and the report flow", async () => {
  for (const text of ["/start", "/start report", "/help", "/status", "/discount 10", "/menu", "hello"]) {
    calls = [];
    await say(ALICE, text);
    assert.match(lastSend().text, /بلّغ عن لقطة/, text);
    assert.equal(lastSend().chat_id, ALICE);
    assert.equal(writes("config.json") + writes("reports.json"), 0, `${text}: no writes`);
  }
  await post({ message: { message_id: 3, text: "https://www.amazon.eg/dp/B0TEST0001", chat: { id: -100, type: "group" }, from: { id: ALICE } } });
  assert.equal(writes("reports.json"), 0, "groups are ignored");
});

test("a report stores only the ASIN and time; the link is rebuilt later", async () => {
  await say(ALICE, "شوفوا العرض ده 🔥 https://www.amazon.eg/-/en/Some-Name/dp/B0TEST0001/ref=sr_1_1?tag=someone-21&th=1");
  const [[id, rep]] = Object.entries(reports());
  assert.match(id, /^r[a-z0-9]{6,14}$/);
  assert.deepEqual(Object.keys(rep).sort(), ["asin", "at"], "no user id, text, tag or query params");
  assert.equal(rep.asin, "B0TEST0001");
  assert.ok(!repo["reports.json"].text.includes(String(ALICE)));
  assert.match(lastSend().text, /شكرًا! 🙏 استلمت البلاغ/);
  assert.equal(lastSend().reply_markup, undefined, "no credit button without a username");
  const user = JSON.parse(kv.get(`user:${ALICE}`));
  assert.equal(user.count, 1);
  assert.equal(user.reports[0].id, id);
  assert.equal(kvPuts.at(-1).opts.expirationTtl, 8 * 86400, "user data expires by itself");
});

test("short links work and usernames get an opt-in credit button", async () => {
  await say(BOB, "amzn.eu/d/abc123", "bob_deals");
  const [[id, rep]] = Object.entries(reports());
  assert.equal(rep.asin, "B0SHORT001");
  assert.deepEqual(lastSend().reply_markup.inline_keyboard[0][0], { text: "🙋 اكتب اسمي لو اتنشرت", callback_data: `cr:${id}` });
  await tap(ALICE, `cr:${id}`, {}, "alice_x");
  assert.equal(lastToast(), "الزرار ده مش ليك");
  assert.equal(reports()[id].credit, undefined);
  await tap(BOB, `cr:${id}`, {}, "bob_deals");
  assert.equal(reports()[id].credit, "bob_deals");
  assert.equal(lastToast(), "تمام 🙌 هنكتب اسمك لو اتنشرت");
});

test("5 reports per user per day", async () => {
  for (let i = 0; i < 5; i++) await say(ALICE, `https://www.amazon.eg/dp/B0TEST000${i}`);
  assert.equal(Object.keys(reports()).length, 5);
  await say(ALICE, "https://www.amazon.eg/dp/B0TEST0009");
  assert.match(lastSend().text, /وصلت للحد النهارده \(5 لقطات\)/);
  assert.equal(Object.keys(reports()).length, 5);
});

test("each ASIN is accepted once per 7 days, from anyone", async () => {
  setReports({ rold000001: { asin: "B0TEST0001", at: iso(3 * 24 * 60) } });
  await say(BOB, "https://www.amazon.eg/dp/B0TEST0001");
  assert.match(lastSend().text, /حد بلّغ عنه قبل كده/);
  assert.equal(Object.keys(reports()).length, 1);
  setReports({ rold000001: { asin: "B0TEST0001", at: iso(8 * 24 * 60) } });
  await say(BOB, "https://www.amazon.eg/dp/B0TEST0001");
  assert.equal(Object.keys(reports()).length, 2, "older than 7 days: accepted again");
});

test("not an Amazon product link", async () => {
  await say(ALICE, "https://www.amazon.eg/gp/help/customer/display.html");
  assert.match(lastSend().text, /مش لمنتج من أمازون مصر/);
  assert.equal(writes("reports.json"), 0);
  assert.equal(JSON.parse(kv.get(`user:${ALICE}`)).rejects.length, 1);
});

test("more than 10 rejections in a week: ignored silently", async () => {
  // 11 بلاغ البوت قال عليهم بايظين
  const ids = Array.from({ length: 11 }, (_, i) => `rbad0000${String(i).padStart(2, "0")}`);
  kv.set(`user:${ALICE}`, JSON.stringify({ day: "2000-01-01", count: 0, reports: ids.map((id) => ({ id, at: iso(60) })) }));
  setState({ reports: Object.fromEntries(ids.map((id) => [id, { status: "dropped", at: iso(60) }])) });
  calls = [];
  await say(ALICE, "https://www.amazon.eg/dp/B0TEST0001");
  assert.equal(tg("sendMessage").length, 0, "no reply at all");
  assert.equal(writes("reports.json"), 0);
  calls = [];
  await say(ALICE, "/start");
  assert.equal(tg("sendMessage").length, 0, "still ignored");
});

// ---------- مراجعة صاحب البوت ----------
const REVIEW_TEXT = "📥 بلاغ محتاج رأيك\nBudget Earbuds Z2\n💰 450 ج.م بدل 1,500 ج.م (خصم 70%)\n• البياع مش أمازون";
const reviewMsg = (id) => ({ text: REVIEW_TEXT, reply_markup: { inline_keyboard: [
  [{ text: "✅ انشر", callback_data: `rv:p:${id}` }, { text: "❌ ارفض", callback_data: `rv:x:${id}` }, { text: "👁️ تابعه بس", callback_data: `rv:w:${id}` }],
  [{ text: "🛒 شوفه على أمازون", url: "https://www.amazon.eg/dp/B0TEST0061" }]] } });
const ID = "rtest00001";

function withReview(extra = {}) {
  setReports({ [ID]: { asin: "B0TEST0061", at: iso(30), ...extra } });
  setState({ reports: { [ID]: { asin: "B0TEST0061", status: "review", title: "Budget Earbuds Z2", price: 450, old: 1500,
                                 score: 48, reasons: ["البياع مش أمازون"], at: iso(30) } } });
}

test("✅ انشر records the decision and shows ↩️ تراجع", async () => {
  withReview();
  await tap(OWNER, `rv:p:${ID}`, reviewMsg(ID));
  assert.equal(reports()[ID].decision, "publish");
  assert.ok(reports()[ID].decided_at);
  const edit = tg("editMessageText").at(-1).body;
  assert.match(edit.text, /البياع مش أمازون\n———\n✅ هيتنشر في التشغيلة الجاية/);
  assert.deepEqual(edit.reply_markup.inline_keyboard.map((r) => r.map((b) => b.text)), [["↩️ تراجع"], ["🛒 شوفه على أمازون"]]);
  assert.equal(lastToast(), "✅ هيتنشر");
  // ↩️ تراجع جوه الـ 10 دقايق
  await tap(OWNER, `rv:u:${ID}`, { text: edit.text.replace(/<[^>]+>/g, ""), reply_markup: edit.reply_markup });
  assert.equal(reports()[ID].decision, undefined);
  const back = tg("editMessageText").at(-1).body;
  assert.doesNotMatch(back.text, /———/);
  assert.deepEqual(back.reply_markup.inline_keyboard[0].map((b) => b.callback_data), [`rv:p:${ID}`, `rv:x:${ID}`, `rv:w:${ID}`]);
});

test("↩️ تراجع stops working after 10 minutes", async () => {
  withReview({ decision: "reject", decided_at: iso(11) });
  await tap(OWNER, `rv:u:${ID}`, reviewMsg(ID));
  assert.match(lastToast(), /عدّى أكتر من 10 دقايق/);
  assert.equal(reports()[ID].decision, "reject");
});

test("👁️ تابعه بس adds a watch at 10% below, and undo removes it", async () => {
  withReview();
  await tap(OWNER, `rv:w:${ID}`, reviewMsg(ID));
  assert.equal(privData().watch.B0TEST0061.target, 405);
  assert.match(tg("editMessageText").at(-1).body.text, /هتابعه لحد 405 ج.م/);
  await tap(OWNER, `rv:u:${ID}`, reviewMsg(ID));
  assert.equal(privData().watch.B0TEST0061, undefined);
});

test("decisions on closed reports and from other users are rejected", async () => {
  withReview();
  setState({ reports: { [ID]: { asin: "B0TEST0061", status: "published" } } });
  await tap(OWNER, `rv:p:${ID}`, reviewMsg(ID));
  assert.match(lastToast(), /اتقفل خلاص/);
  withReview();
  calls = [];
  await tap(ALICE, `rv:p:${ID}`, reviewMsg(ID));
  assert.equal(writes("reports.json"), 0);
  assert.equal(reports()[ID].decision, undefined);
});

test("🗑️ احذف من القناة deletes the post and mutes the product", async () => {
  setState({ reports: { [ID]: { asin: "B0TEST0001", status: "published", channel_message_id: 321, title: "Nebula X1" } } });
  await tap(OWNER, `rd:${ID}`, { text: "🚨 اتنشر بلاغ تلقائي\nNebula X1" });
  const del = tg("deleteMessage").at(-1).body;
  assert.deepEqual(del, { chat_id: "@chan", message_id: 321 });
  assert.deepEqual(Object.keys(privData().muted), ["B0TEST0001"]);
  assert.match(tg("editMessageText").at(-1).body.text, /اتحذف من القناة، والمنتج اتكتم/);
  setState({ reports: { [ID]: { asin: "B0TEST0001", status: "published", channel_message_id: 404 } } });
  await tap(OWNER, `rd:${ID}`, { text: "x" });
  assert.match(lastToast(), /48 ساعة/, "Telegram refusal is explained");
});

// ---------- لوحة التحكم ----------
test("auto-publish toggle and the pending reports screen", async () => {
  withReview();
  await say(Number(OWNER), "/menu");
  const kb = lastSend().reply_markup.inline_keyboard.flat().map((b) => b.text);
  assert.ok(kb.includes("📥 بلاغات مستنية (1)"));
  assert.ok(kb.includes("🚨 النشر التلقائي للبلاغات ✅"), "on by default");
  await tap(OWNER, "ap:t");
  assert.equal(json("config.json").report_autopublish, false);
  assert.ok(tg("editMessageText").at(-1).body.reply_markup.inline_keyboard.flat().some((b) => b.text === "🚨 النشر التلقائي للبلاغات ⬜"));
  await tap(OWNER, "ap:t");
  assert.equal(json("config.json").report_autopublish, true);

  await tap(OWNER, "p:reports");
  const screen = tg("editMessageText").at(-1).body;
  assert.match(screen.text, /بلاغات مستنية \(1\)/);
  assert.match(screen.text, /Budget Earbuds Z2\n   💰 450 ج.م بدل 1,500 ج.م — سكور 48\n   ⚠️ البياع مش أمازون/);
  assert.deepEqual(screen.reply_markup.inline_keyboard[0].map((b) => b.callback_data), [`rs:p:${ID}`, `rs:x:${ID}`, `rs:w:${ID}`]);
  await tap(OWNER, `rs:x:${ID}`);
  assert.equal(reports()[ID].decision, "reject");
  assert.match(tg("editMessageText").at(-1).body.text, /مفيش بلاغات مستنية/);
});

test("pendingReports ignores decided and non-review items", () => {
  const state = { _meta: { reports: { a: { status: "review", at: "1" }, b: { status: "review", at: "2" }, c: { status: "published" } } } };
  const inbox = { reports: { a: {}, b: { decision: "reject" }, c: {} } };
  assert.deepEqual(pendingReports(state, inbox).map(([id]) => id), ["a"]);
});

test("the owner can test the flow with /report", async () => {
  await say(Number(OWNER), "/report https://www.amazon.eg/dp/B0TEST0001");
  assert.equal(Object.values(reports())[0].asin, "B0TEST0001");
  assert.match(lastSend().text, /استلمت البلاغ/);
});
