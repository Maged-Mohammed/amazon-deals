// D1 وهمي للاختبارات: SQLite حقيقي في الذاكرة (node:sqlite) بنفس schema.sql، ومفيش أي طلب للنت
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";

const SCHEMA = readFileSync(new URL("../schema.sql", import.meta.url), "utf8");

export function fakeD1() {
  const sql = new DatabaseSync(":memory:");
  sql.exec(SCHEMA);
  const writes = [];
  const exec = (q, args, mode) => {
    const st = sql.prepare(q);
    if (/^\s*(INSERT|UPDATE|DELETE)/i.test(q)) writes.push(q);
    if (mode === "all") return { results: st.all(...args).map((r) => ({ ...r })) };
    if (mode === "first") { const r = st.get(...args); return r ? { ...r } : null; }
    const r = st.run(...args);
    return { meta: { changes: Number(r.changes) } };
  };
  const stmt = (q, args = []) => ({
    bind: (...a) => stmt(q, a),
    all: async () => exec(q, args, "all"),
    first: async () => exec(q, args, "first"),
    run: async () => exec(q, args, "run"),
    _run: () => exec(q, args, "run"),
  });
  const db = {
    prepare: (q) => stmt(q),
    batch: async (stmts) => {
      sql.exec("BEGIN");
      try { const out = stmts.map((s) => s._run()); sql.exec("COMMIT"); return out; }
      catch (e) { sql.exec("ROLLBACK"); throw e; }
    },
  };

  // مساعدين متزامنين للاختبارات
  const t0 = "2026-10-01T00:00:00.000Z";
  db.rows = (table) => sql.prepare(`SELECT * FROM ${table}`).all().map((r) => ({ ...r }));
  db.writes = (table) => writes.filter((q) => new RegExp(`\\b(INTO|UPDATE|FROM)\\s+${table}\\b`, "i").test(q)).length;
  // نفس شكل getPrivate: {watch, muted, watch_state}
  db.priv = () => {
    const watch = {}, muted = {}, watch_state = {};
    for (const r of db.rows("watch")) {
      watch[r.asin] = { target: r.target, ...(r.stock_only ? { stock_only: true } : {}), url: r.url, added: r.created_at,
                        ...(r.title ? { title: r.title } : {}) };
      if (r.checked_at) watch_state[r.asin] = { price: r.last_price, in_stock: r.in_stock == null ? null : Boolean(r.in_stock),
                                                alerted_price: r.alerted_price, checked: r.checked_at };
    }
    for (const r of db.rows("muted")) muted[r.asin] = { at: r.created_at || null, ...(r.title ? { title: r.title } : {}) };
    return { watch, muted, watch_state };
  };
  db.setPriv = ({ watch = {}, muted = {}, watch_state = {} }) => {
    sql.exec("DELETE FROM watch; DELETE FROM muted;");
    for (const [a, w] of Object.entries(watch)) {
      const st = watch_state[a] || {};
      sql.prepare(`INSERT INTO watch (asin, target, stock_only, title, url, last_price, in_stock, alerted_price, checked_at,
                   created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
        .run(a, w.target ?? null, w.stock_only ? 1 : 0, w.title ?? null, w.url ?? `https://www.amazon.eg/dp/${a}`,
             st.price ?? null, st.in_stock == null ? null : (st.in_stock ? 1 : 0), st.alerted_price ?? null,
             st.checked ?? (watch_state[a] ? t0 : null), w.added || t0, t0);
    }
    for (const [a, m] of Object.entries(muted)) {
      sql.prepare("INSERT INTO muted (asin, title, created_at, updated_at) VALUES (?, ?, ?, ?)")
        .run(a, m.title ?? null, m.at || "", t0); // "" = تاريخ مش معروف (من قبل ما نسجّله)
    }
  };
  // نفس شكل reports.json القديم: {id: {asin, at, credit?, decision?, decided_at?}}
  db.reports = () => Object.fromEntries(db.rows("reports").map((r) => [r.id, {
    asin: r.asin, at: r.reported_at, ...(r.credit ? { credit: r.credit } : {}),
    ...(r.decision ? { decision: r.decision, decided_at: r.decided_at } : {}) }]));
  db.setReports = (reports) => {
    sql.exec("DELETE FROM reports");
    for (const [id, r] of Object.entries(reports)) {
      sql.prepare(`INSERT INTO reports (id, asin, reported_at, credit, decision, decided_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
        .run(id, r.asin, r.at, r.credit ?? null, r.decision ?? null, r.decided_at ?? null, r.at, r.at);
    }
  };
  return db;
}
