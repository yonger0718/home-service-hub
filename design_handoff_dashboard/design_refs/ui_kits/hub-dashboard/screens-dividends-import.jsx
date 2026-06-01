/* Home Hub UI Kit — Dividends (股利領取紀錄) + CSV import (匯入 CSV) */
const { useState: useSdi } = React;

/* ===================== DIVIDEND RECORDS ================================= */
const DIVIDENDS = [
  { id: 1, date: "2026-05-22", day: "22", mon: "MAY", sym: "元大台灣50", code: "0050", type: "現金", per: 2.6, qty: 3000 },
  { id: 2, date: "2026-04-15", day: "15", mon: "APR", sym: "台積電", code: "2330", type: "現金", per: 4.0, qty: 1000 },
  { id: 3, date: "2026-03-20", day: "20", mon: "MAR", sym: "中華電", code: "2412", type: "現金", per: 4.7, qty: 1500 },
  { id: 4, date: "2026-02-18", day: "18", mon: "FEB", sym: "聯發科", code: "2454", type: "現金", per: 32.0, qty: 200 },
];
const UPCOMING = [
  { sym: "元大高股息", code: "0056", ex: "06 / 18", est: 13500 },
  { sym: "台積電", code: "2330", ex: "06 / 26", est: 4000 },
];

function DividendList() {
  const total = DIVIDENDS.reduce((s, d) => s + d.per * d.qty, 0);
  let lastDay = null;
  return (
    <div>
      <div className="page-head" style={{ justifyContent: "flex-end" }}>
        <div className="head-actions">
          <Btn variant="secondary" icon="pi-calendar">2026 全年</Btn>
          <Btn icon="pi-plus">新增股利</Btn>
        </div>
      </div>

      <div className="summary-row">
        <div className="summary-item"><div className="l">本年度累計股利</div><div className="v tnum" style={{ color: "var(--app-dividend)" }}>{ntd(total)}</div></div>
        <div className="summary-item"><div className="l">平均殖利率</div><div className="v tnum">3.84%</div></div>
        <div className="summary-item net"><div className="l">領取筆數</div><div className="v tnum">{DIVIDENDS.length} 筆</div></div>
      </div>

      {/* upcoming ex-dividend reminder */}
      <div className="bento" style={{ borderRadius: 16, padding: "1.25rem 1.5rem", marginBottom: "1.5rem" }}>
        <h3 className="card-title" style={{ marginBottom: "1rem" }}>即將除權息提醒</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: "1rem" }}>
          {UPCOMING.map(u => (
            <div key={u.code} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--app-surface-soft)", border: "1px solid var(--app-border)", borderRadius: 12, padding: "0.85rem 1rem" }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: ".92rem", color: "var(--app-text)" }}>{u.sym} <span style={{ fontSize: ".76rem", color: "var(--app-text-muted)", fontWeight: 600 }}>{u.code}</span></div>
                <div style={{ fontSize: ".74rem", color: "var(--app-text-muted)", marginTop: 2 }}>除息日 {u.ex}</div>
              </div>
              <span className="tnum" style={{ fontWeight: 800, fontSize: ".9rem", color: "var(--app-dividend)" }}>~{ntd(u.est)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="timeline">
        {DIVIDENDS.map(d => {
          const newDay = d.day !== lastDay; lastDay = d.day;
          return (
            <div className="tl-item" key={d.id}>
              <div className="tl-date">{newDay && (<><span className="d">{d.day}</span><span className="m">{d.mon}</span></>)}</div>
              <div className="tl-card stk-card">
                <div className="stk-lhs">
                  <span className="side-tag cash">{d.type}股利</span>
                  <div>
                    <div className="tl-name">{d.sym} <span className="stk-code">{d.code}</span></div>
                    <div className="tl-meta tnum">
                      <span>每股 {d.per.toFixed(2)}</span><span className="dot">×</span><span>{d.qty.toLocaleString()} 股</span>
                    </div>
                  </div>
                </div>
                <span className="tl-amt tnum dividend">+{ntd(d.per * d.qty)}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ===================== CSV IMPORT ====================================== */
const PARSED = [
  { date: "2026-05-26", sym: "台積電", code: "2330", side: "買進", qty: 100, price: 1070.0 },
  { date: "2026-05-20", sym: "聯發科", code: "2454", side: "賣出", qty: 50, price: 1340.0 },
  { date: "2026-05-15", sym: "元大台灣50", code: "0050", side: "買進", qty: 500, price: 196.0 },
  { date: "2026-05-08", sym: "中華電", code: "2412", side: "買進", qty: 300, price: 129.5 },
];

function ImportCSV() {
  const [uploaded, setUploaded] = useSdi(false);
  return (
    <div style={{ maxWidth: 820 }}>
      <div className="imp-card">
        {/* Step 1 — broker */}
        <div className="imp-section">
          <div className="imp-step-label"><span className="imp-step-num">1</span>選擇券商格式</div>
          <select className="imp-select">
            <option>永豐金證券</option>
            <option>元大證券</option>
            <option>國泰證券</option>
            <option>富邦證券</option>
            <option>通用格式 (日期, 代號, 買賣, 股數, 價格)</option>
          </select>
        </div>

        {/* Step 2 — upload */}
        <div className="imp-section">
          <div className="imp-step-label"><span className="imp-step-num">2</span>上傳 CSV 檔案</div>
          {!uploaded ? (
            <div className="dropzone" onClick={() => setUploaded(true)}>
              <i className="pi pi-cloud-upload"></i>
              將 CSV 拖曳至此，或<span style={{ color: "var(--app-primary)", fontWeight: 700 }}>　選擇檔案</span>
              <div style={{ fontSize: ".74rem", marginTop: ".5rem" }}>支援 .csv · 最大 5MB · UTF-8 編碼</div>
            </div>
          ) : (
            <div className="file-chip">
              <span className="fi"><i className="pi pi-file"></i></span>
              <div style={{ flex: 1 }}>
                <div className="fn">transactions_2026Q2.csv</div>
                <div className="fm">{PARSED.length} 筆交易 · 12.4 KB</div>
              </div>
              <button className="icon-btn" onClick={() => setUploaded(false)}><i className="pi pi-times"></i></button>
            </div>
          )}
        </div>

        {/* Step 3 — preview */}
        {uploaded && (
          <div className="imp-section">
            <div className="imp-step-label"><span className="imp-step-num">3</span>確認交易預覽</div>
            <div style={{ overflowX: "auto" }}>
              <table className="preview-table">
                <thead>
                  <tr><th>日期</th><th>股票</th><th>買賣</th><th className="num">股數</th><th className="num">價格</th><th className="num">金額</th></tr>
                </thead>
                <tbody>
                  {PARSED.map((r, i) => (
                    <tr key={i}>
                      <td className="tnum">{r.date}</td>
                      <td>{r.sym} <span style={{ color: "var(--app-text-muted)", fontSize: ".76rem" }}>{r.code}</span></td>
                      <td><span className={`side-tag ${r.side === "買進" ? "buy" : "sell"}`}>{r.side}</span></td>
                      <td className="num tnum">{r.qty.toLocaleString()}</td>
                      <td className="num tnum">{r.price.toFixed(2)}</td>
                      <td className="num tnum">{ntd(r.qty * r.price)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="dlg-foot" style={{ borderTop: "none" }}>
          <Btn variant="text" onClick={() => setUploaded(false)}>取消</Btn>
          <Btn icon="pi-check" onClick={() => setUploaded(false)}>{uploaded ? `確認匯入 ${PARSED.length} 筆` : "確認匯入"}</Btn>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DividendList, ImportCSV });
