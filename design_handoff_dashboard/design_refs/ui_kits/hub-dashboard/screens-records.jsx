/* Home Hub UI Kit — transaction timeline & inventory screens */
const { useState: useSt } = React;

const TXNS = [
  { id: 1, date: "2026-05-28", day: "28", mon: "MAY", item: "午餐 · 鼎泰豐", cat: "餐飲", pay: "國泰 CUBE 卡", amt: 420, type: "EXPENSE" },
  { id: 2, date: "2026-05-28", day: "28", mon: "MAY", item: "捷運儲值", cat: "交通", pay: "悠遊卡", amt: 500, type: "EXPENSE" },
  { id: 3, date: "2026-05-27", day: "27", mon: "MAY", item: "Netflix 訂閱", cat: "娛樂", pay: "玉山 Pi 卡", amt: 390, type: "EXPENSE", note: "家庭方案" },
  { id: 4, date: "2026-05-25", day: "25", mon: "MAY", item: "五月薪資", cat: "薪資", pay: "薪轉戶", amt: 62000, type: "INCOME" },
  { id: 5, date: "2026-05-24", day: "24", mon: "MAY", item: "全聯採購", cat: "居家", pay: "台新 @GoGo 卡", amt: 1280, type: "EXPENSE" },
  { id: 6, date: "2026-05-22", day: "22", mon: "MAY", item: "0050 股利", cat: "投資", pay: "證券戶", amt: 4100, type: "INCOME" },
];

function TransactionList() {
  const [type, setType] = useSt("ALL");
  const [q, setQ] = useSt("");
  const filtered = TXNS.filter(t => (type === "ALL" || t.type === type) && (!q || t.item.includes(q) || t.cat.includes(q)));
  const expense = TXNS.filter(t => t.type === "EXPENSE").reduce((s, t) => s + t.amt, 0);
  const income = TXNS.filter(t => t.type === "INCOME").reduce((s, t) => s + t.amt, 0);
  let lastDay = null;

  return (
    <div>
      <div className="page-head" style={{ justifyContent: "flex-end" }}>
        <div className="head-actions">
          <Btn variant="secondary" icon="pi-sync">同步定期交易</Btn>
          <Btn icon="pi-plus" onClick={() => window.__openTxnDialog && window.__openTxnDialog()}>新增交易</Btn>
        </div>
      </div>

      <div className="month-nav">
        <button className="nav-btn"><i className="pi pi-chevron-left"></i></button>
        <div className="cur">2026年5月</div>
        <button className="nav-btn"><i className="pi pi-chevron-right"></i></button>
        <button className="reset">回到本月</button>
      </div>

      <div className="summary-row">
        <div className="summary-item expense"><div className="l">支出</div><div className="v">-{ntd(expense)}</div></div>
        <div className="summary-item income"><div className="l">收入</div><div className="v">+{ntd(income)}</div></div>
        <div className="summary-item net"><div className="l">結餘</div><div className="v">{ntd(income - expense)}</div></div>
      </div>

      <div className="filter-bar">
        <div className="search-wrap">
          <i className="pi pi-search"></i>
          <input placeholder="搜尋項目、備註..." value={q} onChange={e => setQ(e.target.value)} />
        </div>
        <div className="filter-controls">
          <div className="type-pills">
            {[["ALL", "全部"], ["EXPENSE", "支出"], ["INCOME", "收入"]].map(([v, l]) => (
              <button key={v} className={type === v ? "active" : ""} onClick={() => setType(v)}>{l}</button>
            ))}
          </div>
        </div>
      </div>

      <div className="timeline">
        {filtered.map(t => {
          const newDay = t.day !== lastDay; lastDay = t.day;
          return (
            <div className="tl-item" key={t.id}>
              <div className="tl-date">{newDay && (<><span className="d">{t.day}</span><span className="m">{t.mon}</span></>)}</div>
              <div className="tl-card">
                <div>
                  <div className="tl-name">{t.item}</div>
                  <div className="tl-meta"><span>{t.cat}</span><span className="dot">•</span><span>{t.pay}</span></div>
                </div>
                <span className={`tl-amt ${t.type === "EXPENSE" ? "expense" : "income"}`}>
                  {t.type === "EXPENSE" ? "−" : "+"}{ntd(t.amt)}
                </span>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <div className="empty"><i className="pi pi-receipt"></i><h4>無交易紀錄</h4><p>本月尚未有任何入帳，開始記錄你的第一筆消費吧！</p></div>
        )}
      </div>
    </div>
  );
}

/* ===================== INVENTORY ======================================== */
const ITEMS = [
  { id: 1, name: "衛生紙", cat: "日用品", loc: "儲藏室", qty: 8, target: 12, status: "OK" },
  { id: 2, name: "洗碗精", cat: "清潔", loc: "廚房", qty: 1, target: 6, status: "LOW" },
  { id: 3, name: "咖啡豆", cat: "食品", loc: "廚房", qty: 3, target: 4, status: "OK" },
  { id: 4, name: "牙膏", cat: "盥洗", loc: "浴室", qty: 0, target: 3, status: "OUT" },
  { id: 5, name: "礦泉水", cat: "飲品", loc: "儲藏室", qty: 18, target: 24, status: "OK" },
  { id: 6, name: "洗衣精", cat: "清潔", loc: "陽台", qty: 2, target: 4, status: "LOW" },
];
const STATUS = { OK: ["充足", "ok"], LOW: ["低庫存", "low"], OUT: ["缺貨", "low"] };

function InventoryGrid() {
  const [lowOnly, setLow] = useSt(false);
  const [items, setItems] = useSt(ITEMS);
  const shown = items.filter(i => !lowOnly || i.status !== "OK");
  const adjust = (id, d) => setItems(items.map(i => {
    if (i.id !== id) return i;
    const qty = Math.max(0, i.qty + d);
    const status = qty === 0 ? "OUT" : qty <= i.target * 0.34 ? "LOW" : "OK";
    return { ...i, qty, status };
  }));

  return (
    <div>
      <div className="page-head" style={{ justifyContent: "flex-end" }}>
        <div className="head-actions"><Btn icon="pi-plus">新增物品</Btn></div>
      </div>
      <div className="filter-bar">
        <div className="search-wrap"><i className="pi pi-search"></i><input placeholder="搜尋物品名稱、類別或位置..." /></div>
        <div className="filter-controls">
          <button className={`toggle-pill${lowOnly ? " on" : ""}`} onClick={() => setLow(!lowOnly)}>
            <i className="pi pi-exclamation-triangle"></i> 只看低庫存
          </button>
        </div>
      </div>
      <div className="inv-grid">
        {shown.map(it => {
          const [lbl, badge] = STATUS[it.status];
          const lowish = it.status !== "OK";
          const fillPct = Math.min(100, Math.round((it.qty / it.target) * 100));
          return (
            <div className={`inv-card${lowish ? " low" : ""}`} key={it.id}>
              <div className="inv-vis">
                <div className="inv-ph"><i className="pi pi-box"></i></div>
                <span className={`inv-badge ${badge}`}>{lbl}</span>
              </div>
              <div className="inv-name">{it.name}</div>
              <div className="inv-tags">
                <span className="tag secondary"><i className="pi pi-tag"></i> {it.cat}</span>
                <span className="tag secondary"><i className="pi pi-map-marker"></i> {it.loc}</span>
              </div>
              <div className="inv-qty"><span className="cur">{it.qty}</span><span className="tgt">/ {it.target}</span></div>
              <div className="inv-bar"><i style={{ width: fillPct + "%", background: lowish ? "var(--app-danger)" : "var(--app-success)" }}></i></div>
              <div className="inv-actions">
                <div className="grp">
                  <button className="icon-btn neg" title="使用 -1" onClick={() => adjust(it.id, -1)}><i className="pi pi-minus"></i></button>
                  <button className="icon-btn pos" title="補貨 +1" onClick={() => adjust(it.id, 1)}><i className="pi pi-plus"></i></button>
                </div>
                <div className="grp">
                  <button className="icon-btn" title="歷史"><i className="pi pi-history"></i></button>
                  <button className="icon-btn" title="編輯"><i className="pi pi-pencil"></i></button>
                  <button className="icon-btn" title="刪除"><i className="pi pi-trash"></i></button>
                </div>
              </div>
            </div>
          );
        })}
        {shown.length === 0 && (<div className="empty" style={{ gridColumn: "1/-1" }}><i className="pi pi-box"></i><h4>空空如也</h4><p>目前沒有符合條件的庫存物品。</p></div>)}
      </div>
    </div>
  );
}

/* ---- Add transaction dialog -------------------------------------------- */
function TxnDialog({ open, onClose }) {
  const [t, setT] = useSt("EXPENSE");
  if (!open) return null;
  return (
    <div className="dlg-mask" onClick={onClose}>
      <div className="dlg" onClick={e => e.stopPropagation()}>
        <div className="dlg-head"><h3>新增交易</h3><Btn variant="text" icon="pi-times" onClick={onClose}></Btn></div>
        <div className="dlg-body">
          <div className="field full">
            <label>交易類型</label>
            <div className="seg">
              {[["EXPENSE", "支出"], ["INCOME", "收入"]].map(([v, l]) => (
                <button key={v} className={t === v ? "active" : ""} onClick={() => setT(v)}>{l}</button>
              ))}
            </div>
          </div>
          <div className="field full"><label>標題</label><input placeholder="例如：午餐、Netflix 訂閱" /></div>
          <div className="field"><label>日期</label><input type="date" defaultValue="2026-05-29" /></div>
          <div className="field"><label>分類</label><select><option>餐飲</option><option>交通</option><option>居家</option><option>娛樂</option></select></div>
          <div className="field"><label>支付方式</label><select><option>國泰 CUBE 卡</option><option>玉山 Pi 卡</option><option>現金</option></select></div>
          <div className="field"><label>交易金額</label><input placeholder="NT$0" /></div>
          <div className="field full"><label>備註</label><input /></div>
        </div>
        <div className="dlg-foot"><Btn variant="text" onClick={onClose}>取消</Btn><Btn onClick={onClose}>儲存交易</Btn></div>
      </div>
    </div>
  );
}

Object.assign(window, { TransactionList, InventoryGrid, TxnDialog });
