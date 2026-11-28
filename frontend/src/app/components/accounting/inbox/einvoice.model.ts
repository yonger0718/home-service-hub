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
  /** The owner's 不用記帳 of this seller (tax id) so far; 0 without one and on questions. Absent on an older API. */
  seller_dismissed?: number;
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
  /** What the linked entry or group records in the invoice's terms (D12 match chip); null for a mixed-currency group. */
  recorded_amount?: string | null;
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
  /** 對帳日期範圍: ± days around the invoice date (0–7). */
  date_window_days: number;
  /** 不用記帳 marks of one seller before 這家以後都不提醒 is offered (1–10). */
  mute_prompt_after: number;
  /** Read only: the newest invoice's first sighting; null before the first import. */
  last_import_at: string | null;
  /** Read only: invoices the matcher linked on its own. */
  auto_linked_count: number;
  version: number;
}

/** The fields `PATCH /einvoices/preferences` accepts (any subset; the server merges and bumps `version`). */
export type EinvoicePreferencesChange = Partial<Pick<EinvoicePreferences, 'remind_unmatched' | 'date_window_days' | 'mute_prompt_after'>>;

export interface EinvoiceStateOut {
  id: number;
  state: string;
  dismiss_reason: string | null;
  linked_by: string | null;
  entry_id: number | null;
  group_id: number | null;
}

/** 換一張發票: the invoice the link left and the one it moved to. */
export interface EinvoiceReplaceOut {
  previous: EinvoiceStateOut;
  current: EinvoiceStateOut;
}

export type EinvoiceLinkTarget = { entry_id: number } | { group_id: number };
