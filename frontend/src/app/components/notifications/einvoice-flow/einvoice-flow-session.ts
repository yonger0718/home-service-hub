import { Injectable, inject, signal } from '@angular/core';
import { NavigationEnd, Router } from '@angular/router';
import { filter } from 'rxjs';

import { InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { EINVOICE_FLOW_SEGMENT } from '../review-flow-url';

/** What the owner did with an invoice in this flow. */
export type EinvoiceOutcome = 'linked' | 'dismissed' | 'recorded';

/**
 * The flow's order (the ambiguous / near invoices at the first read), the invoices still to decide (their latest
 * data), the answers given here, the skipped ones, the ones whose answer was undone here (`restored`: open until
 * answered again, whatever a read lists) and the invoice shown (`cursor`, null = the end state).
 */
export interface EinvoiceFlowState {
  readonly order: readonly number[];
  readonly open: ReadonlyMap<number, InboxEinvoice>;
  readonly outcome: ReadonlyMap<number, EinvoiceOutcome>;
  readonly skipped: ReadonlySet<number>;
  readonly restored: ReadonlySet<number>;
  readonly cursor: number | null;
}

function asking(invoices: readonly InboxEinvoice[]): InboxEinvoice[] {
  return invoices.filter((invoice) => invoice.state !== 'unmatched');
}

/** The first open, unskipped invoice after `from` in order (from the start when `from` is null), else null. */
function nextFrom(state: EinvoiceFlowState, from: number | null): number | null {
  const start = from === null ? 0 : state.order.indexOf(from) + 1;
  return state.order.slice(start).find((id) => state.open.has(id) && !state.skipped.has(id)) ?? null;
}

export function startFlow(invoices: readonly InboxEinvoice[]): EinvoiceFlowState {
  const list = asking(invoices);
  const state: EinvoiceFlowState = {
    order: list.map((invoice) => invoice.id),
    open: new Map(list.map((invoice) => [invoice.id, invoice])),
    outcome: new Map(),
    skipped: new Set(),
    restored: new Set(),
    cursor: null,
  };
  return { ...state, cursor: nextFrom(state, null) };
}

/**
 * A later read: invoices no longer listed and not answered here left elsewhere (another tab, the inbox panel) and
 * drop out of the flow; open ones take the new data; new invoices are not added (they show on the next open). An
 * invoice listed as `unmatched` (an answer undone here: the API puts it back to unmatched until the next matcher run)
 * stays open with the data the flow already has. `complete` false (the read's page was cut: fewer listed than the
 * count) proves nothing about an absent invoice: it stays, with the data the flow has. Nor does a restored one's
 * absence (the API leaves it `unmatched`, which it does not list when 有發票沒有帳 reminders are off): it stays.
 */
export function afterRead(state: EinvoiceFlowState, invoices: readonly InboxEinvoice[], complete = true): EinvoiceFlowState {
  const listed = new Map(invoices.map((invoice) => [invoice.id, invoice]));
  const order = state.order.filter(
    (id) => state.outcome.has(id) || listed.has(id) || !complete || state.restored.has(id),
  );
  const data = (id: number): InboxEinvoice => {
    const fresh = listed.get(id);
    if (!fresh) return state.open.get(id)!;
    return fresh.state === 'unmatched' ? (state.open.get(id) ?? fresh) : fresh;
  };
  const open = new Map(order.filter((id) => !state.outcome.has(id)).map((id) => [id, data(id)]));
  const skipped = new Set([...state.skipped].filter((id) => open.has(id)));
  const next: EinvoiceFlowState = { ...state, order, open, skipped };
  if (state.cursor !== null && open.has(state.cursor)) {
    return next;
  }
  // The shown invoice left: the next open one after it (by the old order), else the first open one.
  const after = state.cursor === null ? null : state.order.slice(state.order.indexOf(state.cursor) + 1).find((id) => open.has(id) && !skipped.has(id));
  return { ...next, cursor: state.cursor === null ? null : after ?? nextFrom(next, null) };
}

export function answer(state: EinvoiceFlowState, id: number, outcome: EinvoiceOutcome): EinvoiceFlowState {
  const open = new Map(state.open);
  open.delete(id);
  const restored = new Set(state.restored);
  restored.delete(id);
  const next: EinvoiceFlowState = { ...state, open, restored, outcome: new Map(state.outcome).set(id, outcome) };
  return { ...next, cursor: nextFrom(next, id) ?? nextFrom(next, null) };
}

/** 復原: the answer is undone; the invoice is open again (with the data it had) and shown. */
export function restore(state: EinvoiceFlowState, invoice: InboxEinvoice): EinvoiceFlowState {
  if (!state.order.includes(invoice.id)) return state;
  const outcome = new Map(state.outcome);
  outcome.delete(invoice.id);
  return {
    ...state,
    outcome,
    open: new Map(state.open).set(invoice.id, invoice),
    restored: new Set(state.restored).add(invoice.id),
    cursor: invoice.id,
  };
}

export function skip(state: EinvoiceFlowState): EinvoiceFlowState {
  if (state.cursor === null) return state;
  const next: EinvoiceFlowState = { ...state, skipped: new Set(state.skipped).add(state.cursor) };
  return { ...next, cursor: nextFrom(next, state.cursor) ?? nextFrom(next, null) };
}

/** ← / →: the previous / next open invoice (skipped ones included); unchanged at either end. */
export function move(state: EinvoiceFlowState, by: -1 | 1): EinvoiceFlowState {
  if (state.cursor === null) return state;
  const open = state.order.filter((id) => state.open.has(id));
  const target = open[open.indexOf(state.cursor) + by];
  return target === undefined ? state : { ...state, cursor: target };
}

export function resume(state: EinvoiceFlowState): EinvoiceFlowState {
  const next: EinvoiceFlowState = { ...state, skipped: new Set() };
  return { ...next, cursor: nextFrom(next, null) };
}

/** `position` of the shown invoice in the flow (1-based; 0 at the end), the flow's size, answered and skipped. */
export function progress(state: EinvoiceFlowState): { position: number; total: number; handled: number; skipped: number } {
  return {
    position: state.cursor === null ? 0 : state.order.indexOf(state.cursor) + 1,
    total: state.order.length,
    handled: state.outcome.size,
    skipped: state.skipped.size,
  };
}

const ENTRY_FORM_PATH = '/accounting/entry';
const FLOW_PATH = `/notifications/${EINVOICE_FLOW_SEGMENT}`;

/**
 * The flow's state across the 記成新的一筆 round trip: the entry form is another route, so the flow (and the page)
 * unmount and mount again on the way back. `pendingRecord` is the invoice sent to the form (id and number) with
 * the `AccountingService.invoiceCreateMark()` at that moment; a navigation anywhere but the form or the flow
 * drops it.
 */
@Injectable({ providedIn: 'root' })
export class EinvoiceFlowSession {
  readonly state = signal<EinvoiceFlowState | null>(null);
  readonly pendingRecord = signal<{ id: number; number: string; mark: number } | null>(null);

  constructor() {
    inject(Router).events.pipe(filter((event) => event instanceof NavigationEnd)).subscribe((event) => {
      const path = (event as NavigationEnd).urlAfterRedirects.split(/[?#]/)[0];
      if (path !== ENTRY_FORM_PATH && path !== FLOW_PATH) {
        this.pendingRecord.set(null);
      }
    });
  }

  awaitRecord(id: number, number: string, mark: number): void {
    this.pendingRecord.set({ id, number, mark });
  }
}
