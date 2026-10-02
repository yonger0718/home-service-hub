export type EntryKind =
  | 'expense'
  | 'income'
  | 'transfer_out'
  | 'transfer_in'
  | 'receivable'
  | 'payable'
  | 'balance_adjustment'
  | 'fee'
  | 'discount'
  | 'reward'
  | 'interest'
  | 'refund';

export interface LedgerAccount {
  id: number;
  name: string;
  currency: string;
  opening_balance: string;
  balance: string;
  entry_count: number;
}

export interface LedgerEntry {
  id: number;
  kind: EntryKind;
  amount: string;
  currency: string;
  original_amount: string | null;
  original_currency: string | null;
  fx_rate: string | null;
  fx_source: 'fx_api' | 'moze_backup' | null;
  entry_date: string;
  entry_time: string | null;
  category: string | null;
  project: string | null;
  name: string | null;
  merchant: string | null;
  counterparty: string | null;
  description: string | null;
  tags: string[];
  parent_entry_id: number | null;
  transfer_group_id: string | null;
  needs_review: boolean;
  running_balance: string;
}

export interface EntryPage {
  items: LedgerEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface EntryQuery {
  limit?: number;
  offset?: number;
  kind?: EntryKind | null;
  date_from?: string | null;
  date_to?: string | null;
}

export interface UnpairedTransfer {
  row: number;
  account: string;
  kind: EntryKind;
  date: string;
  time: string | null;
  amount: string;
  currency: string;
}

export interface ImportSummary {
  kind_counts?: Record<string, number>;
  accounts_created?: string[];
  accounts_archived?: string[];
  accounts_renamed?: { from: string; to: string }[];
  unpaired_transfers?: UnpairedTransfer[];
  error?: string;
}

export interface ImportRun {
  id: number | null;
  status: 'running' | 'succeeded' | 'failed' | 'dry_run';
  started_at: string;
  finished_at: string | null;
  file_name: string;
  file_sha256: string;
  row_count: number | null;
  summary: ImportSummary | null;
}

export const ENTRY_KIND_LABELS: Record<EntryKind, string> = {
  expense: '支出',
  income: '收入',
  transfer_out: '轉出',
  transfer_in: '轉入',
  receivable: '應收款項',
  payable: '應付款項',
  balance_adjustment: '餘額調整',
  fee: '手續費',
  discount: '折扣',
  reward: '紅利回饋',
  interest: '利息',
  refund: '退款',
};
