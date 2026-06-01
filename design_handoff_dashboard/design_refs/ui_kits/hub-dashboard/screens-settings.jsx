/* Home Hub UI Kit — Settings (appearance + gain/loss convention) */

function SegToggle({ value, options, onChange }) {
  return (
    <div className="seg-toggle">
      {options.map(([v, label]) => (
        <button key={v} className={value === v ? "active" : ""} onClick={() => onChange(v)}>{label}</button>
      ))}
    </div>
  );
}

function Settings({ dark, setDark, gainLoss, setGainLoss }) {
  return (
    <div className="settings-page">
      <div className="page-head">
        <h2 className="section-title">設定</h2>
      </div>

      {/* Appearance */}
      <div className="set-section">
        <div className="set-section-title">外觀</div>
        <div className="set-card">
          <div className="set-row">
            <div className="set-label">
              <i className={`pi ${dark ? "pi-moon" : "pi-sun"}`}></i>
              <div>
                <div className="t">外觀模式</div>
                <div className="d">選擇淺色或深色主題</div>
              </div>
            </div>
            <SegToggle
              value={dark ? "dark" : "light"}
              options={[["light", "淺色"], ["dark", "深色"]]}
              onChange={v => setDark(v === "dark")}
            />
          </div>
        </div>
      </div>

      {/* Investment display */}
      <div className="set-section">
        <div className="set-section-title">投資顯示</div>
        <div className="set-card">
          <div className="set-row">
            <div className="set-label">
              <i className="pi pi-chart-line"></i>
              <div>
                <div className="t">漲跌顏色</div>
                <div className="d">選擇符合你習慣的市場慣例</div>
              </div>
            </div>
            <SegToggle
              value={gainLoss}
              options={[["asian", "紅漲綠跌"], ["western", "綠漲紅跌"]]}
              onChange={setGainLoss}
            />
          </div>
          <div className="set-row preview">
            <span className="set-sublabel">即時預覽</span>
            <div className="prev-chips">
              <span className="pill-preview pos"><i className="pi pi-arrow-up" style={{ fontSize: ".7rem" }}></i>台積電 +2.41%</span>
              <span className="pill-preview neg"><i className="pi pi-arrow-down" style={{ fontSize: ".7rem" }}></i>聯發科 −1.83%</span>
            </div>
          </div>
        </div>
        <p className="caption" style={{ marginTop: ".75rem", paddingLeft: ".25rem", color: "var(--app-text-muted)", fontSize: ".78rem" }}>
          台股以紅色代表上漲、綠色代表下跌；歐美市場則相反。預設為台股慣例。
        </p>
      </div>
    </div>
  );
}

Object.assign(window, { Settings });
