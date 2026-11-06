/** E-invoice linking (spec §7–§9). Field names follow the accounting-service schemas; money arrives as strings. */

export type EinvoiceState = 'unmatched' | 'ambiguous' | 'near' | 'linked' | 'dismissed' | 'void';

export interface EinvoiceItem {
  name: string;
  subtotal: string;
}

/** A ledger entry or split group an invoice can be linked to. `amount` is the net. */
export interface EinvoiceTarget {
  kind: 'entry' | 'group';
  id: number;
  account_name: string;
  date: string;
  amount: string;
  currency: string;
  category: string | null;
  name: string | null;
}

export interface EinvoiceSummary {
  id: number;
  number: string;
  issue_date: string;
  seller_name: string;
  seller_tax_id: string;
  total_amount: string;
  status_raw: string;
  state: string;
  items: EinvoiceItem[];
}

export interface EinvoiceDismissed extends EinvoiceSummary {
  dismiss_reason: 'owner' | 'runbook';
}

/** One inbox question: an invoice the matcher could not settle on its own. */
export interface InboxEinvoice extends EinvoiceSummary {
  state: 'ambiguous' | 'near' | 'unmatched';
  candidates: EinvoiceTarget[];
  offer_mute: boolean;
}

/** The invoice linked to an entry directly or through its split group. */
export interface EntryEinvoice {
  id: number;
  number: string;
  issue_date: string;
  seller_name: string;
  total_amount: string;
  linked_by: 'auto' | 'owner';
  linked_to: 'entry' | 'group';
  items: EinvoiceItem[];
}

export interface EinvoicePicker {
  entry_eligible: boolean;
  group_id: number | null;
  group_eligible: boolean;
  invoices: EinvoiceSummary[];
}

export interface EinvoiceMute {
  seller_tax_id: string;
  seller_name: string | null;
  created_at: string;
}

export interface EinvoicePreferences {
  enabled: boolean;
  remind_unmatched: boolean;
  version: number;
}

export interface EinvoiceStateOut {
  id: number;
  state: string;
  dismiss_reason: string | null;
  linked_by: string | null;
  entry_id: number | null;
  group_id: number | null;
}

export type EinvoiceLinkTarget = { entry_id: number } | { group_id: number };
