"""Request/response schemas for reconciliation actions and their audit trail (R1c)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .statements import Strict


class ActionIn(BaseModel):
    model_config = Strict
    action: str = Field(min_length=1, max_length=32)
    params: dict = Field(default_factory=dict)


class DismissIn(BaseModel):
    model_config = Strict
    note: str = Field(min_length=1, max_length=1000)


class ActionOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    case_id: int
    proposal_id: int | None
    action: str
    params: dict
    actor: str
    status: str
    effect_slot: str
    created_entry_ids: list[int]
    deleted_entry_ids: list[int]
    touched_entry_ids: list[int]
    touched_group_ids: list[int]
    reverted_by_action_id: int | None
    created_at: datetime
    replayed: bool = False


class AuditOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: int
    statement_id: int | None
    case_id: int | None
    action_id: int | None
    run_id: int | None
    event: str
    actor: str
    request_id: str | None
    detail: dict
    created_at: datetime
