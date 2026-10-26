# Institution logos — sources

Every logo in this directory is a trademark of its owner. They are bundled only for this household's private
HomeHub app, so accounts show their institution's mark, and are not redistributed.

Each file was downloaded into a scratch directory outside the repo, checked with `file`, and written here only by
`tools/account-logos/normalize.py` (SVG: rules in `tools/account-logos/svg-rules.json`, metadata and editor data
dropped, mark wrapped in a square viewBox with 8 % padding; PNG: square ≥ 180 px, only IHDR/PLTE/tRNS/IDAT/IEND
kept). Before normalising, some downloads were pre-cleaned in the scratch directory: the standard SVG DOCTYPE and
comments removed, class-only `<style>` rules inlined as `style=""`, wordmark text removed and the viewBox cropped
to the symbol, or a raster cropped / padded to a square. Colours were not changed. All fetched 2026-10-26.

| slug | institution | source URL | fetched | notes |
|---|---|---|---|---|
| `standard-chartered` | 渣打 | https://commons.wikimedia.org/wiki/File:Standard_Chartered_Logo_(2021,_Logo_only).svg | 2026-10-26 | Commons SVG, symbol only |
| `bank-of-taiwan` | 臺灣銀行 | https://commons.wikimedia.org/wiki/File:Bank_of_Taiwan_Seal.svg | 2026-10-26 | Commons SVG; matches the mark in the official header logo https://www.bot.com.tw/assets/images/Logo/Logo(red).png |
| `ctbc` | 中國信託 | https://www.ctbcbank.com/content/dam/twrbo/images/touch/apple-touch-icon-180x180.png | 2026-10-26 | official apple-touch-icon PNG 180 px (the "We are Family" ball used in the site header); Commons `CTBC-logo.svg` is a different company |
| `taishin` | 台新 | https://www.taishinbank.com.tw/TSB/export/system/modules/com.thesys.project.TSB/resources/img/ts_FB.png | 2026-10-26 | official share image (600×314) cropped to the mark and padded transparent to 344 px square |
| `cathay-united` | 國泰世華 | https://commons.wikimedia.org/wiki/File:Cathay_United_Bank_logo.svg | 2026-10-26 | Commons SVG; wordmark removed, viewBox cropped to the tree symbol |
| `hsbc` | 匯豐 | https://commons.wikimedia.org/wiki/File:HSBC_滙豐_(logo_only).svg | 2026-10-26 | Commons SVG, hexagon only; `<style>` inlined |
| `sinopac` | 永豐 | https://commons.wikimedia.org/wiki/File:Bank_SinoPac_logo_20121103.svg | 2026-10-26 | Commons SVG |
| `hua-nan` | 華南 | https://www.hncb.com.tw/hncb/asset_v2/favicon/apple-touch-icon.png | 2026-10-26 | official apple-touch-icon PNG 180 px |
| `yuanta` | 元大 | https://commons.wikimedia.org/wiki/File:元大金控logo.svg | 2026-10-26 | Commons SVG; wordmark removed, viewBox cropped to the symbol; `<style>` inlined |
| `taipei-fubon` | 台北富邦 | https://www.fubon.com/financialholdings/Content/img/home/logo.svg | 2026-10-26 | official SVG (Fubon Financial header); wordmark removed, viewBox cropped to the shared Fubon symbol |
| `line-bank` | LINE Bank | https://www.linebank.com.tw/assets/img/logo-02@3x.74d4df8c.png | 2026-10-26 | official site PNG 240 px, padded transparent to 280 px |
| `amex` | 美國運通 | https://www.aexp-static.com/cdaas/one/statics/axp-static-assets/1.8.0/package/dist/img/logos/dls-logo-bluebox-solid.svg | 2026-10-26 | official SVG (americanexpress.com header); hidden white backing square removed; `<style>` inlined |
| `chunghwa-post` | 中華郵政 | https://commons.wikimedia.org/wiki/File:Chunghwa_Post_Logo.svg | 2026-10-26 | Commons SVG; DOCTYPE and four fully transparent tracing paths removed |
| `chang-hwa` | 彰銀 | https://www.bankchb.com/chb_2a_resource/leap_do/gallery/1581756833131/%E5%BD%B0%E5%8C%96%E9%8A%80%E8%A1%8Clogo.png | 2026-10-26 | official og:image PNG (1000×500) cropped to the CHB mark, 236 px square, white background kept |
| `starbucks` | 星巴克 | https://www.starbucks.com/pwa-icon.png | 2026-10-26 | official PWA icon PNG 512 px (starbucks.com manifest) |
| `kgi` | 凱基 | https://www.kgibank.com.tw/zh-tw/-/media/images/kgib/header/logo-kgib.svg | 2026-10-26 | official header SVG; wordmark removed, viewBox cropped to the square symbol |
| `richart` | 台新 Richart | https://www.taishinbank.com.tw/TSB/export/system/modules/com.thesys.project.TSB/resources/img/ts_FB.png | 2026-10-26 | copy of `taishin.png`: richart.tw only serves a 340×100 wordmark PNG (dog mark under 180 px) and no SVG |
| `line-points` | LINE Points | https://static.line-scdn.net/line_pointclub_assets/edge/1.48.0/images/home_icon.png | 2026-10-26 | official home-screen icon PNG 256 px (points.line.me) |
| `dawho` | 永豐 DAWHO | https://dawho.tw/images/img_logo.svg | 2026-10-26 | official header SVG; viewBox cropped to the 大 mark (wordmark outside the crop) |
| `rakuten-bank` | 樂天銀行 | https://www.rakuten-bank.com.tw/apple-touch-icon.png | 2026-10-26 | official apple-touch-icon PNG 180 px |
| `sinopac-securities` | 永豐金證券 | https://www.sinotrade.com.tw/newweb/images/icons/apple-touch-icon.png | 2026-10-26 | official apple-touch-icon PNG 180 px (the site's logo.svg is a black single-colour trace) |
| `interactive-brokers` | Interactive Brokers | https://www.interactivebrokers.com/images/web/favicons/home-screen-icon-192x192.png | 2026-10-26 | official home-screen icon PNG 192 px |
| `firstrade` | Firstrade | https://www.firstrade.com/apple-touch-icon.png | 2026-10-26 | official apple-touch-icon PNG 180 px |
| `dbs` | 星展 | https://www.dbs.com.sg/iwov-resources/media/images/redesign/navbar-logo-dbs.svg | 2026-10-26 | official DBS group header SVG (dbs.com.tw answers 403); "DBS" lettering removed, viewBox cropped to the star symbol |
| `next-bank` | 將來銀行 | https://www.nextbank.com.tw/images/favicon-192.png | 2026-10-26 | official icon PNG 192 px (the site's header SVG holds only the wordmark) |
| `cota` | 高雄三信（三信商業銀行） | https://www.cotabank.com.tw/web/wp-content/uploads/2016/03/cropped-favicon-180x180.png | 2026-10-26 | official site icon PNG 180 px |
| `charles-schwab` | Charles Schwab | https://commons.wikimedia.org/wiki/File:Charles_Schwab_Corporation_logo.svg | 2026-10-26 | Commons SVG (schwab.com refuses scripted requests) |

Not bundled (accounts keep their emoji): `union-bank` 聯邦 (ubot.com.tw is a script-rendered SPA whose bundles
reference no logo file; only a 24 px favicon; nothing on Commons), `china-airlines` 華航 (the official site serves the
logo only as WebP; no Commons file of the plum-blossom mark), `pionex` Pionex (pionex.com answers 403; the
pionex.us icon carries a "U.S." band; nothing on Commons).
