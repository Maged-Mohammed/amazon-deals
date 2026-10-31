// اختبارات offline للينكات أمازون المختصرة (زرار Share في الأبلكيشن): redirects وهمية، ومفيش ولا طلب للنت
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { asinFromUrl, extractAsin, findAmazonLink, resolveShortLink } from "../src/index.js";

const ASIN = "B0FN7FVX8N";
let routes, calls, kv, logs, origLog;

// زي amzn.eu الحقيقي: HEAD = 404، وGET = 301 لـ Location (من غير ما نقرا أي صفحة)
const redirect = (location, status = 301) => (init) =>
  init.method === "HEAD" ? new Response(null, { status: 404 }) : new Response("<html>body</html>", { status, headers: { location } });

beforeEach(() => {
  routes = {};
  calls = [];
  kv = new Map();
  logs = [];
  origLog = console.log;
  console.log = (...a) => logs.push(a.join(" "));
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), method: init.method, redirect: init.redirect, signal: init.signal });
    const handler = routes[String(url)];
    if (!handler) throw new Error(`unexpected fetch: ${url}`);
    return handler(init);
  };
});
afterEach(() => { console.log = origLog; });

const env = () => ({ REPORTS_KV: { get: async (k) => kv.get(k) ?? null, put: async (k, v) => { kv.set(k, v); } } });

test("the real share link shape: HEAD fails, GET with redirect: manual works", async () => {
  routes["https://amzn.eu/d/09JtBC4d"] = redirect(`https://www.amazon.eg/dp/${ASIN}?ref=cm_sw_r_cso_cp_apan_dp_X&th=1&psc=1`);
  assert.equal(await extractAsin("https://amzn.eu/d/09JtBC4d", env()), ASIN);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].redirect, "manual");
  assert.ok(calls[0].signal instanceof AbortSignal, "every hop has a timeout");
});

test("links are found inside share text with words around them", () => {
  const share = `Check out this deal on Amazon: Kivo Gaming Mouse RGB 7200, black https://amzn.eu/d/09JtBC4d — enjoy!`;
  assert.equal(findAmazonLink(share), "https://amzn.eu/d/09JtBC4d");
  assert.equal(findAmazonLink("شوف ده amzn.eu/d/09JtBC4d."), "amzn.eu/d/09JtBC4d");
  assert.equal(findAmazonLink("(https://www.amazon.eg/dp/B0TEST0001)"), "https://www.amazon.eg/dp/B0TEST0001");
  assert.equal(findAmazonLink("see https://example.com/dp/B0TEST0001 and nothing else"), null, "only Amazon links");
  assert.equal(findAmazonLink("no link here"), null);
});

test("every common product URL pattern", () => {
  const cases = {
    [`https://www.amazon.eg/dp/${ASIN}`]: ASIN,
    [`https://www.amazon.eg/Kivo-Mouse/dp/${ASIN}/ref=sr_1_1?keywords=x`]: ASIN,
    [`https://www.amazon.eg/gp/product/${ASIN}?psc=1`]: ASIN,
    [`https://www.amazon.eg/gp/aw/d/${ASIN}`]: ASIN,
    [`https://www.amazon.eg/product/${ASIN}`]: ASIN,
    [`https://www.amazon.eg/-/en/dp/${ASIN}`]: ASIN,
    [`https://www.amazon.eg/-/ar/dp/${ASIN}?language=ar_AE`]: ASIN,
    [`https://www.amazon.eg/-/en/Kivo-Gaming-Mouse/dp/${ASIN}/`]: ASIN,
    [`https://www.amazon.eg/gp/offer-listing/x?asin=${ASIN}`]: ASIN,
    [`https://www.amazon.eg/dp/${ASIN.toLowerCase()}`]: ASIN,
    "https://www.amazon.eg/dp/B0SHORT": null,
    "https://www.amazon.eg/dp/B0TOOLONG123": null,
    "https://www.amazon.eg/gp/help/customer/display.html": null,
    "https://www.amazon.eg/s?k=mouse": null,
    "https://www.amazon.eg/x?asin=B0BAD!!!!!": null,
  };
  for (const [url, want] of Object.entries(cases)) assert.equal(asinFromUrl(url), want, url);
});

test("every short-link host, with and without https, including /d/", async () => {
  routes["https://amzn.eu/d/aaa111"] = redirect(`https://www.amazon.eg/-/ar/dp/${ASIN}`);
  routes["https://amzn.to/3xyzABC"] = redirect(`https://www.amazon.eg/gp/product/${ASIN}`);
  routes["https://a.co/d/bbb222"] = redirect(`https://www.amazon.eg/gp/aw/d/${ASIN}`);
  routes["https://amzn.asia/d/ccc333"] = redirect(`https://www.amazon.eg/x?asin=${ASIN}`);
  for (const link of ["amzn.eu/d/aaa111", "https://amzn.to/3xyzABC", "a.co/d/bbb222", "http://amzn.asia/d/ccc333"]) {
    assert.equal(await extractAsin(link, env()), ASIN, link);
  }
});

test("follows up to 3 hops between short links, relative locations too", async () => {
  routes["https://amzn.to/hop1"] = redirect("https://a.co/hop2");
  routes["https://a.co/hop2"] = redirect("/hop3", 302);
  routes["https://a.co/hop3"] = redirect(`https://www.amazon.eg/dp/${ASIN}`, 307);
  assert.equal(await resolveShortLink("https://amzn.to/hop1", env()), ASIN);
  assert.equal(calls.length, 3);
  routes["https://amzn.to/loop"] = redirect("https://amzn.to/loop");
  calls = [];
  assert.equal(await resolveShortLink("https://amzn.to/loop", env()), null);
  assert.equal(calls.length, 3, "never more than 3 hops");
  assert.match(logs.at(-1), /more than 3 redirects/);
});

test("a non-Amazon domain anywhere in the chain is refused and never fetched", async () => {
  routes["https://amzn.eu/d/evil01"] = redirect("https://a.co/d/next");
  routes["https://a.co/d/next"] = redirect(`https://evil.example/dp/${ASIN}`);
  assert.equal(await extractAsin("https://amzn.eu/d/evil01", env()), null);
  assert.ok(!calls.some((c) => c.url.includes("evil.example")));
  assert.match(logs.at(-1), /non-Amazon domain \(evil\.example\)/);
  routes["https://amzn.eu/d/evil02"] = redirect(`https://amazon.eg.evil.example/dp/${ASIN}`);
  assert.equal(await extractAsin("https://amzn.eu/d/evil02", env()), null, "look-alike host");
});

test("a redirect to an Amazon page without an ASIN stops there, without loading the page", async () => {
  routes["https://amzn.eu/d/page01"] = redirect("https://www.amazon.eg/deals?ref=x");
  assert.equal(await extractAsin("https://amzn.eu/d/page01", env()), null);
  assert.deepEqual(calls.map((c) => c.url), ["https://amzn.eu/d/page01"], "amazon.eg itself is never fetched");
  assert.match(logs.at(-1), /redirect without an ASIN/);
});

test("timeouts, network errors and non-redirects fail cleanly and log no URL", async () => {
  routes["https://amzn.eu/d/slow01"] = () => { throw new DOMException("The operation timed out.", "TimeoutError"); };
  routes["https://amzn.eu/d/down01"] = () => { throw new TypeError("fetch failed"); };
  routes["https://amzn.eu/d/gone01"] = () => new Response("", { status: 404 });
  routes["https://amzn.eu/d/noloc1"] = () => new Response(null, { status: 301 });
  assert.equal(await extractAsin("https://amzn.eu/d/slow01", env()), null);
  assert.match(logs.at(-1), /short link failed \(amzn\.eu\): timeout/);
  assert.equal(await extractAsin("https://amzn.eu/d/down01", env()), null);
  assert.match(logs.at(-1), /network TypeError/);
  assert.equal(await extractAsin("https://amzn.eu/d/gone01", env()), null);
  assert.match(logs.at(-1), /no redirect \(HTTP 404\)/);
  assert.equal(await extractAsin("https://amzn.eu/d/noloc1", env()), null);
  for (const line of logs) assert.ok(!/slow01|down01|gone01|noloc1/.test(line), `the link itself is not logged: ${line}`);
  assert.equal(kv.size, 0, "failures are not cached");
});

test("results are cached, so the same link is never resolved twice", async () => {
  const e = env();
  routes["https://amzn.eu/d/09JtBC4d"] = redirect(`https://www.amazon.eg/dp/${ASIN}`);
  assert.equal(await extractAsin("https://amzn.eu/d/09JtBC4d", e), ASIN);
  assert.equal(await extractAsin("amzn.eu/d/09JtBC4d/", e), ASIN, "same link, other spelling");
  assert.equal(await extractAsin("Share: https://amzn.eu/d/09JtBC4d?tag=x", e), ASIN);
  assert.equal(calls.length, 1);
  assert.equal(kv.get("short:amzn.eu/d/09JtBC4d"), ASIN);
});

test("resolving a link is not an Amazon request: only short-link hosts are contacted", async () => {
  routes["https://amzn.eu/d/09JtBC4d"] = redirect(`https://www.amazon.eg/dp/${ASIN}`);
  await extractAsin("https://amzn.eu/d/09JtBC4d", env());
  assert.ok(calls.every((c) => /^https:\/\/(amzn\.(eu|to|asia)|a\.co)\//.test(c.url)));
  // ولا GitHub ولا deals_state.json (ميزانية البوت في الحالة دي ومش بنلمسها)
  assert.ok(!calls.some((c) => c.url.includes("api.github.com")));
});
