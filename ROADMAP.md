# Roadmap

Big upgrade in 4 phases. Each phase is finished completely (implement, test, commit, push, verify on Actions), reported, and then work **stops until the owner says "continue"**.

| Phase | Status |
|---|---|
| 1. Smarter deals and better messages | ✅ Done (2026-10-23), see notes below |
| 2. GitHub Pages dashboard | ⏳ Not started |
| 3. Cloudflare Worker for Telegram control | ⏳ Not started |
| 4. White Friday mode | ⏳ Not started |

## Rules for every phase
- Start with `git pull`.
- Never hand-edit `deals_state.json` (the bot owns it).
- Commits use the owner's identity with no AI attribution.
- The repo is PUBLIC: never commit tokens or secrets. Use GitHub Secrets or Cloudflare secrets only.
- If something is needed from the owner (an ID, a login, a decision), ask. Don't guess.
- Africa/Cairo timezone for everything time-related.
- Update CLAUDE.md (Arabic) and this file at the end of each phase.
- Kanbkam returns 403 from GitHub Actions: every Kanbkam-based feature must degrade gracefully and fall back to the bot's own history.
- The workflow's save step does `git pull --rebase` with retries before pushing (the Worker in phase 3 also commits).

## PHASE 1: Smarter deals and better messages
1. **Price history**: price points per ASIN in `data/history.json`, capped per product. `deals_state.json` stays state-only.
2. **Deal Score (0-100)**: real discount (vs Kanbkam highest, else the bot's own highest), distance from all-time low, rating, review count, seller (Amazon / FBA vs unknown third party). Labels 🔥 أسطورية / 👍 كويسة / 😐 عادية. `MIN_SCORE` setting.
3. **Coupons**: detect coupon badges, compute the effective price, use it in the score.
4. **Cross-store**: if Kanbkam lists the same product cheaper on Noon or Jumia, add "⚡ أرخص في نون بـ X جنيه". Skip silently otherwise.
5. **Messages**: `sendPhoto` with image + Arabic caption, inline URL buttons 📈 كان بكام | 🛒 اشتري. Normal deals silent (`disable_notification`); price glitches (effective discount ≥ 80% AND near/below all-time low) loud, 🚨, sent immediately.
6. **Quiet hours 00:00-08:00 Cairo**: queue non-glitch deals; first run after 08:00 sends one "🌅 صباح الخير — لقطات الليلة" digest.
7. **Weekly report (Friday)**: deals found, fakes caught, top 5.
8. **Telegram channel** @loata_amazon (secret `TELEGRAM_CHANNEL_ID`): curated deals (score ≥ `CHANNEL_MIN_SCORE`). Private chat gets everything plus admin/status messages.
9. **Affiliate hook (OFF)**: optional secret `AMAZON_TAG` appends the tag to Amazon links plus a short Arabic disclosure. Not enabled.

**Phase 1 notes (2026-10-23)**
- Seller is not in search cards, so it comes from the product page, fetched for candidates only (same per-run cap as Kanbkam).
- When neither Kanbkam nor the bot's own history shows a higher price, the score uses Amazon's claimed discount × `UNVERIFIED_WEIGHT` (0.6).
- Kanbkam has no "same product on Noon" section; cross-store matching uses its "Similar Products" rows (store from `data-gtmid`) plus title matching. No real Noon/Jumia row has been seen yet, so this is built but unverified.
- Kanbkam stops for the rest of a run on the first 403 or timeout.
- Verified on Actions: selftest (bot is admin in the channel with can_post, sendPhoto + buttons delivered to private) and a quiet-hours run (12 deals queued, 0 sent).
- Fixed after the last verification run: a never-seen product with only Amazon's claimed 80%+ discount was sent as a loud glitch at 01:04; glitches now require a known all-time low.
- Not yet verified on Actions: the morning digest, normal daytime posting to the channel, glitch alerts, the Friday report (the first one is due 2026-10-30).
- Without Kanbkam on Actions, scores are about 45-55, below `CHANNEL_MIN_SCORE` 60, so the channel stays quiet until the history fills in.

## PHASE 2: GitHub Pages dashboard
- Static site in `/docs`, updated by the workflow with `docs/deals.json` (current deals) and per-product price history.
- Arabic RTL, mobile-first, dark/light mode, fast.
- Filters: category, min score, max price. Sort: score, discount, newest.
- Price chart per product, Kanbkam/Amazon links, "last updated" time.
- PWA manifest (installable on a phone home screen).
- Link to the Telegram channel t.me/loata_amazon.
- Enable Pages via `gh api` and report the URL.

## PHASE 3: Cloudflare Worker for instant Telegram control
- wrangler; owner has a free Cloudflare account (help log in, check Node).
- Worker = Telegram webhook. Verify Telegram's `secret_token` header; accept commands only from the owner's chat ID.
- Settings in `config.json` in the repo, updated by the Worker through the GitHub API with a fine-grained token scoped to this repo only (guide the owner to create it, store with `wrangler secret put`). The Python bot reads `config.json` at startup; its values override the script defaults.
- Commands (Arabic replies): /add, /remove (keywords), /discount, /score, /mute 3h, /status, /run (workflow_dispatch), /watch <amazon link> <target price> (alert at/below target and when back in stock), /unwatch, /list.
- Inline callback button under each private deal: 🔕 متبعتليش عن ده تاني (adds the ASIN to a mute list in config.json).
- Set the webhook, test every command end to end, show results.

## PHASE 4: White Friday mode
- `config.json` gets a `white_friday` block with start/end dates (ask the owner or look them up).
- Cron hourly; outside White Friday the bot exits early unless 3 hours passed since its last full run. During White Friday a full run every hour focused on the watchlist + top categories.
- Pre-White-Friday alert: products whose price jumped up in the 30 days before ("⚠️ سعره اترفع قبل الوايت فرايداي — خلي بالك").
- On White Friday, compare every deal against the pre-event history and flag fake discounts loudly in messages and on the dashboard.

## Decisions log
- 2026-10-22: Kanbkam returns 403 from GitHub Actions; leave it as is (option 2), fall back to the bot's own history.
- 2026-10-23: Actions pinned to releases at least 2 weeks old (`checkout@v7.0.1`, `setup-python@v7.0.0`).
- 2026-10-23: Filters stay as they are until a full keyword rotation has run.
- 2026-10-23: Channel threshold is adaptive: 50 for the first 14 days of price history, then 60 automatically (around 2026-11-06).
- 2026-10-23: Pinned channel welcome message drafted; post and pin only after the owner approves it.
- 2026-10-23: Phase 2 is on hold until the owner has checked the morning digest and daytime channel posts.
- 2026-10-23: Phase 1 defaults (owner may change): `MIN_SCORE` 40, `CHANNEL_MIN_SCORE` 60, `GLITCH_DISCOUNT` 80, `UNVERIFIED_WEIGHT` 0.6, `HISTORY_CAP` 90, `DIGEST_SIZE` 10, weekly report Fridays from 10:00 Cairo, once the week has at least 6 days of data.
