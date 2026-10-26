/** Bundled institution logos (#175): the manifest is the contract with `src/assets/logos/` (see account-logos.spec.ts).
 *  Order follows the design §4 slug table (left column, then right column); slugs with no obtainable logo are omitted
 *  and their accounts keep their emoji. Files are written by tools/account-logos/normalize.py; provenance in SOURCES.md. */
export interface AccountLogo {
  /** Stored in `account.logo`; also the file's basename. */
  slug: string;
  /** The institution's short name as the owner uses it. */
  label: string;
  /** File name under `assets/logos/`. */
  file: string;
}

/** Slug shape shared with the backend's `account.logo` validation. */
export const LOGO_SLUG = /^[a-z0-9-]{1,32}$/;

export const ACCOUNT_LOGOS: readonly AccountLogo[] = [
  { slug: 'standard-chartered', label: '渣打', file: 'standard-chartered.svg' },
  { slug: 'bank-of-taiwan', label: '臺灣銀行', file: 'bank-of-taiwan.svg' },
  { slug: 'ctbc', label: '中國信託', file: 'ctbc.png' },
  { slug: 'taishin', label: '台新', file: 'taishin.png' },
  { slug: 'cathay-united', label: '國泰世華', file: 'cathay-united.svg' },
  { slug: 'hsbc', label: '匯豐', file: 'hsbc.svg' },
  { slug: 'sinopac', label: '永豐', file: 'sinopac.svg' },
  { slug: 'hua-nan', label: '華南', file: 'hua-nan.png' },
  { slug: 'yuanta', label: '元大', file: 'yuanta.svg' },
  { slug: 'taipei-fubon', label: '台北富邦', file: 'taipei-fubon.svg' },
  { slug: 'line-bank', label: 'LINE Bank', file: 'line-bank.png' },
  { slug: 'amex', label: '美國運通', file: 'amex.svg' },
  { slug: 'chunghwa-post', label: '中華郵政', file: 'chunghwa-post.svg' },
  { slug: 'chang-hwa', label: '彰銀', file: 'chang-hwa.png' },
  { slug: 'starbucks', label: '星巴克', file: 'starbucks.png' },
  { slug: 'kgi', label: '凱基', file: 'kgi.svg' },
  { slug: 'richart', label: '台新 Richart', file: 'richart.png' },
  { slug: 'line-points', label: 'LINE Points', file: 'line-points.png' },
  { slug: 'dawho', label: '永豐 DAWHO', file: 'dawho.svg' },
  { slug: 'rakuten-bank', label: '樂天銀行', file: 'rakuten-bank.png' },
  { slug: 'sinopac-securities', label: '永豐金證券', file: 'sinopac-securities.png' },
  { slug: 'interactive-brokers', label: 'Interactive Brokers', file: 'interactive-brokers.png' },
  { slug: 'firstrade', label: 'Firstrade', file: 'firstrade.png' },
  { slug: 'dbs', label: '星展', file: 'dbs.svg' },
  { slug: 'next-bank', label: '將來銀行', file: 'next-bank.png' },
  { slug: 'cota', label: '高雄三信（三信商業銀行）', file: 'cota.png' },
  { slug: 'charles-schwab', label: 'Charles Schwab', file: 'charles-schwab.svg' },
];

const BY_SLUG = new Map(ACCOUNT_LOGOS.map((l) => [l.slug, l]));

/** The manifest entry for `slug`, or undefined for null / unknown slugs. */
export function accountLogo(slug: string | null | undefined): AccountLogo | undefined {
  return slug ? BY_SLUG.get(slug) : undefined;
}
