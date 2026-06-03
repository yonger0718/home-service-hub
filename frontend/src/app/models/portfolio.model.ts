export enum TransactionType {
  BUY = 'BUY',
  SELL = 'SELL'
}

export type PositionSide = 'LONG' | 'SHORT';

export interface Transaction {
  id: number;
  symbol: string;
  name?: string;
  type: TransactionType;
  position_side?: PositionSide;
  quantity: number;
  price: number;
  trade_date?: string | Date;
  fee: number;
  tax: number;
  is_day_trade?: boolean;
  created_at?: string;
  updated_at?: string;
}

export type ImportKind = 'transactions' | 'dividends';

export interface ImportRow {
  row_index: number;
  fingerprint: string;
  payload: Record<string, string | null>;
}

export interface ImportError {
  row_index: number;
  message: string;
}

export interface UnresolvedName {
  name: string;
  occurrences: number;
  sample_dates: string[];
}

export type OverrideStatus =
  | 'verified'
  | 'name_mismatch'
  | 'not_traded_on_date'
  | 'fetch_failed'
  | 'user_overridden';

export interface OverrideValidation {
  name: string;
  code: string;
  status: OverrideStatus;
  expected_name?: string | null;
  fetched_name?: string | null;
}

export interface ImportResult {
  parsed: number;
  created: number;
  skipped_duplicates: number;
  rehashed?: number;
  would_rehash?: number;
  would_insert?: number;
  would_skip_duplicate?: number;
  skipped_unresolved?: number;
  skipped_unverified?: number;
  unresolved_names?: UnresolvedName[];
  override_validations?: OverrideValidation[];
  dry_run: boolean;
  errors: ImportError[];
  created_ids: number[];
  rows: ImportRow[];
  recalc_scheduled?: boolean;
  csv_format?: 'generic' | 'cathay';
}

export interface RecalcStepStatus {
  name: string;
  status: 'ok' | 'failed' | 'skipped' | 'partial';
  detail?: Record<string, unknown>;
  error?: string | null;
}

export interface RecalcStatus {
  state: 'idle' | 'running' | 'completed' | 'partial' | 'failed';
  started_at?: string;
  finished_at?: string | null;
  recalc_from?: string | null;
  recalc_to?: string | null;
  touched_symbols?: string[];
  current_step?: string | null;
  steps?: RecalcStepStatus[];
}

export interface RecalcTriggerResponse {
  recalc_scheduled: boolean;
  start_date: string;
  end_date: string;
  touched_symbols: string[];
}

export interface Dividend {
  id: number;
  symbol: string;
  amount: number;
  ex_dividend_date: string | Date;
  received_date?: string | Date;
  fee?: number;
  tax?: number;
  cash_dividend_per_share?: number;
  stock_dividend_shares?: number;
  source?: string;
  quantity_at_record_date?: number;
  created_at?: string;
  updated_at?: string;
}

export interface UpcomingEvent {
  date: string;
  symbol: string;
  name?: string;
  type: 'CASH_DIV' | 'STOCK_DIV' | 'BOTH' | 'FACE_VALUE';
  cash_dividend?: string;
  stock_dividend_shares?: string;
  ratio?: string;
  reference_price_change?: string;
  source?: string;
}

export interface StockHolding {
  symbol: string;
  name?: string;
  total_quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_percent: number;
  day_change_amount: number;
  day_change_percent: number;
  day_pnl: number;
  total_dividends: number;
  total_pnl_with_dividend: number;
  xirr?: number;              // 年化報酬率，e.g. 0.1523 = 15.23%
  xirr_1m: number | null;
  xirr_3m: number | null;
  xirr_1y: number | null;
  xirr_ytd: number | null;
}

export interface PortfolioSummary {
  total_market_value: number;
  total_cost: number;
  total_unrealized_pnl: number;
  total_unrealized_pnl_percent: number;
  total_day_pnl: number;
  total_dividends: number;
  total_realized_pnl: number;     // 累積已實現損益（含當沖）
  holdings: StockHolding[];
  portfolio_xirr?: number;    // 整體投資組合年化報酬率
  portfolio_xirr_1m: number | null;
  portfolio_xirr_3m: number | null;
  portfolio_xirr_1y: number | null;
  portfolio_xirr_ytd: number | null;
}

export interface NetworthPoint {
  date: string;
  total_market_value: string;
  total_cost: string;
  total_unrealized_pnl: string;
  total_dividends: string;
  total_realized_pnl: string;
  portfolio_xirr: string | null;
}

export interface CorporateAction {
  id: number;
  symbol: string;
  effective_date: string;
  action_type: string;
  ratio: string;
  source: string;
  source_event_key: string;
}

export interface ExDividendRecord {
  symbol: string;
  name: string;
  ex_dividend_date?: string;
  ex_rights_date?: string;
  cash_dividend?: string;
  stock_dividend?: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
}

export interface TransactionQuery {
  offset?: number;
  limit?: number;
  symbol?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  side?: 'BUY' | 'SELL' | null;
  sort?: string;
}

export interface DividendQuery {
  offset?: number;
  limit?: number;
  symbol?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  source?: string | null;
  sort?: string;
}

export interface RealizedPnlEvent {
  trade_date: string;
  symbol: string;
  name: string | null;
  quantity: number;
  sell_price: string;
  avg_cost_at_sale: string;
  fee: string;
  tax: string;
  proceeds_gross: string;
  proceeds_net: string;
  cost_out: string;
  realized_pnl: string;
  is_day_trade: boolean;
  position_side: PositionSide;
  note: string | null;
}

export interface RealizedPnlSummary {
  filter_scope_total: string;
  filter_scope_count: number;
  ytd_total: string;
  ytd_count: number;
}

export interface RealizedPnlQuery {
  offset?: number;
  limit?: number;
  symbol?: string | null;
  date_from?: string | null;
  date_to?: string | null;
  year?: number | null;
  day_trade_only?: boolean | null;
  sort?: string;
}

export type RealizedPnlPaged = Paged<RealizedPnlEvent> & { summary: RealizedPnlSummary };

export type BrokerEnum = 'cathay' | 'sinopac' | 'firstrade' | 'ib' | 'cs' | 'other';

export type CashTransactionType =
  | 'deposit' | 'withdraw'
  | 'trade'
  | 'buy_settle' | 'sell_settle'
  | 'fee' | 'tax'
  | 'dividend_cash' | 'interest_in'
  | 'margin_interest' | 'wire_fee'
  | 'fx_convert';

export type CashTransactionSource = 'manual' | 'csv_import' | 'auto_derive';

export interface BrokerAccount {
  id: number;
  broker: BrokerEnum;
  nickname: string;
  currency: string;
  opening_balance: string;
  opening_date: string;
  is_active: boolean;
  created_at: string;
  native_balance: string;
  target_balance?: string | null;
  target_currency?: string | null;
}

export interface CreateBrokerAccount {
  broker: BrokerEnum;
  nickname: string;
  currency: string;
  opening_balance?: string;
  opening_date?: string;
  is_active?: boolean;
}

export interface PatchBrokerAccount {
  nickname?: string;
  opening_balance?: string;
  opening_date?: string;
  is_active?: boolean;
}

export interface CashTransaction {
  id: number;
  account_id: number;
  txn_date: string;
  type: CashTransactionType;
  amount: string;
  currency: string;
  note?: string | null;
  related_transaction_id?: number | null;
  related_dividend_id?: number | null;
  child_legs?: CashTransaction[] | null;
  source: CashTransactionSource;
  import_fingerprint: string;
  created_at: string;
}

export interface CreateCashTransaction {
  txn_date: string;
  type: CashTransactionType;
  amount: string;
  currency: string;
  note?: string | null;
}

export interface CashTransactionQuery {
  date_from?: string;
  date_to?: string;
  type?: CashTransactionType;
  sort?: string;
  offset?: number;
  limit?: number;
  merge_related?: boolean;
}

export interface CashTransactionPaged {
  items: CashTransaction[];
  total: number;
  offset: number;
  limit: number;
}

export interface BalancePoint {
  date: string;
  balance: string;
}

export interface BalanceHistory {
  account_id: number;
  currency: string;
  points: BalancePoint[];
}

export interface AccountsList {
  items: BrokerAccount[];
  target_currency: string | null;
  total_target_balance: string | null;
  skipped_currencies: string[];
}

export interface FxFetchPerBase {
  success: boolean;
  upserted: number;
  source_url: string | null;
  error: string | null;
}

export interface FxFetchResult {
  success: boolean;
  per_base: Record<string, FxFetchPerBase>;
  upserted_count: number;
  error: string | null;
}
