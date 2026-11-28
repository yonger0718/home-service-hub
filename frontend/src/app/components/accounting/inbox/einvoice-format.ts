import { formatMoney } from '../format';
import { apiErrorText, splitApiCode } from '../statements/statement-view';
import { INBOX_GENERIC_ERROR } from './inbox-view';

const TWO_DECIMALS = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/**
 * An invoice amount (spec §7.3): whole values are TWD; a fractional one is a foreign amount of unknown currency,
 * shown as the plain number with 2 decimals and no symbol.
 */
export function invoiceAmount(value: string): string {
  const amount = Number(value);
  return Number.isInteger(amount) ? formatMoney(value, 'TWD') : TWO_DECIMALS.format(amount);
}

/** The anchor of 記帳設定 › 發票, for links that land on that section. */
export const EINVOICE_SETTINGS_FRAGMENT = 'einvoice-settings';

const EINVOICE_ERRORS: Record<string, string> = {
  einvoice_void: '發票已作廢',
  target_ineligible: '這筆帳不能連結',
  not_linked: '已解除連結',
  einvoice_linked: '這張已連到別筆帳',
  einvoice_changed: '這張發票已經變動',
  seller_muted: '這家已設為不提醒',
  retry: '請再試一次',
};

/** `沒成功：…` for a mapped 409 code (`detail` is `<code>` or `<code>: <detail>`), else the generic inbox text. */
export function einvoiceErrorText(err: unknown): string {
  const text = apiErrorText(err);
  const known = text ? EINVOICE_ERRORS[splitApiCode(text).code] : undefined;
  return known ? `沒成功：${known}` : INBOX_GENERIC_ERROR;
}
