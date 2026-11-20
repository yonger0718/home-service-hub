import { DebtEvent } from '../../models/accounting.model';
import { slashDate } from './dates';
import { formatMoney } from './format';

/** The callers the owner knows by another name than their token label. */
const ACTOR_NAMES: Record<string, string> = { spa: '網頁', hermes: 'Hermes' };

/** Who closed or reopened a debt (#401), from the caller's token label; empty without a label. */
export function debtActorLabel(actor: string | null): string {
  return actor ? (ACTOR_NAMES[actor] ?? actor) : '';
}

/** One line of a debt's close / reopen record (#401): date and action, who, the remainder the close took out or the
 * reopen brought back (left out when zero), and 待確認 while the close waits for the owner. */
export function debtEventLine(event: DebtEvent): string {
  const close = event.action === 'close';
  const parts = [`${slashDate(event.event_date)} ${close ? '標記結清' : '重新開啟'}`];
  const actor = debtActorLabel(event.actor);
  if (actor) {
    parts.push(actor);
  }
  if (Number(event.open_amount) > 0) {
    parts.push(`${close ? '當時剩餘' : '回到剩餘'} ${formatMoney(event.open_amount, event.currency)}`);
  }
  if (event.needs_review) {
    parts.push('待確認');
  }
  return parts.join(' · ');
}
