from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime, date
from typing import List, Literal, Optional
from enum import Enum
from decimal import Decimal


def _normalize_symbol(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("symbol must be a string")

    normalized = value.split('.')[0].strip().upper()
    if not normalized:
        raise ValueError("symbol must not be blank")

    return normalized

class TransactionType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TransactionBase(BaseModel):
    """Shared fields for transaction read/write.

    NOTE: ``decimal_places`` is intentionally NOT enforced here. Pydantic
    *rejects* Decimals with more than the declared precision rather than
    rounding them, which would crash the GET response if a high-precision
    value ever reached this layer. Strict precision is enforced on
    ``TransactionCreate`` (the input edge) and at the DB layer via
    ``NUMERIC(12, 2)``. This class stays permissive on output.
    """

    symbol: str
    name: Optional[str] = None
    type: TransactionType
    position_side: PositionSide = PositionSide.LONG
    quantity: int
    price: Decimal
    trade_date: Optional[datetime] = None
    fee: Decimal = Decimal("0.0")
    tax: Decimal = Decimal("0.0")

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

class TransactionCreate(TransactionBase):
    quantity: int = Field(..., gt=0)
    price: Decimal = Field(..., gt=Decimal("0"), decimal_places=2)
    fee: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"), decimal_places=2)
    tax: Decimal = Field(default=Decimal("0.0"), ge=Decimal("0"), decimal_places=2)

class Transaction(TransactionBase):
    id: int
    is_day_trade: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class DividendBase(BaseModel):
    """Shared fields for dividend read/write.

    See ``TransactionBase`` for the rationale on omitting ``decimal_places``.
    """

    symbol: str
    amount: Decimal
    ex_dividend_date: datetime
    received_date: Optional[datetime] = None
    fee: Decimal = Decimal("0")
    tax: Decimal = Decimal("0")
    cash_dividend_per_share: Optional[Decimal] = None
    stock_dividend_shares: int = 0
    source: Optional[str] = None
    quantity_at_record_date: Optional[Decimal] = None

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return _normalize_symbol(value)

class DividendCreate(DividendBase):
    amount: Decimal = Field(..., gt=Decimal("0"), decimal_places=2)
    fee: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), decimal_places=2)
    tax: Decimal = Field(default=Decimal("0"), ge=Decimal("0"), decimal_places=2)
    stock_dividend_shares: int = Field(default=0, ge=0)

class Dividend(DividendBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- 計算後的模型 ---

class StockHolding(BaseModel):
    symbol: str
    name: Optional[str] = None
    total_quantity: int
    avg_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_percent: Decimal
    day_change_amount: Decimal = Decimal("0.0")      # 單日漲跌金額
    day_change_percent: Decimal = Decimal("0.0")     # 單日漲跌幅(%)
    day_pnl: Decimal = Decimal("0.0")                # 單日損益
    total_dividends: Decimal = Decimal("0.0")
    total_pnl_with_dividend: Decimal # 含息損益
    xirr: Optional[Decimal] = None   # 年化報酬率，如 0.1523 = 15.23%

class PortfolioSummary(BaseModel):
    total_market_value: Decimal
    total_cost: Decimal
    total_unrealized_pnl: Decimal
    total_unrealized_pnl_percent: Decimal
    total_day_pnl: Decimal = Decimal("0.0")          # 投資組合今日總損益
    total_dividends: Decimal
    total_realized_pnl: Decimal = Decimal("0.0")     # 累積已實現損益（含當沖）
    holdings: List[StockHolding]
    portfolio_xirr: Optional[Decimal] = None          # 整體投資組合年化報酬率
    quotes_status: Literal["ok", "partial", "unavailable"] = "ok"


class ExDividendRecord(BaseModel):
    symbol: str
    name: str
    ex_dividend_date: Optional[date] = None     # 除息日
    ex_rights_date: Optional[date] = None       # 除權日
    cash_dividend: Optional[str] = None         # 現金股利（字串保留原始精度）
    stock_dividend: Optional[str] = None        # 股票股利


# Sort field allowlists for paginated list endpoints. Server validates the
# `sort` query param against these; UI option keys must be a subset.
TransactionSortField = Literal["trade_date", "symbol", "type", "price", "quantity"]
DividendSortField = Literal["ex_dividend_date", "symbol", "amount", "source"]


class PagedTransactions(BaseModel):
    items: List[Transaction]
    total: int


class PagedDividends(BaseModel):
    items: List[Dividend]
    total: int
