// Cloudflare Worker لبوت لقطات أمازون مصر:
//  1) Telegram webhook: لوحة تحكم بالزراير + أوامر (لصاحب البوت بس)، وبتعدّل config.json عن طريق GitHub API
//  2) Cron: بيشغّل الـ workflow في ميعاده (GitHub schedule بيتأخر)، و08:05 القاهرة عشان ملخص الصبح
// الأسرار: TG_TOKEN، OWNER_CHAT_ID، GITHUB_TOKEN، TG_WEBHOOK_SECRET (wrangler secret put)

const GH = "https://api.github.com";
const TG = "https://api.telegram.org";
const CONFIG_PATH = "config.json";
const STATE_PATH = "deals_state.json";
const SCRIPT_PATH = "amazon_eg_deals.py";
const HISTORY_PATH = "data/history.json";
const DASHBOARD_DATA_PATH = "docs/deals.json";
const MORNING_CRONS = new Set(["5 5 * * *", "5 6 * * *"]);
const FULL_RUN_GAP_MIN = 120; // نفس MIN_HOURS_BETWEEN_FULL_RUNS في البوت
const ASIN_RE = /^[A-Z0-9]{10}$/;
const DISCOUNT_CHOICES = [40, 45, 50, 55, 60];
const SCORE_CHOICES = [40, 50, 60, 70];
const KW_PROMPT = "🔍 اكتب كلمة البحث اللي عايز تضيفها (إنجليزي):";
const PRICE_PROMPT = "✏️ اكتب السعر اللي عايزه للمنتج ده";
const AMAZON_LINK_RE = /(?:https?:\/\/)?(?:[\w-]+\.)?(?:amazon\.eg|amzn\.eu|amzn\.to|a\.co)\/\S+/i;

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
  const now = new Date();
  let out;
  try {
    out = await routeMessage(msg, env, now);
  } catch (e) {
    out = { text: errorReply(e) };
    if (!(e instanceof UserError)) console.error("message failed:", e && e.message);
  }
  if (out && out.text) {
    await tg(env, "sendMessage", { chat_id: secret(env.OWNER_CHAT_ID), text: out.text, parse_mode: "HTML",
                                   disable_web_page_preview: true,
                                   ...(out.keyboard ? { reply_markup: { inline_keyboard: out.keyboard } } : {}),
                                   ...(out.markup ? { reply_markup: out.markup } : {}) });
  }
}

// رسالة عادية: رد على سؤال (force_reply)، أو أمر، أو لينك أمازون لوحده
async function routeMessage(msg, env, now) {
  const text = msg.text.trim();
  const asked = (msg.reply_to_message && msg.reply_to_message.text) || "";
  if (asked.startsWith(KW_PROMPT)) {
    return { text: await cmdAdd(text, env), keyboard: [[btn("🔍 كلمات البحث", "p:kw"), btn("⬅️ القايمة", "p:main")]] };
  }
  if (asked.startsWith(PRICE_PROMPT)) {
    const asin = (asked.match(/\(([A-Z0-9]{10})\)/) || [])[1];
    const target = parseNumber(text.replace(/ج\.?م|جنيه|egp/gi, "").trim());
    if (!asin) throw new UserError("مش لاقي المنتج، ابعت اللينك تاني.");
    if (!target) throw new UserError("اكتب السعر رقم بس، مثلًا 1500.");
    return saveWatch(env, asin, { target }, now);
  }
  if (text.startsWith("/")) {
    const cmd = text.split(/\s+/)[0].toLowerCase().replace(/@\w+$/, "");
    if (cmd === "/start" || cmd === "/menu") return renderScreen("main", await screenData(env, "main"), now);
    return { text: await runCommand(text, env, now) };
  }
  const link = text.match(AMAZON_LINK_RE);
  if (link) return linkReply(env, link[0], now);
  return { text: "ابعت /menu عشان لوحة التحكم، أو ابعتلي لينك أي منتج من أمازون وأنا أتابعهولك.",
           keyboard: [[btn("🎛️ لوحة التحكم", "p:main")]] };
}

// رد قصير بالسبب، من غير أي توكن أو سر
export function errorReply(e) {
  if (e instanceof UserError) return `⚠️ ${e.message}`;
  if (e instanceof GitHubError) {
    return `❌ GitHub رفض الطلب ${e.status}${e.detail ? `: ${esc(e.detail)}` : ""}`;
  }
  return `❌ حصلت مشكلة: ${esc(String((e && e.message) || e).slice(0, 120))}`;
}

// ================== عدد بالعربي ==================
// المفرد، المثنى، الجمع (3-10). 11 وأكتر بيرجع للمفرد: "11 ساعة"
const AR_WORDS = {
  "ساعة": ["ساعة", "ساعتين", "ساعات", "واحدة"], "دقيقة": ["دقيقة", "دقيقتين", "دقايق", "واحدة"],
  "يوم": ["يوم", "يومين", "أيام", "واحد"], "لقطة": ["لقطة", "لقطتين", "لقطات", "واحدة"],
  "منتج": ["منتج", "منتجين", "منتجات", "واحد"], "كلمة": ["كلمة", "كلمتين", "كلمات", "واحدة"],
};

// explicitOne: بعد ":" أحسن نقول "لقطة واحدة" بدل "لقطة" لوحدها
export function arCount(n, word, explicitOne = false) {
  const [one, two, many, oneWord] = AR_WORDS[word];
  n = Math.trunc(Number(n));
  if (n === 1) return explicitOne ? `${one} ${oneWord}` : one;
  if (n === 2) return two;
  const r = n % 100;
  return `${n} ${r >= 3 && r <= 10 ? many : one}`;
}

export function duration(ms) {
  const mins = Math.max(1, Math.round(ms / 60e3));
  if (mins < 60) return arCount(mins, "دقيقة");
  const hours = Math.round(mins / 60);
  if (hours < 24) return arCount(hours, "ساعة");
  return arCount(Math.round(hours / 24), "يوم");
}

function ago(iso, now) {
  const ms = now - new Date(iso);
  return ms < 60e3 ? "دلوقتي" : `من ${duration(ms)}`;
}

// ================== الأوامر ==================
const HELP = [
  "🤖 <b>أوامر البوت</b>",
  "/menu — لوحة التحكم بالزراير",
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
  "",
  "💡 أو ابعت لينك أي منتج من أمازون لوحده وأنا أسألك تتابعه بكام.",
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

function muteUntil(cfg, now, ms) {
  return new Date(now.getTime() + ms).toISOString();
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
  const until = muteUntil(null, now, ms);
  await updateConfig(env, "config: mute", (cfg) => { cfg.mute_until = until; });
  return `🔇 هسكت في الخاص لحد ${cairo(new Date(until))}.\nالقناة شغالة عادي، واللقطات هتجيلك في ملخص بعد الكتم.`;
}

function statusText(cfg, state, run, now) {
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
  lines.push(`🌙 مستني في الملخص: ${arCount(Object.keys(meta.queue || {}).length, "لقطة", true)}`);
  const left = muteLeft(cfg, now);
  lines.push(`🔇 الخاص مكتوم: ${left ? `أيوه، فاضل ${left}` : "لأ"}`);
  lines.push(`⚙️ أقل خصم: ${cfg.min_discount != null ? `${cfg.min_discount}%` : "الافتراضي"} | أقل سكور: ${cfg.min_score ?? "الافتراضي"}`);
  lines.push(`🔤 كلمات زيادة: ${(cfg.extra_keywords || []).length} | متشالة: ${(cfg.removed_keywords || []).length} | أقسام مقفولة: ${(cfg.disabled_categories || []).length}`);
  lines.push(`👀 متابعة: ${Object.keys(cfg.watch || {}).length} | 🔕 مكتوم: ${(cfg.muted_asins || []).length}`);
  return lines.join("\n");
}

async function cmdStatus(env, now) {
  const [cfg, state, run] = await Promise.all([readJson(env, CONFIG_PATH), readJson(env, STATE_PATH), lastRun(env)]);
  return statusText(cfg, state, run, now) + (env.DASHBOARD_URL ? `\n🖥️ ${env.DASHBOARD_URL}` : "");
}

function recentFullRun(state, now) {
  const last = state && state._meta && state._meta.last_full_run;
  return last && now - new Date(last) < FULL_RUN_GAP_MIN * 60e3 ? last : null;
}

async function cmdRun(env, now) {
  const state = await readJson(env, STATE_PATH);
  await dispatch(env);
  const recent = recentFullRun(state, now);
  const note = recent
    ? `\n⏭️ بس آخر بحث كامل كان ${ago(recent, now)}، فالتشغيلة دي مش هتكلم أمازون (هتبعت الملخص والتقارير بس).`
    : "";
  return `🚀 شغّلت البوت دلوقتي، النتيجة في خلال 5 دقايق تقريبًا.${note}`;
}

async function cmdWatch(arg, env, now) {
  const parts = arg.split(/\s+/).filter(Boolean);
  if (parts.length < 2) throw new UserError("اكتب كده: /watch لينك-المنتج السعر\nمثلًا: /watch https://www.amazon.eg/dp/B0XXXXXXXX 1500\n💡 أو ابعت اللينك لوحده وأنا أسألك.");
  const target = parseNumber(parts[parts.length - 1]);
  if (!target || target <= 0) throw new UserError("السعر اللي عايزه لازم يبقى رقم، مثلًا 1500.");
  const asin = await extractAsin(parts.slice(0, -1).join(" "));
  if (!asin) throw new UserError("مش لاقي رقم المنتج (ASIN) في اللينك ده.");
  await saveWatch(env, asin, { target }, now);
  return `👀 هتابعلك ${asin}: هقولك لما يوصل ${money(target)} أو أقل، أو لو رجع المخزن.\n` +
         "(البوت بيفحص لحد 5 منتجات من المتابعة كل تشغيلة)";
}

async function cmdUnwatch(arg, env) {
  const asin = await extractAsin(arg);
  if (!asin) throw new UserError("اكتب كده: /unwatch لينك-المنتج أو الـ ASIN");
  await removeWatch(env, asin);
  return `✅ بطّلت أتابع ${asin}.`;
}

async function cmdList(env) {
  const cfg = await readJson(env, CONFIG_PATH);
  const watch = Object.entries(cfg.watch || {});
  const lines = [`👀 <b>المتابعة (${watch.length})</b>`];
  for (const [asin, w] of watch) {
    lines.push(`• <a href="https://www.amazon.eg/dp/${asin}">${esc(short(w.title || asin, 40))}</a> ← ${w.target ? money(w.target) : "📦 لما يرجع المخزن"}`);
  }
  if (!watch.length) lines.push("مفيش (ابعتلي لينك أي منتج)");
  const muted = cfg.muted_asins || [];
  lines.push("", `🔕 <b>مكتوم (${muted.length})</b>`, muted.length ? muted.slice(-15).join("، ") : "مفيش");
  lines.push("", `🔤 <b>كلمات زيادة:</b> ${(cfg.extra_keywords || []).map(esc).join("، ") || "مفيش"}`);
  lines.push(`🗑️ <b>كلمات متشالة:</b> ${(cfg.removed_keywords || []).map(esc).join("، ") || "مفيش"}`);
  lines.push(`🗂️ <b>أقسام مقفولة:</b> ${(cfg.disabled_categories || []).map(esc).join("، ") || "مفيش"}`);
  return lines.join("\n");
}

// ================== المتابعة ==================
async function saveWatch(env, asin, { target = null, stockOnly = false }, now) {
  const info = await lookupProduct(env, asin, { history: false });
  await updateConfig(env, `config: watch ${asin} ${stockOnly ? "stock" : target}`, (cfg) => {
    cfg.watch = cfg.watch || {};
    if (!cfg.watch[asin] && Object.keys(cfg.watch).length >= 50) throw new UserError("قايمة المتابعة فيها 50 منتج، شيل حاجة الأول.");
    cfg.watch[asin] = { target: stockOnly ? null : target, ...(stockOnly ? { stock_only: true } : {}),
                        url: `https://www.amazon.eg/dp/${asin}`, added: now.toISOString(),
                        ...(info.title ? { title: info.title.slice(0, 120) } : {}) };
  });
  const name = esc(short(info.title || asin, 60));
  const what = stockOnly ? "📦 هقولك أول ما يرجع المخزن." : `🎯 هقولك لما يوصل ${money(target)} أو أقل، أو لو رجع المخزن.`;
  return { text: `👀 تمام، هتابعلك «${name}»\n${what}`,
           keyboard: [[btn("👁️ المتابعة", "p:watch"), btn("⬅️ القايمة", "p:main")]] };
}

async function removeWatch(env, asin) {
  await updateConfig(env, `config: unwatch ${asin}`, (cfg) => {
    if (!cfg.watch || !cfg.watch[asin]) throw new UserError(`${asin} مش في قايمة المتابعة.`);
    delete cfg.watch[asin];
  });
}

// اللي البوت يعرفه عن منتج من غير ما نكلم أمازون: العنوان، آخر سعر شافه، وأقل سعر
export async function lookupProduct(env, asin, { history = true } = {}) {
  const [dash, state, hist, cfg] = await Promise.all([
    readJson(env, DASHBOARD_DATA_PATH).catch(() => ({})), history ? readJson(env, STATE_PATH) : {},
    history ? readJson(env, HISTORY_PATH).catch(() => ({})) : {}, readJson(env, CONFIG_PATH)]);
  const deal = (dash.deals || []).find((d) => d.asin === asin);
  const points = hist[asin] || [];
  const last = points[points.length - 1];
  const rec = state[asin] || {};
  return {
    title: (deal && deal.title) || (cfg.watch && cfg.watch[asin] && cfg.watch[asin].title) || null,
    price: last ? last[1] : deal ? deal.price : null,
    priceAt: last ? last[0] : null,
    lowest: rec.lowest || null,
    watching: cfg.watch && cfg.watch[asin],
  };
}

async function linkReply(env, link, now) {
  const asin = await extractAsin(link);
  if (!asin) throw new UserError("مش لاقي رقم المنتج (ASIN) في اللينك ده.");
  const p = await lookupProduct(env, asin);
  const lines = [`🔎 <b>${esc(short(p.title || asin, 120))}</b>`];
  if (p.price) lines.push(`💰 آخر سعر شفناه: ${money(p.price)}${p.priceAt ? ` (${ago(p.priceAt, now)})` : ""}`);
  else lines.push("💰 لسه مشفناش المنتج ده في البحث، فمعرفش سعره دلوقتي.");
  if (p.lowest) lines.push(`📉 أقل سعر شفناه: ${money(p.lowest)}`);
  if (p.watching) lines.push(`👀 متابَع دلوقتي ${p.watching.target ? `لحد ${money(p.watching.target)}` : "لما يرجع المخزن"}`);
  lines.push("", "عايز أتابعه لحد أنهي سعر؟");
  const kb = [];
  if (p.lowest) kb.push([btn(`📉 أقل سعر شفناه ${num(p.lowest)}`, `w:t:${asin}:${Math.round(p.lowest)}`)]);
  const base = p.price || p.lowest;
  if (base) {
    kb.push([btn(`-10% ← ${num(base * 0.9)}`, `w:t:${asin}:${Math.round(base * 0.9)}`),
             btn(`-20% ← ${num(base * 0.8)}`, `w:t:${asin}:${Math.round(base * 0.8)}`)]);
  }
  kb.push([btn("✏️ سعر تاني", `w:p:${asin}`), btn("📦 لما يرجع المخزن بس", `w:s:${asin}`)]);
  return { text: lines.join("\n"), keyboard: kb };
}

// ================== لوحة التحكم ==================
const btn = (text, data) => ({ text, callback_data: data });
const BACK = [btn("⬅️ رجوع", "p:main")];
const mark = (on, label) => `${on ? "✅ " : ""}${label}`;

function muteLeft(cfg, now) {
  const until = cfg && cfg.mute_until ? new Date(cfg.mute_until) : null;
  return until && until > now ? duration(until - now) : null;
}

export function statusLine(cfg, state, now) {
  const meta = (state && state._meta) || {};
  const b = meta.amazon_backoff;
  const amazon = b && new Date(b.until) > now ? `🛑 أمازون مرتاح (فاضل ${duration(new Date(b.until) - now)})` : "🟢 أمازون طبيعي";
  const last = meta.last_full_run ? `آخر بحث ${ago(meta.last_full_run, now)}` : "لسه مفيش بحث";
  const left = muteLeft(cfg, now);
  return [amazon, last, left ? `🔇 مكتوم (فاضل ${left})` : "🔔 مش مكتوم"].join(" · ");
}

// كل شاشة = نص + زراير، من غير أي طلبات (عشان تتختبر لوحدها)
export function renderScreen(name, data, now) {
  const { cfg = {}, state = {}, script = null, run = null, titles = {} } = data;
  const defaults = (script && script.defaults) || {};
  switch (name) {
    case "main": {
      const watching = Object.keys(cfg.watch || {}).length;
      return {
        text: `🎛️ <b>لوحة التحكم</b>\n${statusLine(cfg, state, now)}`,
        keyboard: [
          [btn("📊 الحالة", "p:status"), btn("▶️ شغّل دلوقتي", "p:run")],
          [btn("🎯 الخصم", "p:disc"), btn("⭐ السكور", "p:score")],
          [btn("🗂️ الأقسام", "p:cats"), btn(`👁️ المتابعة (${watching})`, "p:watch")],
          [btn("🔕 كتم", "p:mute"), btn("🔍 كلمات البحث", "p:kw")],
          [btn(`🔕 منتجات مكتومة (${(cfg.muted_asins || []).length})`, "p:muted")],
        ],
      };
    }
    case "muted": {
      const list = cfg.muted_asins || [];
      const info = cfg.muted_info || {};
      if (!list.length) {
        return { text: "🔕 <b>منتجات مكتومة</b>\nمفيش منتجات مكتومة.\nدوس 🔕 تحت أي لقطة عشان متجيلكش تاني.", keyboard: [BACK] };
      }
      const shown = list.slice(-30).reverse(); // الأحدث الأول
      const name = (a) => (info[a] && info[a].title) || titles[a] || a; // القديمة: العنوان من الداشبورد
      const lines = [`🔕 <b>منتجات مكتومة (${list.length})</b>`, "دوس 🔔 رجّعه عشان اللقطات بتاعته ترجعلك."];
      if (list.length > shown.length) lines.push(`(بيظهر آخر ${shown.length})`);
      for (const a of shown) {
        lines.push(`• ${esc(short(name(a), 40))} — ${info[a] && info[a].at ? dayMonth(new Date(info[a].at)) : "من قبل كده"}`);
      }
      return { text: lines.join("\n"), keyboard: [...shown.map((a) => [btn(`🔔 رجّعه: ${short(name(a), 26)}`, `u:${a}`)]), BACK] };
    }
    case "status":
      return { text: statusText(cfg, state, run, now), keyboard: [[btn("🔄 تحديث", "p:status")], BACK] };
    case "disc":
    case "score": {
      const isDisc = name === "disc";
      const field = isDisc ? "min_discount" : "min_score";
      const current = cfg[field] ?? defaults[field];
      const unit = isDisc ? "%" : "";
      const choices = isDisc ? DISCOUNT_CHOICES : SCORE_CHOICES;
      return {
        text: [`${isDisc ? "🎯 <b>أقل خصم</b>" : "⭐ <b>أقل سكور</b>"}`,
               `دلوقتي: ${current ?? "؟"}${unit}${cfg[field] == null ? " (الافتراضي)" : ""}`,
               isDisc ? "اللقطة لازم يبقى خصمها الفعلي (بعد الكوبون) كده أو أكتر." : "اللقطة لازم يبقى سكورها كده أو أكتر عشان تتبعتلك."].join("\n"),
        keyboard: [choices.map((v) => btn(mark(current === v, `${v}${unit}`), `${isDisc ? "d" : "s"}:${v}`)),
                   [btn(mark(cfg[field] == null, `↩️ الافتراضي${defaults[field] != null ? ` (${defaults[field]}${unit})` : ""}`), `${isDisc ? "d" : "s"}:r`)],
                   BACK],
      };
    }
    case "mute": {
      const left = muteLeft(cfg, now);
      return {
        text: ["🔕 <b>كتم الخاص</b>", left ? `🔇 مكتوم، فاضل ${left}` : "🔔 مش مكتوم دلوقتي",
               "وقت الكتم القناة شغالة عادي، واللقطات بتجيلك في ملخص بعده."].join("\n"),
        keyboard: [[btn("30 دقيقة", "m:30m"), btn("3 ساعات", "m:3h"), btn("يوم", "m:1d")],
                   [btn("❌ إلغاء الكتم", "m:off")], BACK],
      };
    }
    case "cats": {
      const cats = (script && script.categories) || [];
      const off = new Set(cfg.disabled_categories || []);
      if (!cats.length) return { text: "🗂️ مقدرتش أقرا الأقسام من السكربت دلوقتي، جرّب تاني.", keyboard: [BACK] };
      return {
        text: ["🗂️ <b>الأقسام</b>", `شغال ${cats.length - off.size} من ${cats.length}. دوس على قسم عشان تقفله أو تفتحه (من التشغيلة الجاية).`].join("\n"),
        keyboard: [...cats.map((c, i) => [btn(`${off.has(c.name) ? "⬜" : "✅"} ${c.name} (${arCount(c.count, "كلمة", true)})`, `c:${i}`)]), BACK],
      };
    }
    case "kw": {
      const extra = (cfg.extra_keywords || []).slice(0, 30);
      return {
        text: ["🔍 <b>كلمات البحث الزيادة</b>",
               extra.length ? `عندك ${arCount(extra.length, "كلمة", true)}. دوس ❌ عشان تشيل واحدة.` : "مفيش كلمات زيادة لسه.",
               "(الكلمات الأساسية بتتحكم فيها من 🗂️ الأقسام)"].join("\n"),
        keyboard: [...extra.map((k, i) => [btn(`❌ ${k}`, `k:x:${i}`)]), [btn("➕ ضيف كلمة", "k:add")], BACK],
      };
    }
    case "watch": {
      const entries = Object.entries(cfg.watch || {});
      const states = (state._meta && state._meta.watch_state) || {};
      if (!entries.length) {
        return { text: "👁️ <b>المتابعة</b>\nمفيش منتجات متابَعة.\nابعتلي لينك أي منتج من أمازون وأنا أتابعهولك.", keyboard: [BACK] };
      }
      const lines = ["👁️ <b>المتابعة</b>"];
      for (const [asin, w] of entries) {
        const st = states[asin] || {};
        const now_ = st.price ? money(st.price) : st.in_stock === false ? "خلصان" : "لسه متفحصش";
        lines.push(`• ${esc(short(w.title || asin, 40))}\n   💰 دلوقتي: ${now_} | ${w.target ? `🎯 هدفك: ${money(w.target)}` : "📦 لما يرجع المخزن"}`);
      }
      return { text: lines.join("\n"),
               keyboard: [...entries.map(([asin, w]) => [btn(`❌ بطّل: ${short(w.title || asin, 28)}`, `w:x:${asin}`)]), BACK] };
    }
    case "run": {
      const recent = recentFullRun(state, now);
      return {
        text: ["▶️ <b>أشغّل البوت دلوقتي؟</b>", recent
          ? `⏭️ آخر بحث كامل كان ${ago(recent, now)}، فالتشغيلة دي هتبعت الملخص والتقارير بس من غير ما تكلم أمازون.`
          : "هيدوّر على أمازون ويبعتلك اللقطات في خلال 5 دقايق تقريبًا."].join("\n"),
        keyboard: [[btn("✅ أيوه شغّل", "r:go"), btn("❌ لأ", "p:main")]],
      };
    }
    default:
      return renderScreen("main", data, now);
  }
}

async function screenData(env, name) {
  const [cfg, state, script, run] = await Promise.all([
    readJson(env, CONFIG_PATH), readJson(env, STATE_PATH),
    ["disc", "score", "cats"].includes(name) ? scriptMeta(env) : null,
    name === "status" ? lastRun(env).catch(() => null) : null,
  ]);
  let titles = {};
  if (name === "muted") {
    const dash = await readJson(env, DASHBOARD_DATA_PATH).catch(() => ({}));
    titles = Object.fromEntries((dash.deals || []).map((d) => [d.asin, d.title]));
  }
  return { cfg, state, script, run, titles };
}

// الأقسام والقيم الافتراضية من السكربت نفسه، عشان يفضل مصدر واحد للحقيقة
export function parseScriptMeta(src) {
  const block = ((src || "").match(/SEARCH_GROUPS = \{([\s\S]*?)\n\}/) || [])[1] || "";
  const clean = block.split("\n").filter((l) => !l.trim().startsWith("#")).join("\n");
  const categories = [...clean.matchAll(/"([^"]+)":\s*\[([^\]]*)\]/g)]
    .map((m) => ({ name: m[1], count: (m[2].match(/"[^"]*"/g) || []).length }));
  const n = (re) => { const m = (src || "").match(re); return m ? Number(m[1]) : null; };
  return { categories, defaults: { min_discount: n(/^MIN_DISCOUNT = (\d+)/m), min_score: n(/^MIN_SCORE = (\d+)/m) } };
}

async function scriptMeta(env) {
  const { text } = await readFile(env, SCRIPT_PATH);
  return parseScriptMeta(text);
}

// ================== الزراير (callback_query) ==================
// كل زرار لازم يطابق واحد من دول بالظبط، غير كده "زرار قديم"
const ROUTES = [
  [/^p:(main|status|disc|score|mute|cats|kw|watch|run|muted)$/, async (m) => ({ screen: m[1] })],
  [/^d:(40|45|50|55|60|r)$/, async (m, env) => {
    const v = m[1] === "r" ? null : Number(m[1]);
    await updateConfig(env, `config: min_discount ${v ?? "reset"}`, (cfg) => { cfg.min_discount = v; });
    return { screen: "disc", toast: "✅ اتحفظ" };
  }],
  [/^s:(40|50|60|70|r)$/, async (m, env) => {
    const v = m[1] === "r" ? null : Number(m[1]);
    await updateConfig(env, `config: min_score ${v ?? "reset"}`, (cfg) => { cfg.min_score = v; });
    return { screen: "score", toast: "✅ اتحفظ" };
  }],
  [/^m:(30m|3h|1d|off)$/, async (m, env, cq, now) => {
    const ms = { "30m": 30 * 60e3, "3h": 3 * 3600e3, "1d": 86400e3 }[m[1]];
    const until = ms ? muteUntil(null, now, ms) : null;
    await updateConfig(env, ms ? "config: mute" : "config: unmute", (cfg) => { cfg.mute_until = until; });
    return { screen: "mute", toast: ms ? `🔇 اتكتم ${duration(ms)}` : "🔔 الكتم اتشال" };
  }],
  [/^c:(\d{1,2})$/, async (m, env) => {
    const cats = (await scriptMeta(env)).categories;
    const cat = cats[Number(m[1])];
    if (!cat) throw new UserError("زرار قديم، افتح الأقسام تاني.");
    let nowOff = false;
    await updateConfig(env, `config: toggle category ${m[1]}`, (cfg) => {
      const off = new Set(cfg.disabled_categories || []);
      if (off.has(cat.name)) off.delete(cat.name); else off.add(cat.name);
      if (off.size >= cats.length) throw new UserError("مينفعش تقفل كل الأقسام.");
      nowOff = off.has(cat.name);
      cfg.disabled_categories = [...off];
    });
    return { screen: "cats", toast: nowOff ? "⬜ القسم اتقفل" : "✅ القسم اتفتح" };
  }],
  [/^k:x:(\d{1,2})$/, async (m, env) => {
    let removed = "";
    await updateConfig(env, `config: remove keyword #${m[1]}`, (cfg) => {
      const extra = cfg.extra_keywords || [];
      removed = extra[Number(m[1])];
      if (!removed) throw new UserError("زرار قديم، افتح كلمات البحث تاني.");
      cfg.extra_keywords = extra.filter((_, i) => i !== Number(m[1]));
    });
    return { screen: "kw", toast: `🗑️ «${short(removed, 30)}» اتشالت` };
  }],
  [/^k:add$/, async () => ({
    toast: "✍️ اكتب الكلمة",
    send: { text: KW_PROMPT, markup: { force_reply: true, input_field_placeholder: "مثلًا air purifier" } },
  })],
  [/^w:x:([A-Z0-9]{10})$/, async (m, env) => {
    await removeWatch(env, m[1]);
    return { screen: "watch", toast: "✅ بطّلت أتابعه" };
  }],
  [/^w:t:([A-Z0-9]{10}):(\d{1,7})$/, async (m, env, cq, now) => {
    const target = Number(m[2]);
    if (!target) throw new UserError("السعر لازم يبقى أكبر من صفر.");
    return { replace: await saveWatch(env, m[1], { target }, now), toast: "👀 هتابعه" };
  }],
  [/^w:s:([A-Z0-9]{10})$/, async (m, env, cq, now) => (
    { replace: await saveWatch(env, m[1], { stockOnly: true }, now), toast: "📦 هقولك لما يرجع" })],
  [/^w:p:([A-Z0-9]{10})$/, async (m) => ({
    toast: "✍️ اكتب السعر",
    send: { text: `${PRICE_PROMPT} (${m[1]}):`, markup: { force_reply: true, input_field_placeholder: "مثلًا 1500" } },
  })],
  [/^r:go$/, async (m, env, cq, now) => {
    const state = await readJson(env, STATE_PATH);
    await dispatch(env);
    const recent = recentFullRun(state, now);
    return { screen: "main", toast: recent ? "🚀 اتشغّل (من غير أمازون)" : "🚀 اتشغّل",
             note: recent ? "⏭️ شغّلته، بس من غير أمازون (فيه بحث كامل من أقل من ساعتين)." : "🚀 شغّلت البوت، النتيجة في خلال 5 دقايق تقريبًا." };
  }],
  // 🔕 تحت اللقطة: الرسالة نفسها بتتغير، وفيها ↩️ تراجع
  [/^mute:([A-Z0-9]{10})$/, async (m, env, cq, now) => {
    const asin = m[1];
    const title = await productTitle(env, asin, cq.message);
    await setMuted(env, asin, true, title, now);
    return { toast: "🔕 تمام، مش هبعتلك عنه تاني",
             deal: { text: withTitle("🔕 مش هبعتلك عن المنتج ده تاني", title),
                     keyboard: [...linkRows(cq.message), [btn("↩️ تراجع", `unmute:${asin}`)]] } };
  }],
  [/^unmute:([A-Z0-9]{10})$/, async (m, env, cq, now) => {
    const asin = m[1];
    const prev = await setMuted(env, asin, false, null, now);
    const title = (prev && prev.title) || (await productTitle(env, asin, cq.message));
    return { toast: "🔔 رجّعته",
             deal: { text: withTitle("🔔 رجّعته، هبعتلك عنه تاني", title),
                     keyboard: [...linkRows(cq.message), [btn("🔕 متبعتليش عن ده تاني", `mute:${asin}`)]] } };
  }],
  // من شاشة 🔕 منتجات مكتومة
  [/^u:([A-Z0-9]{10})$/, async (m, env, cq, now) => {
    await setMuted(env, m[1], false, null, now);
    return { screen: "muted", toast: "🔔 رجّعته" };
  }],
];

// كتم/رجوع منتج: muted_asins (البوت بيقراها) + muted_info فيها العنوان والتاريخ للشاشة
async function setMuted(env, asin, on, title, now) {
  let prev = null;
  await updateConfig(env, `config: ${on ? "mute" : "unmute"} ${asin}`, (cfg) => {
    cfg.muted_asins = cfg.muted_asins || [];
    cfg.muted_info = cfg.muted_info || {};
    prev = cfg.muted_info[asin] || null;
    if (on) {
      if (!cfg.muted_asins.includes(asin)) cfg.muted_asins.push(asin);
      cfg.muted_info[asin] = { at: now.toISOString(), ...(title ? { title: title.slice(0, 120) } : {}) };
      cfg.muted_asins = cfg.muted_asins.slice(-500);
      for (const a of Object.keys(cfg.muted_info)) if (!cfg.muted_asins.includes(a)) delete cfg.muted_info[a];
    } else {
      if (!cfg.muted_asins.includes(asin)) throw new UserError("المنتج ده مش مكتوم أصلًا.");
      cfg.muted_asins = cfg.muted_asins.filter((a) => a !== asin);
      delete cfg.muted_info[asin];
    }
  });
  return prev;
}

// عنوان المنتج من كابشن اللقطة نفسها (من غير ما نقرا أي ملف)، وإلا من الداشبورد
export function titleFromCaption(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines[0] && /^[🔕🔔]/u.test(lines[0])) return lines[1] || null; // رسالة اتكتمت قبل كده
  const rest = lines.filter((l) => !l.startsWith("🚨") && !l.includes("سكور"));
  return rest[0] && !/^[💰🎟️⭐🏪📊⚠️🎯✅⚡📉🛒📈ℹ️]/u.test(rest[0]) ? rest[0] : null;
}

async function productTitle(env, asin, msg) {
  const fromMsg = titleFromCaption(msg && (msg.caption || msg.text));
  return fromMsg || (await lookupProduct(env, asin, { history: false }).catch(() => ({}))).title || null;
}

const withTitle = (head, title) => (title ? `${head}\n${esc(short(title, 120))}` : head);

// زراير اللينكات (📈 كان بكام، 🛒 اشتري) بتفضل زي ما هي
function linkRows(msg) {
  return ((msg && msg.reply_markup && msg.reply_markup.inline_keyboard) || [])
    .map((row) => row.filter((b) => b.url)).filter((row) => row.length);
}

async function handleCallback(cq, env) {
  if (!isOwner(env, cq.from && cq.from.id)) {
    return tg(env, "answerCallbackQuery", { callback_query_id: cq.id });
  }
  const now = new Date();
  const data = String(cq.data || "");
  const route = data.length <= 64 && ROUTES.find(([re]) => re.test(data));
  if (!route) return tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "زرار قديم" });
  const msg = cq.message;
  let result;
  try {
    result = await route[1](data.match(route[0]), env, cq, now);
  } catch (e) {
    if (!(e instanceof UserError)) console.error("button failed:", e && e.message);
    const reason = errorReply(e).replace(/<[^>]+>/g, "").slice(0, 190);
    return tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: reason, show_alert: true });
  }
  try {
    if (msg && result.screen) {
      const view = renderScreen(result.screen, await screenData(env, result.screen), now);
      await editMessage(env, msg, result.note ? `${result.note}\n\n${view.text}` : view.text, view.keyboard);
    } else if (msg && result.replace) {
      await editMessage(env, msg, result.replace.text, result.replace.keyboard);
    } else if (msg && result.deal) {
      await editDeal(env, msg, result.deal.text, result.deal.keyboard);
    }
    if (result.send) {
      await tg(env, "sendMessage", { chat_id: secret(env.OWNER_CHAT_ID), text: result.send.text,
                                     reply_markup: result.send.markup });
    }
  } finally {
    await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: result.toast || "" });
  }
}

// رسالة اللقطة غالبًا صورة: الصور بتتعدّل بـ editMessageCaption مش editMessageText
async function editDeal(env, msg, text, keyboard) {
  if (!msg.photo) return editMessage(env, msg, text, keyboard);
  return tg(env, "editMessageCaption", { chat_id: msg.chat.id, message_id: msg.message_id, caption: text,
                                         parse_mode: "HTML", reply_markup: { inline_keyboard: keyboard } });
}

async function editMessage(env, msg, text, keyboard) {
  // نفس الرسالة بتتعدّل بدل ما نبعت رسايل جديدة
  const res = await tg(env, "editMessageText", { chat_id: msg.chat.id, message_id: msg.message_id, text,
                                                  parse_mode: "HTML", disable_web_page_preview: true,
                                                  reply_markup: { inline_keyboard: keyboard } });
  return res;
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

async function readJson(env, path) {
  const { text } = await readFile(env, path);
  return text ? JSON.parse(text) : {};
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
  // "message is not modified" = دوست على نفس الاختيار، مش مشكلة
  if (!data.ok && !/not modified/i.test(data.description || "")) {
    console.error(`telegram ${method} failed: ${data.description || res.status}`);
  }
  return data;
}

export async function ensureWebhook(env, origin) {
  const hook = await tg(env, "setWebhook", {
    url: `${origin}/telegram`, secret_token: secret(env.TG_WEBHOOK_SECRET),
    allowed_updates: ["message", "callback_query"], drop_pending_updates: true,
  });
  const commands = await tg(env, "setMyCommands", {
    commands: [
      ["menu", "🎛️ لوحة التحكم"], ["status", "حالة البوت"], ["run", "شغّل البوت دلوقتي"],
      ["watch", "تابع منتج"], ["unwatch", "بطّل متابعة"], ["list", "المتابعة والمكتوم"],
      ["add", "ضيف كلمة بحث"], ["remove", "شيل كلمة بحث"], ["discount", "أقل خصم %"], ["score", "أقل سكور"],
      ["mute", "اكتم الخاص (3h)"], ["help", "الأوامر"],
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
  const shortLink = s.match(/^(?:https?:\/\/)?((?:amzn\.(?:eu|to)|a\.co)\/\S+)/i);
  if (shortLink) {
    const res = await fetch(`https://${shortLink[1]}`, { method: "HEAD", redirect: "manual" });
    const loc = res.headers.get("location");
    return loc && loc !== s ? extractAsin(loc) : null;
  }
  return null;
}

function cairo(date) {
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", { timeZone: "Africa/Cairo", day: "numeric", month: "short",
                                                      hour: "numeric", minute: "2-digit" }).format(date);
}

function dayMonth(date) {
  return new Intl.DateTimeFormat("ar-EG-u-nu-latn", { timeZone: "Africa/Cairo", day: "numeric", month: "short" }).format(date);
}

const num = (n) => Math.round(n).toLocaleString("en-US");
const money = (n) => `${num(n)} ج.م`;
const short = (s, n) => (String(s).length > n ? `${String(s).slice(0, n - 1)}…` : String(s));
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
