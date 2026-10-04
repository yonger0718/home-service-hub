"""Template lines of a schedule definition (design D29): normalisation, validation, overrides, references.

Messages never carry amounts or names: a validation error raised while posting becomes the instance's last_error.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from ..models import Account, Category, Counterparty, LedgerEntry, Project
from .errors import ValidationError
from .schedule_rules import plain

LINE_KINDS = ("expense", "income", "receivable", "payable", "transfer", "repayment", "collection", "interest")
LINE_KEYS = (
    "kind", "account_id", "to_account_id", "to_amount", "counterparty_id", "category_id", "project_id", "amount",
    "currency", "loan_entry_id", "name", "merchant",
)
# Line kind → the ledger kind of the entry it writes (the category must be of this kind).
LEDGER_KINDS = {
    "expense": "expense", "income": "income", "receivable": "receivable", "payable": "payable",
    "transfer": "transfer_out", "repayment": "payable", "collection": "receivable", "interest": "interest",
}
LOAN_LINE_KINDS = {"repayment": "payable", "collection": "receivable"}
COUNTERPARTY_LINE_KINDS = ("receivable", "payable")
NO_COUNTERPARTY_LINE_KINDS = ("expense", "income", "transfer")
MAX_LINES = 10


def _decimal(value, field: str, *, allow_zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError(field, "金額格式錯誤") from exc
    if not number.is_finite() or number.normalize().as_tuple().exponent < -4:
        raise ValidationError(field, "金額格式錯誤")
    if number < 0 or (number == 0 and not allow_zero):
        raise ValidationError(field, "金額須大於 0")
    return number


def normalize_template(template: dict) -> dict:
    """Every line with exactly LINE_KEYS (absent keys None), amounts as plain strings."""
    lines = []
    for raw in template.get("lines") or []:
        line = {key: raw.get(key) for key in LINE_KEYS}
        for key in ("amount", "to_amount"):
            if line[key] is not None:
                line[key] = plain(line[key])
        lines.append(line)
    return {"lines": lines, "description": template.get("description"), "tags": list(template.get("tags") or [])}


def _open_account(db: Session, account_id, field: str) -> Account:
    account = db.get(Account, account_id) if account_id is not None else None
    if account is None:
        raise ValidationError(field, "找不到帳戶")
    if account.is_archived:
        raise ValidationError(field, "帳戶已封存")
    return account


def _check_loan(db: Session, line: dict, kind: str, field: str) -> None:
    loan_id = line["loan_entry_id"]
    loan = db.get(LedgerEntry, loan_id) if loan_id is not None else None
    if (
        loan is None
        or loan.kind != LOAN_LINE_KINDS[kind]
        or loan.is_settlement
        or loan.settles_entry_id is not None
        or loan.parent_entry_id is not None
    ):
        raise ValidationError(field, "貸款記錄不存在或類型不符")
    if loan.currency != line["currency"]:
        raise ValidationError(field, "貸款幣別與這一行不同")


def validate_template(db: Session, template: dict) -> None:
    """Spec "Template lines": every rule, at save time and again at posting time (archived rows fail then)."""
    lines = template.get("lines") or []
    if not 1 <= len(lines) <= MAX_LINES:
        raise ValidationError("lines", f"需要 1 到 {MAX_LINES} 行")
    loan_lines = 0
    for index, line in enumerate(lines):
        def field(key: str) -> str:
            return f"lines[{index}].{key}"

        kind = line.get("kind")
        if kind not in LINE_KINDS:
            raise ValidationError(field("kind"), "不支援的類型")
        account = _open_account(db, line.get("account_id"), field("account_id"))
        if line.get("currency") != account.currency:
            raise ValidationError(field("currency"), "幣別須與帳戶相同")
        _decimal(line.get("amount"), field("amount"))
        if kind == "transfer":
            if line.get("to_account_id") is None:
                raise ValidationError(field("to_account_id"), "轉帳需要轉入帳戶")
            if line["to_account_id"] == account.id:
                raise ValidationError(field("to_account_id"), "轉入帳戶不可與轉出帳戶相同")
            target = _open_account(db, line["to_account_id"], field("to_account_id"))
            if target.currency != account.currency:
                if line.get("to_amount") is None:
                    raise ValidationError(field("to_amount"), "跨幣別轉帳需要轉入金額")
                _decimal(line["to_amount"], field("to_amount"))
            elif line.get("to_amount") is not None:
                raise ValidationError(field("to_amount"), "同幣別轉帳不填轉入金額")
        else:
            for key in ("to_account_id", "to_amount"):
                if line.get(key) is not None:
                    raise ValidationError(field(key), "只有轉帳使用")
        if kind in COUNTERPARTY_LINE_KINDS:
            counterparty_id = line.get("counterparty_id")
            if counterparty_id is None or db.get(Counterparty, counterparty_id) is None:
                raise ValidationError(field("counterparty_id"), "應收應付需要對象")
        elif kind in NO_COUNTERPARTY_LINE_KINDS and line.get("counterparty_id") is not None:
            raise ValidationError(field("counterparty_id"), "這個類型不設對象")
        if kind in LOAN_LINE_KINDS:
            loan_lines += 1
            if loan_lines > 1:
                raise ValidationError(field("kind"), "一個排程只能有一行還款或收款")
            _check_loan(db, line, kind, field("loan_entry_id"))
        elif line.get("loan_entry_id") is not None:
            raise ValidationError(field("loan_entry_id"), "只有還款或收款使用")
        if line.get("category_id") is not None:
            category = db.get(Category, line["category_id"])
            if category is None or category.kind != LEDGER_KINDS[kind]:
                raise ValidationError(field("category_id"), "類別與類型不符")
        if line.get("project_id") is not None and db.get(Project, line["project_id"]) is None:
            raise ValidationError(field("project_id"), "找不到專案")


def template_amounts(template: dict) -> list[str]:
    return [line["amount"] for line in template["lines"]]


def resolved_amounts(definition, instance) -> list[Decimal]:
    """D29 precedence: the instance's override (owner edit or imported amounts) when set, else the template's."""
    amounts = instance.amount_override if instance.amount_override is not None else template_amounts(definition.template)
    return [Decimal(str(value)) for value in amounts]


def check_amounts(template: dict, amounts: list | None) -> list[str] | None:
    """An override aligned with the template's lines (HTTP 422 naming `amounts` otherwise), as plain strings."""
    if amounts is None:
        return None
    if len(amounts) != len(template["lines"]):
        raise ValidationError("amounts", f"需要 {len(template['lines'])} 個金額，每一行一個")
    values = [_decimal(value, "amounts", allow_zero=True) for value in amounts]
    if all(value == 0 for value in values):
        raise ValidationError("amounts", "至少一行金額須大於 0")
    return [plain(value) for value in values]


def realign_override(old: dict, new: dict, override: list[str] | None) -> list[str] | None:
    """D35: keep an override for a line still at the same index with the same kind; a changed line takes the new
    template amount. None when nothing is kept."""
    if override is None:
        return None
    old_lines, result, kept = old["lines"], [], False
    for index, line in enumerate(new["lines"]):
        if index < len(old_lines) and index < len(override) and old_lines[index]["kind"] == line["kind"]:
            result.append(override[index])
            kept = True
        else:
            result.append(line["amount"])
    return result if kept else None


def loan_line(template: dict) -> tuple[int, dict] | None:
    for index, line in enumerate(template["lines"]):
        if line.get("kind") in LOAN_LINE_KINDS:
            return index, line
    return None


def referenced_ids(template: dict) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {"account": set(), "category": set(), "counterparty": set(), "project": set(), "loan": set()}
    for line in template["lines"]:
        for key, bucket in (
            ("account_id", "account"), ("to_account_id", "account"), ("category_id", "category"),
            ("counterparty_id", "counterparty"), ("project_id", "project"), ("loan_entry_id", "loan"),
        ):
            if line.get(key) is not None:
                found[bucket].add(int(line[key]))
    return found
