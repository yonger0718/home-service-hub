export enum TransactionType {
  BUY = 'BUY',
  SELL = 'SELL'
}

export interface Transaction {
  id: number;
  symbol: string;
  name?: string;
  type: TransactionType;
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

export interface ImportResult {
  parsed: number;
  created: number;
  skipped_duplicates: number;
  dry_run: boolean;
  errors: ImportError[];
  created_ids: number[];
  rows: ImportRow[];
  recalc_scheduled?: boolean;
}

export interface RecalcStepResult {
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
  steps?: RecalcStepResult[];
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
