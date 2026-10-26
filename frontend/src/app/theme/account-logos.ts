/** Bundled institution logos (#175): the manifest is the contract with `src/assets/logos/` (see account-logos.spec.ts).
 *  Order follows the design §4 slug table (left column, then right column); slugs with no obtainable logo are omitted
 *  and their accounts keep their emoji. Files are written by tools/account-logos/normalize.py; provenance in
 *  tools/account-logos/SOURCES.md. */
export interface AccountLogo {
  /** Stored in `account.logo`; also the first segment of the file name. */
  slug: string;
  /** The institution's short name as the owner uses it. */
  label: string;
  /** File name under `assets/logos/`: `<slug>.<sha256[:8]>.<ext>` — content-hashed so the SPA publisher's
   *  immutable-asset rule lets a re-cropped mark ship under a new name. */
  file: string;
}

/** Slug shape shared with the backend's `account.logo` validation. */
export const LOGO_SLUG = /^[a-z0-9-]{1,32}$/;

export const ACCOUNT_LOGOS: readonly AccountLogo[] = [
  { slug: 'standard-chartered', label: '渣打', file: 'standard-chartered.ba51dd5b.svg' },
  { slug: 'bank-of-taiwan', label: '臺灣銀行', file: 'bank-of-taiwan.036c6c11.svg' },
  { slug: 'ctbc', label: '中國信託', file: 'ctbc.9c3f4c12.png' },
  { slug: 'taishin', label: '台新', file: 'taishin.3231fd5c.png' },
  { slug: 'cathay-united', label: '國泰世華', file: 'cathay-united.a5495cc3.svg' },
  { slug: 'hsbc', label: '匯豐', file: 'hsbc.1cceb970.svg' },
  { slug: 'sinopac', label: '永豐', file: 'sinopac.af59d330.svg' },
  { slug: 'hua-nan', label: '華南', file: 'hua-nan.a241aaf1.png' },
  { slug: 'yuanta', label: '元大', file: 'yuanta.026c52e5.svg' },
  { slug: 'taipei-fubon', label: '台北富邦', file: 'taipei-fubon.b15991ee.svg' },
  { slug: 'line-bank', label: 'LINE Bank', file: 'line-bank.8be8c16b.png' },
  { slug: 'amex', label: '美國運通', file: 'amex.d82071f5.svg' },
  { slug: 'chunghwa-post', label: '中華郵政', file: 'chunghwa-post.ae5fe592.svg' },
  { slug: 'chang-hwa', label: '彰銀', file: 'chang-hwa.c8d432ab.png' },
  { slug: 'starbucks', label: '星巴克', file: 'starbucks.db870aa1.png' },
  { slug: 'kgi', label: '凱基', file: 'kgi.ed7145d4.svg' },
  { slug: 'richart', label: '台新 Richart', file: 'richart.3231fd5c.png' },
  { slug: 'line-points', label: 'LINE Points', file: 'line-points.2c157824.png' },
  { slug: 'dawho', label: '永豐 DAWHO', file: 'dawho.148e20c9.svg' },
  { slug: 'rakuten-bank', label: '樂天銀行', file: 'rakuten-bank.74c32ee7.png' },
  { slug: 'sinopac-securities', label: '永豐金證券', file: 'sinopac-securities.8026079d.png' },
  {
    slug: 'interactive-brokers',
    label: 'Interactive Brokers',
    file: 'interactive-brokers.404e2650.png',
  },
  { slug: 'firstrade', label: 'Firstrade', file: 'firstrade.7b650d3c.png' },
  { slug: 'dbs', label: '星展', file: 'dbs.7c8d86f6.svg' },
  { slug: 'next-bank', label: '將來銀行', file: 'next-bank.a5c713f7.png' },
  { slug: 'cota', label: '高雄三信（三信商業銀行）', file: 'cota.d56d3816.png' },
  { slug: 'charles-schwab', label: 'Charles Schwab', file: 'charles-schwab.4826477b.svg' },
];

const BY_SLUG = new Map(ACCOUNT_LOGOS.map((l) => [l.slug, l]));

/** The manifest entry for `slug`, or undefined for null / unknown slugs. */
export function accountLogo(slug: string | null | undefined): AccountLogo | undefined {
  return slug ? BY_SLUG.get(slug) : undefined;
}
