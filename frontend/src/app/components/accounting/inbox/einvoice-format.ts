import { formatMoney } from '../format';

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
