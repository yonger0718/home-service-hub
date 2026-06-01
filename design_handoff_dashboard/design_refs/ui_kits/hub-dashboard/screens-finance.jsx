/* Home Hub UI Kit — screens */
const { useState: useS, useRef, useEffect } = React;

/* ---- Chart wrapper ------------------------------------------------------ */
function ChartJS({ type, data, options, style }) {
  const ref = useRef(null);
  const inst = useRef(null);
  useEffect(() => {
    if (!ref.current || !window.Chart) return;
    inst.current = new window.Chart(ref.current, { type, data, options });
    return () => inst.current && inst.current.destroy();
  }, []);
  return <canvas ref={ref} style={style}></canvas>;
}

const TREND_POS = "#1f9d6b", TREND_NEG = "#e0457b";
const PIE_COLORS = ["#533afd", "#665efd", "#8e84fb", "#e0457b", "#f96bee", "#9aa6bd"];
/* read live theme tokens at chart-creation time (screens remount on theme change) */
const cvar = (n, f) => { try { const v = getComputedStyle(document.documentElement).getPropertyValue(n).trim(); return v || f; } catch (e) { return f; } };
const isDark = () => document.documentElement.classList.contains("app-dark-mode");

/* ===================== PORTFOLIO DASHBOARD ============================== */
const HOLDINGS = [
  { sym: "台積電", code: "2330", price: 1085.0, chg: 15.0, chgPct: 1.4, qty: 1000, ret: 24.18, mv: 1085000, cost: 874.2, divs: 28000 },
  { sym: "聯發科", code: "2454", price: 1320.0, chg: -22.0, chgPct: -1.64, qty: 200, ret: 8.42, mv: 264000, cost: 1217.5, divs: 12400 },
  { sym: "元大台灣50", code: "0050", price: 198.35, chg: 1.05, chgPct: 0.53, qty: 3000, ret: 12.9, mv: 595050, cost: 175.7, divs: 41200 },
  { sym: "中華電", code: "2412", price: 128.5, chg: -0.5, chgPct: -0.39, qty: 1500, ret: -2.31, mv: 192750, cost: 131.5, divs: 9800 },
];

const RANGE_KEYS = ["1M", "3M", "YTD", "1Y", "5Y"];
const NW_RANGES = {
  "1M":  { labels: ["05/01", "05/08", "05/15", "05/22", "05/29"], data: [2.27, 2.30, 2.28, 2.32, 2.34], xirr: 9.4,   span: "近 1 月" },
  "3M":  { labels: ["3月", "4月", "5月", "現在"], data: [2.18, 2.24, 2.30, 2.34], xirr: 12.1, span: "近 3 月" },
  "YTD": { labels: ["1月", "2月", "3月", "4月", "5月"], data: [2.10, 2.06, 2.15, 2.26, 2.34], xirr: 13.6, span: "今年以來" },
  "1Y":  { labels: ["6月", "7月", "8月", "9月", "10月", "11月", "12月", "1月", "2月", "3月", "4月", "5月"], data: [1.78, 1.85, 1.92, 1.98, 2.05, 1.99, 2.12, 2.21, 2.18, 2.26, 2.30, 2.34], xirr: 14.82, span: "近 1 年" },
  "5Y":  { labels: ["2022", "2023", "2024", "2025", "2026"], data: [0.62, 1.05, 1.48, 1.92, 2.34], xirr: 16.3, span: "近 5 年" },
};

function PortfolioDashboard() {
  const [expanded, setExpanded] = useS("2330");
  const [range, setRange] = useS("1Y");
  const totalMV = HOLDINGS.reduce((s, h) => s + h.mv, 0);
  const totalCost = HOLDINGS.reduce((s, h) => s + h.cost * h.qty, 0);
  const totalDiv = HOLDINGS.reduce((s, h) => s + h.divs, 0);
  const unrealized = totalMV - totalCost;
  const unrealizedPct = (unrealized / totalCost) * 100;
  const dayPnl = HOLDINGS.reduce((s, h) => s + h.chg * h.qty, 0);
  const totalReturn = unrealized + totalDiv;
  const totalReturnPct = (totalReturn / totalCost) * 100;
  const series = NW_RANGES[range];
  const nwUp = series.data[series.data.length - 1] >= series.data[0];
  const periodPct = (series.data[series.data.length - 1] / series.data[0] - 1) * 100;

  return (
    <div>
      <div className="page-head" style={{ justifyContent: "flex-end" }}>
        <div className="head-actions">
          <Btn variant="secondary" icon="pi-refresh">刷新行情</Btn>
        </div>
      </div>
      <div className="bento-grid">
        <div className="bento b-half">
          <div className="label">當前總市值</div>
          <div className="value">{ntd(totalMV)}</div>
          <div className="meta">總成本 {ntd(totalCost)}</div>
          <span className={`pct-badge ${totalReturn < 0 ? "down" : ""}`}>{pct(totalReturnPct)} · 含息總報酬</span>
        </div>
        <div className="bento b-half">
          <div className="label">未實現損益</div>
          <div className={`value ${unrealized >= 0 ? "up" : "down"}`}>{ntd(unrealized)}</div>
          <span className={`pct-badge ${unrealized < 0 ? "down" : ""}`}>{pct(unrealizedPct)}</span>
        </div>
        <div className="bento b-third">
          <div className="label">今日總預估損益</div>
          <div className={`value ${dayPnl >= 0 ? "up" : "down"}`}>{ntd(dayPnl)}</div>
          <div className="meta">今日表現</div>
        </div>
        <div className="bento b-third">
          <div className="label">累計股利收入</div>
          <div className="value dividend">{ntd(totalDiv)}</div>
          <div className="meta">已入帳現金</div>
        </div>
        <div className="bento b-third">
          <div className="label">年化報酬率 (XIRR)</div>
          <div className={`value ${series.xirr >= 0 ? "up" : "down"}`}>{pct(series.xirr)}</div>
          <div className="meta">{series.span} · 含息含手續費</div>
        </div>

        <div className="bento b-full">
          <div className="chart-head">
            <div className="chart-head-left">
              <h3 className="card-title" style={{ margin: 0 }}>淨值走勢</h3>
              <span className={`pct-badge ${periodPct < 0 ? "down" : ""}`} style={{ marginTop: 0 }}>{pct(periodPct)} · {series.span}</span>
            </div>
            <div className="seg-toggle range-tabs">
              {RANGE_KEYS.map(k => (
                <button key={k} className={range === k ? "active" : ""} onClick={() => setRange(k)}>{k}</button>
              ))}
            </div>
          </div>
          <div style={{ height: 220 }}>
            <ChartJS
              key={range}
              type="line"
              style={{ width: "100%", height: "100%" }}
              data={{
                labels: series.labels,
                datasets: [{
                  data: series.data.map(v => v * 1e6),
                  borderColor: cvar(nwUp ? "--app-trend-positive" : "--app-trend-negative", "#1d2433"), borderWidth: 2.5, tension: 0.4, fill: true,
                  backgroundColor: cvar(nwUp ? "--app-trend-positive-soft" : "--app-trend-negative-soft", "rgba(29,36,51,0.06)"), pointRadius: 0, pointHoverRadius: 4,
                }],
              }}
              options={{
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false } },
                scales: {
                  x: { grid: { display: false }, ticks: { color: cvar("--app-text-muted", "#6b7280"), font: { size: 11 } } },
                  y: { grid: { color: isDark() ? "rgba(255,255,255,0.08)" : "#e6eaf0" }, ticks: { color: cvar("--app-text-muted", "#6b7280"), font: { size: 11 }, callback: v => (v / 1e6).toFixed(1) + "M" } },
                },
              }}
            />
          </div>
        </div>

        <div className="bento b-full">
          <h3 className="card-title">持股明細</h3>
          <div className="holdings">
            {HOLDINGS.map(h => (
              <div className="stock-row" key={h.code} onClick={() => setExpanded(expanded === h.code ? null : h.code)}>
                <div className="stock-top">
                  <div className="stock-sym"><span className="s">{h.sym}</span><span className="n">{h.code}</span></div>
                  <div className="stock-price">
                    <span className="p">{h.price.toFixed(2)}</span>
                    <span className={`c ${h.chg >= 0 ? "up" : "down"}`}>
                      {h.chg >= 0 ? "+" : ""}{h.chg.toFixed(2)} ({pct(h.chgPct)})
                    </span>
                  </div>
                </div>
                <div className="stock-stats">
                  <div className="st"><span className="l">股數</span><span className="v">{h.qty.toLocaleString()}</span></div>
                  <div className="st"><span className="l">報酬</span><span className={`v ${h.ret >= 0 ? "up" : "down"}`}>{pct(h.ret)}</span></div>
                </div>
                {expanded === h.code && (
                  <div className="detail-panel">
                    <div className="detail-grid">
                      <div className="d"><small>市值</small><div>{ntd(h.mv)}</div></div>
                      <div className="d"><small>平均成本</small><div>{h.cost.toFixed(2)}</div></div>
                      <div className="d"><small>累計股利</small><div>{ntd(h.divs)}</div></div>
                      <div className="d"><small>未實現損益</small><div className={h.mv - h.cost * h.qty >= 0 ? "up" : "down"}>{ntd(h.mv - h.cost * h.qty)}</div></div>
                      <div className="d"><small>含息損益</small><div className="up">{ntd(h.mv - h.cost * h.qty + h.divs)}</div></div>
                      <div className="d"><small>年化報酬率</small><div className="up">{pct(h.ret * 0.6)}</div></div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ===================== ACCOUNTING ANALYTICS ============================= */
const BREAKDOWN = [
  { cat: "餐飲", amt: 12480, p: 30 }, { cat: "交通", amt: 6200, p: 15 },
  { cat: "居家", amt: 8300, p: 20 }, { cat: "娛樂", amt: 4980, p: 12 },
  { cat: "醫療", amt: 3320, p: 8 }, { cat: "其他", amt: 6220, p: 15 },
];
const CARDS = [
  { name: "國泰 CUBE 卡", use: 0.62, amt: 24800, limit: 40000 },
  { name: "玉山 Pi 拍錢包卡", use: 0.88, amt: 17600, limit: 20000 },
  { name: "台新 @GoGo 卡", use: 1.04, amt: 31200, limit: 30000 },
];
function AccountingDash() {
  const totalExpense = BREAKDOWN.reduce((s, b) => s + b.amt, 0);
  return (
    <div>
      <div className="page-head">
        <h2 className="section-title">記帳分析</h2>
        <div className="head-actions"><Btn variant="secondary" icon="pi-calendar">2026 / 05</Btn></div>
      </div>
      <div className="bento-grid">
        <div className="bento b-third">
          <div className="label">本月總支出</div>
          <div className="value">{ntd(totalExpense)}</div>
          <div className="meta"><span className="up"><i className="pi pi-arrow-up"></i> NT$3,260 較上月</span></div>
        </div>
        <div className="bento b-third">
          <div className="label">本月結餘</div>
          <div className="value">{ntd(28600)}</div>
          <div className="meta">儲蓄率 41.0%</div>
        </div>
        <div className="bento b-third">
          <div className="label">本月總收入</div>
          <div className="value">{ntd(70100)}</div>
          <div className="meta">薪資 + 股利入帳</div>
        </div>

        <div className="bento b-wide">
          <h3 className="card-title">支出分類</h3>
          <div className="chart-layout">
            <div className="chart-wrap">
              <ChartJS
                type="doughnut"
                style={{ width: "100%", height: "100%" }}
                data={{ labels: BREAKDOWN.map(b => b.cat), datasets: [{ data: BREAKDOWN.map(b => b.amt), backgroundColor: PIE_COLORS, borderWidth: 0 }] }}
                options={{ responsive: true, maintainAspectRatio: false, animation: false, cutout: "72%", plugins: { legend: { display: false } } }}
              />
              <div className="chart-center"><span className="l">總額</span><span className="v">{(totalExpense / 1000).toFixed(1)}K</span></div>
            </div>
            <div className="legend">
              {BREAKDOWN.map((b, i) => (
                <div className="legend-item" key={b.cat}>
                  <span className="dot" style={{ background: PIE_COLORS[i] }}></span>
                  <span className="name">{b.cat}</span><span className="val">{b.p}%</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="bento b-tall">
          <h3 className="card-title">類別變化</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: ".75rem" }}>
            {[["餐飲", 12480, 1840], ["娛樂", 4980, -1200], ["交通", 6200, 560], ["居家", 8300, -320]].map(([c, cur, d]) => (
              <div key={c} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: ".5rem", borderBottom: "1px solid var(--app-border)" }}>
                <div style={{ display: "flex", flexDirection: "column" }}>
                  <span style={{ fontWeight: 600, fontSize: ".9rem", color: "var(--app-text)" }}>{c}</span>
                  <span style={{ fontSize: ".7rem", color: "var(--app-text-muted)" }}>{ntd(cur)}</span>
                </div>
                <span style={{ fontSize: ".75rem", fontWeight: 700, color: d > 0 ? "var(--app-trend-positive)" : "var(--app-trend-negative)" }}>
                  {d > 0 ? "+" : "−"}{ntd(Math.abs(d))}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="bento b-full">
          <h3 className="card-title">信用卡額度監控</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: "1rem" }}>
            {CARDS.map(c => {
              const over = c.use >= 1, warn = c.use >= 0.8;
              const fill = over ? "var(--app-danger)" : warn ? "var(--app-warning)" : "var(--app-success)";
              return (
                <div key={c.name} style={{ background: "var(--app-surface-soft)", padding: "1rem", borderRadius: 12, border: "1px solid var(--app-border)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: ".5rem" }}>
                    <span style={{ fontWeight: 700, fontSize: ".85rem", color: "var(--app-text)" }}>{c.name}</span>
                    <span style={{ fontWeight: 800, fontSize: ".85rem", color: fill }}>{Math.round(c.use * 100)}%</span>
                  </div>
                  <div style={{ height: 4, background: "var(--app-border)", borderRadius: 2, overflow: "hidden" }}>
                    <div style={{ width: Math.min(c.use, 1) * 100 + "%", height: "100%", background: fill }}></div>
                  </div>
                  <div style={{ marginTop: ".65rem", fontSize: ".75rem", color: "var(--app-text-muted)", fontWeight: 600 }}>
                    已使用 {ntd(c.amt)} · 額度 {ntd(c.limit)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ===================== STOCK TRADE RECORDS ============================= */
/* 股票交易紀錄 — actual buy/sell trades (was wrongly showing the personal
   accounting timeline). Fee/tax modelled simply; amounts use tnum. */
const TRADES = [
  { id: 1, date: "2026-05-26", day: "26", mon: "MAY", sym: "台積電", code: "2330", side: "BUY",  qty: 100, price: 1070.0, fee: 152 },
  { id: 2, date: "2026-05-22", day: "22", mon: "MAY", sym: "中華電", code: "2412", side: "BUY",  qty: 300, price: 129.5, fee: 55 },
  { id: 3, date: "2026-05-20", day: "20", mon: "MAY", sym: "聯發科", code: "2454", side: "SELL", qty: 50,  price: 1340.0, fee: 95, tax: 201 },
  { id: 4, date: "2026-05-15", day: "15", mon: "MAY", sym: "元大台灣50", code: "0050", side: "BUY", qty: 500, price: 196.0, fee: 139 },
  { id: 5, date: "2026-05-08", day: "08", mon: "MAY", sym: "台積電", code: "2330", side: "SELL", qty: 50, price: 1045.0, fee: 74, tax: 156 },
];

function StockTransactionList() {
  const [side, setSide] = useS("ALL");
  const [q, setQ] = useS("");
  const rows = TRADES.filter(t => (side === "ALL" || t.side === side) && (!q || t.sym.includes(q) || t.code.includes(q)));
  const buys = TRADES.filter(t => t.side === "BUY").reduce((s, t) => s + t.qty * t.price + t.fee, 0);
  const sells = TRADES.filter(t => t.side === "SELL").reduce((s, t) => s + t.qty * t.price - t.fee - (t.tax || 0), 0);
  let lastDay = null;

  return (
    <div>
      <div className="page-head" style={{ justifyContent: "flex-end" }}>
        <div className="head-actions">
          <Btn variant="secondary" icon="pi-upload">匯入 CSV</Btn>
          <Btn icon="pi-plus">新增交易</Btn>
        </div>
      </div>

      <div className="summary-row">
        <div className="summary-item"><div className="l">本月買進</div><div className="v tnum" style={{ color: "var(--app-buy)" }}>{ntd(buys)}</div></div>
        <div className="summary-item"><div className="l">本月賣出</div><div className="v tnum" style={{ color: "var(--app-sell)" }}>{ntd(sells)}</div></div>
        <div className="summary-item net"><div className="l">交易筆數</div><div className="v tnum">{TRADES.length} 筆</div></div>
      </div>

      <div className="filter-bar">
        <div className="search-wrap">
          <i className="pi pi-search"></i>
          <input placeholder="搜尋股票名稱或代號..." value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <div className="filter-controls">
          <div className="type-pills">
            {[["ALL", "全部"], ["BUY", "買進"], ["SELL", "賣出"]].map(([v, l]) => (
              <button key={v} className={side === v ? "active" : ""} onClick={() => setSide(v)}>{l}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="timeline">
        {rows.map(t => {
          const newDay = t.day !== lastDay; lastDay = t.day;
          const gross = t.qty * t.price;
          const net = t.side === "BUY" ? gross + t.fee : gross - t.fee - (t.tax || 0);
          const isBuy = t.side === "BUY";
          return (
            <div className="tl-item" key={t.id}>
              <div className="tl-date">{newDay && (<><span className="d">{t.day}</span><span className="m">{t.mon}</span></>)}</div>
              <div className="tl-card stk-card">
                <div className="stk-lhs">
                  <span className={`side-tag ${isBuy ? "buy" : "sell"}`}>{isBuy ? "買進" : "賣出"}</span>
                  <div>
                    <div className="tl-name">{t.sym} <span className="stk-code">{t.code}</span></div>
                    <div className="tl-meta tnum">
                      <span>{t.qty.toLocaleString()} 股</span><span className="dot">×</span>
                      <span className="stk-price">{t.price.toFixed(2)}</span>
                      <span className="dot">·</span><span>手續費 {ntd(t.fee + (t.tax || 0))}</span>
                    </div>
                  </div>
                </div>
                <span className={`tl-amt tnum ${isBuy ? "buy" : "sell"}`}>
                  {isBuy ? "−" : "+"}{ntd(net)}
                </span>
              </div>
            </div>
          );
        })}
        {rows.length === 0 && (
          <div className="empty"><i className="pi pi-chart-line"></i><h4>無股票交易</h4><p>記錄你的第一筆股票交易，開始追蹤投資績效。</p></div>
        )}
      </div>
    </div>
  );
}

Object.assign(window, { PortfolioDashboard, AccountingDash, StockTransactionList, ChartJS });
