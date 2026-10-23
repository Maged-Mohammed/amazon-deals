// اختبارات offline للـ Worker: fetch متبدّل بـ GitHub وتليجرام وهميين، ومفيش ولا طلب للنت
// التشغيل: cd worker && npm test
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import worker, { cairoHour, errorReply, extractAsin, githubHeaders, handleCron, parseNumber } from "../src/index.js";

const OWNER = "111222333";
const SECRET = "test-webhook-secret";
const env = {
  TG_TOKEN: "test-bot-token", OWNER_CHAT_ID: OWNER, GITHUB_TOKEN: "test-gh-token", TG_WEBHOOK_SECRET: SECRET,
  GITHUB_REPO: "owner/repo", WORKFLOW_FILE: "deals.yml", DASHBOARD_URL: "https://example.test/dash/",
};

let repo, calls, failNextPut, dispatchStatus;
const b64 = (s) => Buffer.from(s, "utf8").toString("base64");
const unb64 = (s) => Buffer.from(s, "base64").toString("utf8");

beforeEach(() => {
  repo = {
    "config.json": { text: JSON.stringify({ min_discount: null, min_score: null, extra_keywords: [], removed_keywords: [],
                                            mute_until: null, muted_asins: [], watch: {} }), sha: "sha1" },
    "deals_state.json": { text: JSON.stringify({ _meta: { last_full_run: "2026-10-23T10:59+03:00", queue: { A: {} },
                                                          amazon_backoff: null } }), sha: "sha-state" },
  };
  calls = [];
  failNextPut = 0;
  dispatchStatus = 204;
  globalThis.fetch = async (url, init = {}) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: String(url), method: init.method || "GET", body, headers: init.headers || {} });
    if (u.hostname === "api.telegram.org") return Response.json({ ok: true, result: { message_id: 1 } });
    if (u.hostname === "api.github.com") {
      // زي GitHub الحقيقي: من غير User-Agent بيرد 403، وتوكن غلط بيرد 401
      if (!init.headers["User-Agent"]) {
        return Response.json({ message: "Request forbidden by administrative rules. Please make sure your request has a User-Agent header" }, { status: 403 });
      }
      if (init.headers.Authorization !== "Bearer test-gh-token") {
        return Response.json({ message: "Bad credentials" }, { status: 401 });
      }
      const path = u.pathname.replace("/repos/owner/repo", "");
      if (path === "") return Response.json({ full_name: "owner/repo" });
      if (path === "/actions/workflows/deals.yml") return Response.json({ path: ".github/workflows/deals.yml" });
      if (path.startsWith("/contents/")) {
        const name = path.slice("/contents/".length);
        if ((init.method || "GET") === "GET") {
          const f = repo[name];
          return f ? Response.json({ content: b64(f.text), sha: f.sha }) : new Response("", { status: 404 });
        }
        if (init.method === "PUT") {
          if (failNextPut > 0) { failNextPut--; repo[name].sha += "x"; return new Response("", { status: 409 }); }
          assert.equal(body.sha, repo[name].sha, "PUT must use the latest sha");
          repo[name] = { text: unb64(body.content), sha: repo[name].sha + "+" };
          return Response.json({ content: { sha: repo[name].sha } });
        }
      }
      if (path === "/actions/workflows/deals.yml/dispatches") return new Response(null, { status: dispatchStatus });
      if (path.startsWith("/actions/workflows/deals.yml/runs")) {
        return Response.json({ workflow_runs: [{ status: "completed", conclusion: "success",
                                                 created_at: "2026-10-23T08:00:00Z", event: "workflow_dispatch" }] });
      }
    }
    if (u.hostname === "amzn.eu") return new Response(null, { status: 301, headers: { location: "https://www.amazon.eg/dp/B0SHORT001?ref=x" } });
    throw new Error(`unexpected fetch: ${url}`);
  };
});

const cfg = () => JSON.parse(repo["config.json"].text);
const tgSends = () => calls.filter((c) => c.url.includes("api.telegram.org"));
const lastReply = () => tgSends().filter((c) => c.url.endsWith("/sendMessage")).at(-1)?.body.text;

function update(text, from = OWNER) {
  return { message: { message_id: 5, text, chat: { id: Number(from) }, from: { id: Number(from) } } };
}

async function post(payload, secret = SECRET) {
  return worker.fetch(new Request("https://bot.example.test/telegram", {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": secret },
  }), env, {});
}

// ---------- الأمان ----------
test("rejects a wrong webhook secret", async () => {
  const res = await post(update("/status"), "wrong");
  assert.equal(res.status, 401);
  assert.equal(calls.length, 0);
});

test("ignores anyone who is not the owner", async () => {
  const res = await post(update("/discount 10", "999"));
  assert.equal(res.status, 200);
  assert.equal(calls.length, 0, "no GitHub write and no reply");
  assert.equal(cfg().min_discount, null);
});

// ---------- الأوامر ----------
test("/add and /remove keywords", async () => {
  await post(update("/add Air Purifier"));
  assert.deepEqual(cfg().extra_keywords, ["air purifier"]);
  assert.match(lastReply(), /ضفت «air purifier»/);
  await post(update("/remove air purifier"));
  assert.deepEqual(cfg().extra_keywords, []);
  await post(update("/remove smartphone"));
  assert.deepEqual(cfg().removed_keywords, ["smartphone"]);
  await post(update("/add smartphone"));
  assert.deepEqual(cfg().removed_keywords, [], "re-adding a removed default restores it");
  const put = calls.find((c) => c.method === "PUT");
  assert.equal(put.body.message, "config: /add air purifier", "plain commit message");
});

test("/discount and /score validate and reset", async () => {
  await post(update("/discount 45"));
  assert.equal(cfg().min_discount, 45);
  await post(update("/discount 0"));
  assert.match(lastReply(), /⚠️/);
  assert.equal(cfg().min_discount, 45);
  await post(update("/score ٥٥"));
  assert.equal(cfg().min_score, 55, "arabic digits");
  await post(update("/score reset"));
  assert.equal(cfg().min_score, null);
});

test("/mute sets an absolute time and off clears it", async () => {
  const before = Date.now();
  await post(update("/mute 3h"));
  const until = new Date(cfg().mute_until).getTime();
  assert.ok(until >= before + 3 * 3600e3 - 5000 && until <= Date.now() + 3 * 3600e3 + 5000);
  assert.match(lastReply(), /القناة شغالة عادي/);
  await post(update("/mute 30m"));
  assert.ok(new Date(cfg().mute_until).getTime() <= Date.now() + 31 * 60e3);
  await post(update("/mute 30 يوم"));
  assert.match(lastReply(), /⚠️/);
  await post(update("/mute off"));
  assert.equal(cfg().mute_until, null);
});

test("/watch, /list and /unwatch", async () => {
  await post(update("/watch https://www.amazon.eg/-/en/Some-Product/dp/B0TEST0001/ref=sr_1_1 1,500"));
  assert.equal(cfg().watch.B0TEST0001.target, 1500);
  await post(update("/watch https://amzn.eu/d/abcdef 999"));
  assert.equal(cfg().watch.B0SHORT001.target, 999, "short link resolved through redirect");
  await post(update("/watch not-a-link 100"));
  assert.match(lastReply(), /ASIN/);
  await post(update("/list"));
  assert.match(lastReply(), /B0TEST0001/);
  assert.match(lastReply(), /المتابعة \(2\)/);
  await post(update("/unwatch B0TEST0001"));
  assert.equal(cfg().watch.B0TEST0001, undefined);
  await post(update("/unwatch B0TEST0001"));
  assert.match(lastReply(), /مش في قايمة المتابعة/);
});

test("/status summarizes state and config", async () => {
  await post(update("/status"));
  const r = lastReply();
  assert.match(r, /حالة البوت/);
  assert.match(r, /أمازون: طبيعي/);
  assert.match(r, /مستني في الملخص: لقطة واحدة/);
  assert.match(r, /example\.test\/dash/);
});

test("/run dispatches the workflow and warns about a recent full run", async () => {
  await post(update("/run"));
  assert.ok(calls.some((c) => c.url.endsWith("/actions/workflows/deals.yml/dispatches") && c.body.ref === "main"));
  assert.match(lastReply(), /شغّلت البوت/);
  dispatchStatus = 403;
  await post(update("/run"));
  assert.match(lastReply(), /❌/, "failure is reported, not hidden");
});

test("unknown command shows help", async () => {
  await post(update("/foo"));
  assert.match(lastReply(), /مش فاهم/);
});

test("config write retries on a conflict", async () => {
  failNextPut = 1;
  await post(update("/discount 33"));
  assert.equal(cfg().min_discount, 33);
  assert.equal(calls.filter((c) => c.method === "PUT").length, 2);
});

// ---------- زرار 🔕 ----------
test("mute button adds the ASIN and swaps the button for undo", async () => {
  await post({ callback_query: {
    id: "cb1", data: "mute:B0TEST0002", from: { id: Number(OWNER) },
    message: { message_id: 9, chat: { id: Number(OWNER) }, text: "👍 كويسة — سكور 55/100\nQuanta Air Fryer\n💰 1,800 ج.م",
               reply_markup: { inline_keyboard: [
      [{ text: "📈", url: "https://k.example.test" }, { text: "🛒", url: "https://a.example.test" }],
      [{ text: "🔕", callback_data: "mute:B0TEST0002" }]] } } } });
  assert.deepEqual(cfg().muted_asins, ["B0TEST0002"]);
  const edit = tgSends().find((c) => c.url.endsWith("/editMessageText"));
  assert.equal(edit.body.text, "🔕 مش هبعتلك عن المنتج ده تاني\nQuanta Air Fryer");
  assert.deepEqual(edit.body.reply_markup.inline_keyboard.map((r) => r.map((b) => b.text)), [["📈", "🛒"], ["↩️ تراجع"]]);
  assert.ok(tgSends().some((c) => c.url.endsWith("/answerCallbackQuery")));
});

test("mute button from someone else does nothing", async () => {
  await post({ callback_query: { id: "cb2", data: "mute:B0TEST0002", from: { id: 5 } } });
  assert.deepEqual(cfg().muted_asins, []);
});

// ---------- Cron ----------
test("3-hourly cron dispatches", async () => {
  assert.equal(await handleCron("0 */3 * * *", env, new Date("2026-10-23T09:00:00Z")), "dispatched");
});

test("morning cron runs only at 08:xx Cairo (summer and winter time)", async () => {
  // 23 أكتوبر: القاهرة +3، فـ 05:05 UTC = 08:05
  assert.equal(await handleCron("5 5 * * *", env, new Date("2026-10-23T05:05:00Z")), "dispatched");
  assert.equal(await handleCron("5 6 * * *", env, new Date("2026-10-23T06:05:00Z")), "skipped");
  // 5 نوفمبر: القاهرة +2، فـ 06:05 UTC = 08:05
  assert.equal(await handleCron("5 5 * * *", env, new Date("2026-11-05T05:05:00Z")), "skipped");
  assert.equal(await handleCron("5 6 * * *", env, new Date("2026-11-05T06:05:00Z")), "dispatched");
});

test("cron failure alerts the owner", async () => {
  dispatchStatus = 500;
  assert.equal(await handleCron("0 */3 * * *", env, new Date()), "failed");
  assert.match(tgSends().at(-1).body.text, /تنبيه إداري/);
});

// ---------- /setup ----------
test("/setup points the webhook at this worker with the secret", async () => {
  const res = await worker.fetch(new Request("https://bot.example.test/setup"), env, {});
  const data = await res.json();
  assert.equal(data.webhook_url, "https://bot.example.test/telegram");
  const hook = tgSends().find((c) => c.url.endsWith("/setWebhook"));
  assert.equal(hook.body.secret_token, SECRET);
  assert.deepEqual(hook.body.allowed_updates, ["message", "callback_query"]);
  assert.ok(!JSON.stringify(data).includes(SECRET), "never echoes the secret");
});

// ---------- أخطاء GitHub ----------
test("every GitHub request sends User-Agent, Authorization and Accept", async () => {
  await post(update("/status"));
  await post(update("/discount 30"));
  await post(update("/run"));
  const ghCalls = calls.filter((c) => c.url.includes("api.github.com"));
  assert.ok(ghCalls.length >= 5);
  for (const c of ghCalls) {
    assert.equal(c.headers["User-Agent"], "loata-bot-worker", c.url);
    assert.match(c.headers.Accept, /^application\/vnd\.github/);
    assert.ok(c.headers.Authorization.startsWith("Bearer "));
  }
});

test("a request without User-Agent is what GitHub rejects (guards the header)", async () => {
  const headers = githubHeaders(env);
  delete headers["User-Agent"];
  const res = await fetch("https://api.github.com/repos/owner/repo", { headers });
  assert.equal(res.status, 403);
  assert.match((await res.json()).message, /User-Agent/);
});

test("secrets pasted with a trailing newline still work", async () => {
  const messy = { ...env, GITHUB_TOKEN: "test-gh-token\r\n", OWNER_CHAT_ID: ` ${OWNER}\n` };
  const res = await worker.fetch(new Request("https://bot.example.test/telegram", {
    method: "POST", body: JSON.stringify(update("/discount 21")),
    headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET } }), messy, {});
  assert.equal(res.status, 200);
  assert.equal(cfg().min_discount, 21);
});

test("a GitHub rejection is reported with its status and reason, never the token", async () => {
  const bad = { ...env, GITHUB_TOKEN: "wrong-token" };
  await worker.fetch(new Request("https://bot.example.test/telegram", {
    method: "POST", body: JSON.stringify(update("/status")),
    headers: { "X-Telegram-Bot-Api-Secret-Token": SECRET } }), bad, {});
  const reply = lastReply();
  assert.equal(reply, "❌ GitHub رفض الطلب 401: Bad credentials");
  assert.ok(!reply.includes("wrong-token"));
  assert.equal(errorReply(new Error("boom")), "❌ حصلت مشكلة: boom");
});

test("/health reports GitHub status codes without secrets", async () => {
  const ok = await (await worker.fetch(new Request("https://bot.example.test/health"), env, {})).json();
  assert.deepEqual(ok, { token_set: true, repo: 200, contents: 200, actions: 200 });
  const bad = await (await worker.fetch(new Request("https://bot.example.test/health"),
                                        { ...env, GITHUB_TOKEN: "wrong-token" }, {})).json();
  assert.deepEqual(bad.repo, { status: 401, error: "Bad credentials" });
  assert.ok(!JSON.stringify(bad).includes("wrong-token"));
});

// ---------- أدوات ----------
test("helpers", async () => {
  assert.equal(parseNumber("١٬٥٠٠"), 1500);
  assert.equal(parseNumber("abc"), null);
  assert.equal(await extractAsin("b0test0001"), "B0TEST0001");
  assert.equal(await extractAsin("https://www.amazon.eg/gp/product/B0TEST0003?x=1"), "B0TEST0003");
  assert.equal(cairoHour(new Date("2026-10-23T05:05:00Z")), 8);
  assert.equal(cairoHour(new Date("2026-11-05T05:05:00Z")), 7);
});
