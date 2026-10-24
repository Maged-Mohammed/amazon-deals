-- D1 (loata-private): البيانات الشخصية بس. الأسعار والحالة لسه في الريبو (المرحلة 5 = النقل الكامل)
-- التطبيق: npx wrangler d1 execute loata-private --remote --file schema.sql
-- كل الأوقات ISO (UTC)

-- 👀 المتابعة: الهدف (أو stock_only) + آخر حالة البوت شافها
CREATE TABLE IF NOT EXISTS watch (
  asin          TEXT PRIMARY KEY,
  target        REAL,
  stock_only    INTEGER NOT NULL DEFAULT 0,
  title         TEXT,
  url           TEXT,
  last_price    REAL,
  in_stock      INTEGER,
  alerted_price REAL,
  checked_at    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- 🔕 المنتجات المكتومة
CREATE TABLE IF NOT EXISTS muted (
  asin       TEXT PRIMARY KEY,
  title      TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- 🚨 بلاغات المتابعين وقراراتك (من غير أي Telegram ID؛ العدادات في KV)
CREATE TABLE IF NOT EXISTS reports (
  id          TEXT PRIMARY KEY,
  asin        TEXT NOT NULL,
  reported_at TEXT NOT NULL,
  credit      TEXT,
  decision    TEXT CHECK (decision IN ('publish', 'reject', 'watch')),
  decided_at  TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_by_asin ON reports (asin, reported_at);
