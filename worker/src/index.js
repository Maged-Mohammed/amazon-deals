// Cloudflare Worker لبوت لقطات أمازون مصر:
//  1) Telegram webhook: أوامر صاحب البوت بس، وبتعدّل config.json في الريبو عن طريق GitHub API
//  2) Cron: بيشغّل الـ workflow في ميعاده (GitHub schedule بيتأخر)، و08:05 القاهرة عشان ملخص الصبح
// الأسرار: TG_TOKEN، OWNER_CHAT_ID، GITHUB_TOKEN، TG_WEBHOOK_SECRET (wrangler secret put)

const GH = "https://api.github.com";
const TG = "https://api.telegram.org";
const CONFIG_PATH = "config.json";
const STATE_PATH = "deals_state.json";
const MORNING_CRONS = new Set(["5 5 * * *", "5 6 * * *"]);
const FULL_RUN_GAP_MIN = 120; // نفس MIN_HOURS_BETWEEN_FULL_RUNS في البوت
const ASIN_RE = /^[A-Z0-9]{10}$/;

class UserError extends Error {}

// GitHub رفض الطلب: بنحتفظ بالكود ورسالة GitHub والصلاحية الناقصة (مفيش أي سر هنا)
class GitHubError extends Error {
  constructor(what, status, detail) {
    super(`GitHub ${what}: ${status}${detail ? ` (${detail})` : ""}`);
    this.status = status;
    this.detail = detail;
  }
}

async function githubDetail(res) {
  let msg = "";
  try {
    msg = ((await res.clone().json()) || {}).message || "";
  } catch {}
  const need = res.headers.get("x-accepted-github-permissions");
  return [msg.slice(0, 120), need ? `محتاج: ${need}` : ""].filter(Boolean).join(" — ");
}

async function ghOk(res, what) {
  if (!res.ok) throw new GitHubError(what, res.status, await githubDetail(res));
  return res;
}

export default {
  async fetch(request, env, ctx) {
    return handleFetch(request, env);
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil(handleCron(event.cron, env, new Date(event.scheduledTime)));
  },
};

// ================== HTTP ==================
export async function handleFetch(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/") return new Response("loata bot worker ✅");
  if (request.method === "GET" && url.pathname === "/setup") {
    // آمن لو أي حد فتحه: بيظبط الـ webhook على نفس الـ Worker ومبيكشفش أي سر
    return Response.json(await ensureWebhook(env, url.origin));
  }
  if (request.method === "GET" && url.pathname === "/health") {
    // تشخيص: هل التوكن شغال؟ بيرجّع أكواد GitHub ورسايله بس، ومفيش ولا سر
    return Response.json(await githubHealth(env));
  }
  if (request.method === "POST" && url.pathname === "/telegram") {
    const given = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
    if (!secret(env.TG_WEBHOOK_SECRET) || !safeEqual(given, secret(env.TG_WEBHOOK_SECRET))) {
      return new Response("unauthorized", { status: 401 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    try {
      await handleUpdate(update, env);
    } catch (e) {
      console.error("update failed:", e && e.message);
    }
    return new Response("ok"); // تليجرام لازم ياخد 200 وإلا هيعيد نفس الرسالة
  }
  return new Response("not found", { status: 404 });
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

const isOwner = (env, id) => id != null && String(id) === secret(env.OWNER_CHAT_ID);

export async function handleUpdate(update, env) {
  if (update.callback_query) return handleCallback(update.callback_query, env);
  const msg = update.message;
  if (!msg || !msg.text || !isOwner(env, msg.chat && msg.chat.id) || !isOwner(env, msg.from && msg.from.id)) {
    return; // أي حد غير صاحب البوت: تجاهل من غير رد
  }
  let reply;
  try {
    reply = await runCommand(msg.text.trim(), env, new Date());
  } catch (e) {
    reply = errorReply(e);
    if (!(e instanceof UserError)) console.error("command failed:", e && e.message);
  }
  if (reply) await tg(env, "sendMessage", { chat_id: secret(env.OWNER_CHAT_ID), text: reply, parse_mode: "HTML",
                                             disable_web_page_preview: true });
}

// رد قصير بالسبب، من غير أي توكن أو سر
export function errorReply(e) {
  if (e instanceof UserError) return `⚠️ ${e.message}`;
  if (e instanceof GitHubError) {
    return `❌ GitHub رفض الطلب ${e.status}${e.detail ? `: ${esc(e.detail)}` : ""}`;
  }
  return `❌ حصلت مشكلة: ${esc(String((e && e.message) || e).slice(0, 120))}`;
}

// ================== الأوامر ==================
const HELP = [
  "🤖 <b>أوامر البوت</b>",
  "/status — حالة البوت",
  "/run — شغّل البوت دلوقتي",
  "/add كلمة — ضيف كلمة بحث (إنجليزي)",
  "/remove كلمة — شيل كلمة بحث",
  "/discount 45 — أقل خصم % (أو reset)",
  "/score 50 — أقل سكور (أو reset)",
  "/mute 3h — اكتم الخاص (30m أو 3h أو 1d أو off)",
  "/watch لينك السعر — تابع منتج لحد ما يوصل للسعر أو يرجع المخزن",
  "/unwatch لينك أو ASIN — بطّل متابعة",
  "/list — المتابعة والمكتوم والكلمات",
].join("\n");

export async function runCommand(text, env, now) {
  const [head, ...rest] = text.split(/\s+/);
  const cmd = head.toLowerCase().replace(/@\w+$/, "");
  const arg = rest.join(" ").trim();
  switch (cmd) {
    case "/start":
    case "/help":
      return HELP;
    case "/add":
      return cmdAdd(arg, env);
    case "/remove":
      return cmdRemove(arg, env);
    case "/discount":
      return cmdNumber(arg, env, "min_discount", 1, 95, "أقل خصم", "%");
    case "/score":
      return cmdNumber(arg, env, "min_score", 0, 100, "أقل سكور", "");
    case "/mute":
      return cmdMute(arg, env, now);
    case "/status":
      return cmdStatus(env, now);
    case "/run":
      return cmdRun(env, now);
    case "/watch":
      return cmdWatch(arg, env, now);
    case "/unwatch":
      return cmdUnwatch(arg, env);
    case "/list":
      return cmdList(env);
    default:
      return `مش فاهم الأمر ده 🤔\n\n${HELP}`;
  }
}

function keyword(arg) {
  const k = arg.toLowerCase().replace(/\s+/g, " ").trim();
  if (k.length < 2 || k.length > 60) throw new UserError("اكتب الكلمة بعد الأمر، مثلًا: /add air purifier");
  return k;
}

async function cmdAdd(arg, env) {
  const k = keyword(arg);
  await updateConfig(env, `config: /add ${k}`, (cfg) => {
    cfg.removed_keywords = (cfg.removed_keywords || []).filter((x) => x !== k);
    cfg.extra_keywords = cfg.extra_keywords || [];
    if (!cfg.extra_keywords.includes(k)) cfg.extra_keywords.push(k);
  });
  return `✅ ضفت «${esc(k)}» لكلمات البحث، وهتدخل الدور من التشغيلة الجاية.`;
}

async function cmdRemove(arg, env) {
  const k = keyword(arg);
  await updateConfig(env, `config: /remove ${k}`, (cfg) => {
    const extra = cfg.extra_keywords || [];
    if (extra.includes(k)) {
      cfg.extra_keywords = extra.filter((x) => x !== k);
    } else {
      cfg.removed_keywords = cfg.removed_keywords || [];
      if (!cfg.removed_keywords.includes(k)) cfg.removed_keywords.push(k);
    }
  });
  return `🗑️ شلت «${esc(k)}» من البحث (لو كانت موجودة).`;
}

async function cmdNumber(arg, env, field, min, max, label, unit) {
  if (/^(reset|off|default)$/i.test(arg)) {
    await updateConfig(env, `config: ${field} reset`, (cfg) => { cfg[field] = null; });
    return `↩️ ${label} رجع للافتراضي.`;
  }
  const n = parseNumber(arg);
  if (n == null || !Number.isInteger(n) || n < min || n > max) {
    throw new UserError(`اكتب رقم صحيح من ${min} لـ ${max}، أو reset.`);
  }
  await updateConfig(env, `config: ${field} ${n}`, (cfg) => { cfg[field] = n; });
  return `✅ ${label} بقى ${n}${unit} من التشغيلة الجاية.`;
}

async function cmdMute(arg, env, now) {
  if (/^(off|0|stop)$/i.test(arg)) {
    await updateConfig(env, "config: unmute", (cfg) => { cfg.mute_until = null; });
    return "🔔 الكتم اتشال، اللقطات هترجعلك عادي.";
  }
  const m = (arg || "3h").toLowerCase().match(/^(\d+)\s*(m|h|d|د|س|ي)?$/);
  if (!m) throw new UserError("اكتب المدة كده: /mute 30m أو /mute 3h أو /mute 1d أو /mute off");
  const unit = { m: 60e3, "د": 60e3, h: 3600e3, "س": 3600e3, d: 86400e3, "ي": 86400e3 }[m[2] || "h"];
  const ms = Number(m[1]) * unit;
  if (ms < 60e3 || ms > 7 * 86400e3) throw new UserError("المدة لازم تبقى من دقيقة لحد 7 أيام.");
  const until = new Date(now.getTime() + ms);
  await updateConfig(env, "config: mute", (cfg) => { cfg.mute_until = until.toISOString(); });
  return `🔇 هسكت في الخاص لحد ${cairo(until)}.\nالقناة شغالة عادي، واللقطات هتجيلك في ملخص بعد الكتم.`;
}

async function cmdStatus(env, now) {
  const [cfg, state, run] = await Promise.all([readJson(env, CONFIG_PATH), readJson(env, STATE_PATH),
                                               lastRun(env)]);
  const meta = (state && state._meta) || {};
  const lines = ["📊 <b>حالة البوت</b>"];
  if (run) {
    const icon = run.status !== "completed" ? "⏳ شغالة" : run.conclusion === "success" ? "✅ نجحت" : `❌ ${run.conclusion}`;
    lines.push(`🕐 آخر تشغيلة: ${cairo(new Date(run.created_at))} (${icon}، ${run.event === "schedule" ? "GitHub" : "يدوي/Worker"})`);
  }
  lines.push(`🔎 آخر بحث كامل: ${meta.last_full_run ? `${cairo(new Date(meta.last_full_run))} (${ago(meta.last_full_run, now)})` : "لسه"}`);
  const b = meta.amazon_backoff;
  lines.push(b && new Date(b.until) > now
    ? `🛑 أمازون: في فترة راحة لحد ${cairo(new Date(b.until))} (مرة رقم ${b.level})`
    : "🟢 أمازون: طبيعي");
  lines.push(`🌙 مستني في الملخص: ${Object.keys(meta.queue || {}).length} لقطة`);
  const mute = cfg.mute_until && new Date(cfg.mute_until) > now ? `لحد ${cairo(new Date(cfg.mute_until))}` : "لأ";
  lines.push(`🔇 الخاص مكتوم: ${mute}`);
  lines.push(`⚙️ أقل خصم: ${cfg.min_discount != null ? `${cfg.min_discount}%` : "الافتراضي"} | أقل سكور: ${cfg.min_score ?? "الافتراضي"}`);
  lines.push(`🔤 كلمات زيادة: ${(cfg.extra_keywords || []).length} | متشالة: ${(cfg.removed_keywords || []).length}`);
  lines.push(`👀 متابعة: ${Object.keys(cfg.watch || {}).length} | 🔕 مكتوم: ${(cfg.muted_asins || []).length}`);
  if (env.DASHBOARD_URL) lines.push(`🖥️ ${env.DASHBOARD_URL}`);
  return lines.join("\n");
}

async function cmdRun(env, now) {
  const state = await readJson(env, STATE_PATH);
  const last = state && state._meta && state._meta.last_full_run;
  await dispatch(env);
  const mins = last ? Math.floor((now - new Date(last)) / 60e3) : null;
  const note = mins != null && mins < FULL_RUN_GAP_MIN
    ? `\n⏭️ بس آخر بحث كامل كان من ${mins} دقيقة، فالتشغيلة دي مش هتكلم أمازون (هتبعت الملخص والتقارير بس).`
    : "";
  return `🚀 شغّلت البوت دلوقتي، النتيجة في خلال 5 دقايق تقريبًا.${note}`;
}

async function cmdWatch(arg, env, now) {
  const parts = arg.split(/\s+/).filter(Boolean);
  if (parts.length < 2) throw new UserError("اكتب كده: /watch لينك-المنتج السعر\nمثلًا: /watch https://www.amazon.eg/dp/B0XXXXXXXX 1500");
  const target = parseNumber(parts[parts.length - 1]);
  if (!target || target <= 0) throw new UserError("السعر اللي عايزه لازم يبقى رقم، مثلًا 1500.");
  const asin = await extractAsin(parts.slice(0, -1).join(" "));
  if (!asin) throw new UserError("مش لاقي رقم المنتج (ASIN) في اللينك ده.");
  await updateConfig(env, `config: /watch ${asin} ${target}`, (cfg) => {
    cfg.watch = cfg.watch || {};
    if (!cfg.watch[asin] && Object.keys(cfg.watch).length >= 50) throw new UserError("قايمة المتابعة فيها 50 منتج، شيل حاجة الأول.");
    cfg.watch[asin] = { target, url: `https://www.amazon.eg/dp/${asin}`, added: now.toISOString() };
  });
  return `👀 هتابعلك ${asin}: هقولك لما يوصل ${target.toLocaleString("en-US")} ج.م أو أقل، أو لو رجع المخزن.\n` +
         "(البوت بيفحص لحد 5 منتجات من المتابعة كل تشغيلة)";
}

async function cmdUnwatch(arg, env) {
  const asin = await extractAsin(arg);
  if (!asin) throw new UserError("اكتب كده: /unwatch لينك-المنتج أو الـ ASIN");
  let existed = false;
  await updateConfig(env, `config: /unwatch ${asin}`, (cfg) => {
    existed = Boolean(cfg.watch && cfg.watch[asin]);
    if (existed) delete cfg.watch[asin];
    else throw new UserError(`${asin} مش في قايمة المتابعة.`);
  });
  return `✅ بطّلت أتابع ${asin}.`;
}

async function cmdList(env) {
  const cfg = await readJson(env, CONFIG_PATH);
  const watch = Object.entries(cfg.watch || {});
  const lines = [`👀 <b>المتابعة (${watch.length})</b>`];
  for (const [asin, w] of watch) lines.push(`• <a href="https://www.amazon.eg/dp/${asin}">${asin}</a> ← ${Number(w.target).toLocaleString("en-US")} ج.م`);
  if (!watch.length) lines.push("مفيش (ضيف بـ /watch)");
  const muted = cfg.muted_asins || [];
  lines.push("", `🔕 <b>مكتوم (${muted.length})</b>`, muted.length ? muted.slice(-15).join("، ") : "مفيش");
  lines.push("", `🔤 <b>كلمات زيادة:</b> ${(cfg.extra_keywords || []).map(esc).join("، ") || "مفيش"}`);
  lines.push(`🗑️ <b>كلمات متشالة:</b> ${(cfg.removed_keywords || []).map(esc).join("، ") || "مفيش"}`);
  return lines.join("\n");
}

// ================== زرار 🔕 ==================
async function handleCallback(cq, env) {
  if (!isOwner(env, cq.from && cq.from.id)) {
    return tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  }
  const m = /^mute:([A-Z0-9]{10})$/.exec(cq.data || "");
  if (!m) return tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "زرار قديم" });
  const asin = m[1];
  await updateConfig(env, `config: mute ${asin}`, (cfg) => {
    cfg.muted_asins = cfg.muted_asins || [];
    if (!cfg.muted_asins.includes(asin)) cfg.muted_asins.push(asin);
    cfg.muted_asins = cfg.muted_asins.slice(-500);
  });
  await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "🔕 تمام، مش هبعتلك عنه تاني" });
  const msg = cq.message;
  if (msg) {
    // نشيل زرار الكتم ونسيب زراير اللينكات
    const rows = ((msg.reply_markup && msg.reply_markup.inline_keyboard) || [])
      .map((row) => row.filter((b) => !b.callback_data)).filter((row) => row.length);
    await tg(env, "editMessageReplyMarkup", { chat_id: msg.chat.id, message_id: msg.message_id,
                                               reply_markup: { inline_keyboard: rows } });
  }
}

// ================== Cron ==================
export async function handleCron(cron, env, when) {
  if (MORNING_CRONS.has(cron) && cairoHour(when) !== 8) {
    console.log(`cron ${cron}: مش 8 الصبح في القاهرة (${cairoHour(when)})، تخطّي`);
    return "skipped";
  }
  try {
    await dispatch(env);
    console.log(`cron ${cron}: شغّلت الـ workflow`);
    return "dispatched";
  } catch (e) {
    console.error(`cron ${cron}: فشل تشغيل الـ workflow: ${e.message}`);
    await tg(env, "sendMessage", { chat_id: secret(env.OWNER_CHAT_ID), disable_notification: true,
                                   text: `⚠️ تنبيه إداري: الـ Worker مقدرش يشغّل البوت (${e.message}). GitHub schedule شغال كاحتياطي.` });
    return "failed";
  }
}

export function cairoHour(date) {
  return Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Cairo", hour: "numeric", hourCycle: "h23" }).format(date));
}

// ================== GitHub ==================
// الأسرار ممكن تتلزق بمسافة أو سطر جديد في الآخر (خصوصًا من PowerShell)، وده بيبوّظ الـ header
const secret = (v) => String(v == null ? "" : v).trim();

export function githubHeaders(env, extra = {}) {
  return {
    Authorization: `Bearer ${secret(env.GITHUB_TOKEN)}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "loata-bot-worker", // GitHub بيرفض أي طلب من غير User-Agent، والـ Worker مبيحطوش لوحده
    ...extra,
  };
}

async function gh(env, path, init = {}) {
  return fetch(`${GH}/repos/${env.GITHUB_REPO}${path}`, { ...init, headers: githubHeaders(env, init.headers) });
}

async function readFile(env, path) {
  const res = await gh(env, `/contents/${path}?ref=main`);
  if (res.status === 404) return { text: null, sha: null };
  await ghOk(res, `read ${path}`);
  const data = await res.json();
  if (data.content) return { text: fromBase64(data.content), sha: data.sha };
  // ملف كبير (أكبر من 1MB): الـ API مبيرجّعش content، فبنجيبه raw
  const raw = await ghOk(await gh(env, `/contents/${path}?ref=main`,
                                  { headers: { Accept: "application/vnd.github.raw+json" } }), `raw ${path}`);
  return { text: await raw.text(), sha: data.sha };
}

export async function githubHealth(env) {
  const out = { token_set: Boolean(secret(env.GITHUB_TOKEN)) };
  for (const [name, path] of [["repo", ""], ["contents", `/contents/${CONFIG_PATH}?ref=main`],
                              ["actions", `/actions/workflows/${env.WORKFLOW_FILE}`]]) {
    try {
      const res = await gh(env, path);
      out[name] = res.ok ? res.status : { status: res.status, error: await githubDetail(res) };
    } catch (e) {
      out[name] = { error: `${e.name}: ${String(e.message).slice(0, 120)}` };
    }
  }
  return out;
}

async function readJson(env, path) {
  const { text } = await readFile(env, path);
  return text ? JSON.parse(text) : {};
}

export async function updateConfig(env, message, mutate) {
  // لو حد تاني (البوت أو أمر تاني) عدّل في نفس الوقت، GitHub بيرد 409 فبنعيد على آخر نسخة
  for (let attempt = 1; attempt <= 3; attempt++) {
    const { text, sha } = await readFile(env, CONFIG_PATH);
    const cfg = text ? JSON.parse(text) : {};
    mutate(cfg);
    const body = { message, content: toBase64(JSON.stringify(cfg, null, 2) + "\n"), branch: "main" };
    if (sha) body.sha = sha;
    const res = await gh(env, `/contents/${CONFIG_PATH}`, { method: "PUT", body: JSON.stringify(body),
                                                             headers: { "Content-Type": "application/json" } });
    if (res.ok) return cfg;
    if (res.status !== 409 && res.status !== 422) await ghOk(res, "write config");
  }
  throw new GitHubError("write config", 409, "conflict");
}

export async function dispatch(env) {
  const res = await gh(env, `/actions/workflows/${env.WORKFLOW_FILE}/dispatches`, {
    method: "POST", body: JSON.stringify({ ref: "main" }), headers: { "Content-Type": "application/json" },
  });
  if (res.status !== 204) throw new GitHubError("dispatch", res.status, await githubDetail(res));
}

async function lastRun(env) {
  const res = await ghOk(await gh(env, `/actions/workflows/${env.WORKFLOW_FILE}/runs?per_page=1`), "runs");
  const data = await res.json();
  return (data.workflow_runs || [])[0] || null;
}

// ================== Telegram ==================
async function tg(env, method, payload) {
  const res = await fetch(`${TG}/bot${secret(env.TG_TOKEN)}/${method}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!data.ok) console.error(`telegram ${method} failed: ${data.description || res.status}`);
  return data;
}

export async function ensureWebhook(env, origin) {
  const hook = await tg(env, "setWebhook", {
    url: `${origin}/telegram`, secret_token: secret(env.TG_WEBHOOK_SECRET),
    allowed_updates: ["message", "callback_query"], drop_pending_updates: true,
  });
  const commands = await tg(env, "setMyCommands", {
    commands: [
      ["status", "حالة البوت"], ["run", "شغّل البوت دلوقتي"], ["add", "ضيف كلمة بحث"], ["remove", "شيل كلمة بحث"],
      ["discount", "أقل خصم %"], ["score", "أقل سكور"], ["mute", "اكتم الخاص (3h)"], ["watch", "تابع منتج"],
      ["unwatch", "بطّل متابعة"], ["list", "المتابعة والمكتوم"], ["help", "الأوامر"],
    ].map(([command, description]) => ({ command, description })),
    scope: { type: "chat", chat_id: Number(secret(env.OWNER_CHAT_ID)) },
  });
  return { webhook: Boolean(hook.ok), webhook_url: `${origin}/telegram`, commands: Boolean(commands.ok),
           error: hook.ok ? undefined : hook.description };
}

// ================== أدوات ==================
const AR_DIGITS = { "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9", "٫": ".", "٬": "," };

export function parseNumber(s) {
  const t = String(s || "").replace(/[٠-٩٫٬]/g, (c) => AR_DIGITS[c]).replace(/,/g, "");
  return /^\d+(\.\d+)?$/.test(t) ? Number(t) : null;
}

export async function extractAsin(input) {
  const s = String(input || "").trim();
  if (ASIN_RE.test(s.toUpperCase())) return s.toUpperCase();
  const m = s.match(/(?:\/dp\/|\/gp\/product\/|\/gp\/aw\/d\/|\/product\/)([A-Z0-9]{10})/i);
  if (m) return m[1].toUpperCase();
  // لينكات مختصرة (amzn.eu/d/... أو a.co): بنشوف بتودّي فين من غير ما نفتح صفحة أمازون نفسها
  if (/^https?:\/\/(amzn\.(eu|to)|a\.co)\//i.test(s)) {
    const res = await fetch(s, { method: "HEAD", redirect: "manual" });
    const loc = res.headers.get("location");
    return loc && loc !== s ? extractAsin(loc) : null;
  }
  return null;
}

function cairo(date) {
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", { timeZone: "Africa/Cairo", day: "numeric", month: "short",
                                                      hour: "numeric", minute: "2-digit" }).format(date);
}

function ago(iso, now) {
  const mins = Math.max(0, Math.round((now - new Date(iso)) / 60e3));
  return mins < 60 ? `من ${mins} دقيقة` : `من ${Math.round(mins / 60)} ساعة`;
}

const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

function toBase64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function fromBase64(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
