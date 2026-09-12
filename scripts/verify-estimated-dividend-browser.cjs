/* Run against a production build with an already available Playwright/browser.
 * Synthetic HTTP responses only; DB/receipt accounting is tested by pytest.
 * PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_EXECUTABLE=/path/to/chrome node scripts/verify-estimated-dividend-browser.cjs [artifact-dir]
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const output = path.resolve(process.argv[2] || 'browser-evidence');
fs.mkdirSync(output, {recursive: true});
const root = path.resolve('frontend/dist/inventory-ui/browser');
const holding = (market, extra = {}) => ({symbol: '9802', market, name: `Synthetic ${market}`,
  total_quantity: 1000, avg_cost: 100, current_price: 110, market_value: 109517,
  unrealized_pnl: 9517, unrealized_pnl_percent: 9.517, day_change_amount: 1,
  day_change_percent: 1, day_pnl: 1000, total_dividends: 100, pending_dividends_net: 3090,
  estimated_pnl_with_dividends: 12707, estimated_pnl_percent: 12.71,
  total_pnl_with_dividend: 9617, xirr: null, ...extra});
const summary = {total_market_value: 119517, total_assets_twd: 119517, total_cash_twd: 0,
  total_cost: 108000, total_unrealized_pnl: 11517, total_unrealized_pnl_percent: 10.66,
  total_dividends: 100, total_pending_dividends_net: 3090,
  estimated_pnl_with_dividends: 14707, estimated_pnl_percent: 13.62,
  holdings: [holding('TW'), holding('US', {total_quantity: 10, native_currency: 'USD', native_close: 110,
    market_value_native: 1100, avg_cost_native: 100, unrealized_pnl_native: 100,
    total_dividends_native: 0, total_dividends: 0, pending_dividends_net: 0,
    estimated_pnl_with_dividends_native: 100, estimated_pnl_percent_native: 10})],
  market_totals: {
    TW: {currency: 'TWD', market_value: 109517, cost: 100000, unrealized_pnl: 9517,
      recorded_dividends: 100, pending_dividends_net: 3090, estimated_pnl_with_dividends: 12707, estimated_pnl_percent: 12.71},
    US: {currency: 'USD', market_value: 1100, cost: 1000, unrealized_pnl: 100,
      recorded_dividends: 0, pending_dividends_net: 0, estimated_pnl_with_dividends: 100, estimated_pnl_percent: 10}}};
const row = {id: 1, symbol: '9802', market: 'TW', currency: 'TWD', amount: '3090',
  ex_dividend_date: '2026-09-10T00:00:00+08:00', source: 'auto:TWT49U', receipt_status: 'pending',
  revision: 1, cash_dividend_per_share: '3.1000', quantity_at_record_date: '1000', fee: '10', tax: '0'};
const contentTypes = {'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml','.woff2':'font/woff2','.json':'application/json'};
(async () => {
  let browser;
  const evidence = {provider: 'synthetic HTTP fixtures; production Angular bundle', checks: [], errors: []};
  try {
    browser = await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE, headless:true});
    for (const [label,width,height] of [['desktop',1440,1100],['mobile',390,844]]) {
      const context = await browser.newContext({viewport:{width,height}});
      await context.tracing.start({screenshots:true,snapshots:true,sources:true});
      const page = await context.newPage();
      page.on('pageerror', e => evidence.errors.push(e.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const file = path.join(root, decodeURIComponent(url.pathname).replace(/^\/hub\//, '/'));
        const target = file.startsWith(root + path.sep) && fs.existsSync(file) && fs.statSync(file).isFile() ? file : path.join(root,'index.html');
        await route.fulfill({path:target, contentType:contentTypes[path.extname(target)] || 'application/octet-stream'});
      });
      let confirmed = false;
      await page.route('**/api/**', async route => {
        const url = new URL(route.request().url());
        let body = [];
        if (url.pathname.endsWith('/summary')) body = summary;
        else if (url.pathname.includes('confirm-receipt')) {
          assert.deepEqual(route.request().postDataJSON(), {receipt_date:'2026-09-11',account_id:1,revision:1});
          confirmed = true; body = {...row, receipt_status:'confirmed'};
        } else if (url.pathname.endsWith('/dividends')) body = {items:[{...row,receipt_status:confirmed?'confirmed':'pending'}],total:1};
        else if (url.pathname.includes('/accounts')) body = {items:[{id:1,nickname:'Synthetic cash',currency:'TWD',is_active:true}]};
        else if (url.pathname.includes('symbol-names')) body = {};
        else if (url.pathname.includes('refresh')) body = {refresh_scheduled:false};
        await route.fulfill({json:body});
      });
      await page.goto('http://synthetic.test/hub/portfolio');
      await page.locator('.estimated-total').waitFor();
      assert.match(await page.locator('.estimated-total').innerText(),/14,707/);
      assert.match(await page.locator('.stock-stats').first().innerText(),/12,707/);
      for (const [market,expected] of [['TW',/12,707/],['US',/100.00 USD/],['All',/14,707/]]) {
        await page.locator('.market-tabs').getByRole('radio',{name:market,exact:true}).click();
        await page.locator('.estimated-total').filter({hasText:expected}).waitFor();
        assert.match(await page.locator('.estimated-total').innerText(),expected);
      }
      await page.locator('.stock-row').first().click();
      await page.locator('.detail-panel').waitFor();
      assert.match(await page.locator('.detail-panel').innerText(),/待收股利/);
      assert.match(await page.locator('.detail-panel').innerText(),/3,090/);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true,'horizontal overflow');
      await page.screenshot({path:path.join(output,`${label}-dashboard.png`),fullPage:true});
      await page.goto('http://synthetic.test/hub/portfolio/dividends');
      await page.getByText('待確認收款',{exact:true}).waitFor();
      assert.match(await page.locator('.receipt-note').innerText(),/納入預估含息損益/);
      await page.getByRole('button',{name:'確認實際收款',exact:true}).click();
      await page.getByLabel('實際收款日',{exact:true}).fill('2026-09-11');
      await page.getByLabel('收款帳戶',{exact:true}).selectOption({label:'Synthetic cash TWD'});
      await page.getByRole('button',{name:'確認已收到並入帳',exact:true}).click();
      await page.getByText('已確認收款',{exact:true}).waitFor();
      assert.equal(confirmed,true);
      assert.equal(await page.getByRole('button',{name:'確認實際收款',exact:true}).count(),0);
      await page.screenshot({path:path.join(output,`${label}-dividends.png`),fullPage:true});
      evidence.checks.push(`${label}: main/holding estimate, TW/US/All filter, detail expansion, no overflow, pending-only confirmation and reload`);
      await context.tracing.stop({path:path.join(output,`${label}-trace.zip`)});
      await context.close();
    }
    assert.deepEqual(evidence.errors,[]);
    evidence.exit = 0;
  } catch(error) { evidence.exit = 1; evidence.failure = error.stack; process.exitCode = 1; }
  finally {
    if(browser) await browser.close();
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(evidence,null,2));
    console.log(JSON.stringify(evidence,null,2));
  }
})();
