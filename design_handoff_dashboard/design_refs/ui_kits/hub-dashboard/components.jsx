/* Home Hub UI Kit — shared primitives & nav shell */
const { useState } = React;

/* ---- Primitives --------------------------------------------------------- */
function Btn({ variant = "primary", icon, children, className = "", ...p }) {
  const cls = `btn btn-${variant}${icon && !children ? " btn-icon" : ""} ${className}`;
  return (
    <button className={cls} {...p}>
      {icon && <i className={`pi ${icon}`}></i>}
      {children}
    </button>
  );
}

function Tag({ kind = "secondary", icon, children }) {
  return (
    <span className={`tag ${kind}`}>
      {icon && <i className={`pi ${icon}`}></i>}
      {children}
    </span>
  );
}

/* TWD currency, no decimals */
function ntd(n) {
  const neg = n < 0;
  const s = "NT$" + Math.abs(Math.round(n)).toLocaleString("en-US");
  return neg ? "-" + s : s;
}
function pct(n) {
  return (n > 0 ? "+" : "") + n.toFixed(2) + "%";
}

/* ---- Navigation shell --------------------------------------------------- */
const NAV = [
  { id: "inventory", icon: "pi-box", label: "庫存", group: "supplies", title: "庫存管理" },
  { id: "shopping", icon: "pi-shopping-cart", label: "採買", group: "supplies", sub: true, title: "採買清單" },
  { divider: true },
  { id: "portfolio", icon: "pi-chart-line", label: "投資", group: "portfolio", title: "投資概覽" },
  { id: "transactions", icon: "pi-list", label: "交易", group: "portfolio", sub: true, title: "股票交易紀錄" },
  { id: "dividends", icon: "pi-percentage", label: "股利", group: "portfolio", sub: true, title: "股利領取紀錄" },
  { id: "import", icon: "pi-upload", label: "匯入", group: "portfolio", sub: true, title: "匯入 CSV" },
  { divider: true },
  { id: "accounting-dash", icon: "pi-chart-pie", label: "分析", group: "accounting", sub: true, title: "記帳分析" },
  { id: "accounting", icon: "pi-wallet", label: "財務", group: "accounting", title: "交易紀錄" },
  { id: "settings", icon: "pi-cog", label: "設定", group: "accounting", sub: true, title: "設定" },
];

const TITLES = Object.fromEntries(NAV.filter(n => n.id).map(n => [n.id, n.title]));

function Dock({ active, onNav }) {
  return (
    <aside className="hub-dock">
      <div className="dock-logo"><i className="pi pi-home"></i></div>
      <nav className="dock-nav">
        {NAV.map((n, i) =>
          n.divider ? (
            <div className="dock-divider" key={"d" + i}></div>
          ) : (
            <button
              key={n.id}
              className={`dock-item${n.sub ? " sub" : ""}${active === n.id ? " active" : ""}`}
              title={n.title}
              onClick={() => onNav(n.id)}
            >
              <i className={`pi ${n.icon}`}></i>
            </button>
          )
        )}
      </nav>
    </aside>
  );
}

/* mobile bottom tab bar + segmented sub-nav */
const GROUPS = {
  supplies: [["inventory", "庫存"], ["shopping", "採買"]],
  portfolio: [["portfolio", "概覽"], ["transactions", "交易"], ["dividends", "股利"], ["import", "匯入"]],
  accounting: [["accounting", "紀錄"], ["accounting-dash", "分析"], ["settings", "管理"]],
};
const GROUP_OF = Object.fromEntries(NAV.filter(n => n.id).map(n => [n.id, n.group]));

function MobileNav({ active, onNav }) {
  const group = GROUP_OF[active] || "supplies";
  return (
    <div className="mobile-nav-shell">
      <div className="m-subnav">
        <div className="seg-control">
          {GROUPS[group].map(([id, lbl]) => (
            <button key={id} className={active === id ? "active" : ""} onClick={() => onNav(id)}>{lbl}</button>
          ))}
        </div>
      </div>
      <nav className="m-tabbar">
        <button className={`m-tab${group === "supplies" ? " active" : ""}`} onClick={() => onNav("inventory")}>
          <i className="pi pi-box"></i><span>物資</span>
        </button>
        <button className={`m-tab${group === "portfolio" ? " active" : ""}`} onClick={() => onNav("portfolio")}>
          <i className="pi pi-chart-line"></i><span>投資</span>
        </button>
        <button className={`m-tab${group === "accounting" ? " active" : ""}`} onClick={() => onNav("accounting")}>
          <i className="pi pi-wallet"></i><span>財務</span>
        </button>
      </nav>
    </div>
  );
}

Object.assign(window, { Btn, Tag, ntd, pct, Dock, MobileNav, NAV, TITLES, NavData: { GROUPS, GROUP_OF } });
