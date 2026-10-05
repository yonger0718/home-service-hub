"""Locks of every schedule write path (design D32).

Order: the shared import advisory key (transaction-scoped) → the schedule_definition row (FOR SHARE when posting or
acting on one instance, FOR UPDATE when editing the definition) → schedule_instance row(s) (FOR UPDATE, ascending
id) → the ledger's own order (entry_group rows ascending → target entries with their transfer legs in one
statement). No path that holds an entry or group lock ever locks a schedule row. The backup importer holds the
import key exclusively, so `take_import_key_shared` answers 409 import_running for the seconds an import runs.
"""

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..models import ScheduleDefinition, ScheduleInstance
from .errors import ConflictError, NotFoundError
from .moze_import_service import IMPORT_LOCK_KEY

SCHEDULE_JOB_LOCK_KEY = 0x53434844  # "SCHD"; held by one job run at a time (session-level, dedicated connection)
IMPORT_RUNNING = "import_running"


class ImportRunningError(ConflictError):
    """A backup or CSV import holds the import key exclusively; HTTP 409 with message import_running."""

    def __init__(self) -> None:
        super().__init__(IMPORT_RUNNING)


def take_import_key_shared(db: Session) -> None:
    """Share the import advisory key until this transaction ends; raise ImportRunningError while an import runs."""
    acquired = db.execute(
        text("SELECT pg_try_advisory_xact_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY}
    ).scalar_one()
    if not acquired:
        raise ImportRunningError()


def import_key_free(engine: Engine) -> bool:
    """True when no import holds the key right now (run-now checks this before starting)."""
    with engine.connect() as conn:
        acquired = conn.execute(text("SELECT pg_try_advisory_lock_shared(:key)"), {"key": IMPORT_LOCK_KEY}).scalar_one()
        if acquired:
            conn.execute(text("SELECT pg_advisory_unlock_shared(:key)"), {"key": IMPORT_LOCK_KEY})
        conn.commit()
    return bool(acquired)


def get_instance(db: Session, instance_id: int) -> ScheduleInstance:
    """Unlocked read (to learn definition_id, which never changes, before locking the definition first)."""
    instance = db.get(ScheduleInstance, instance_id)
    if instance is None:
        raise NotFoundError(f"schedule instance {instance_id} not found")
    return instance


def lock_definition(db: Session, definition_id: int, *, share: bool = False) -> ScheduleDefinition:
    definition = db.execute(
        select(ScheduleDefinition)
        .where(ScheduleDefinition.id == definition_id)
        .with_for_update(read=share)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if definition is None:
        raise NotFoundError(f"schedule definition {definition_id} not found")
    return definition


def lock_instance(db: Session, instance_id: int, *, skip_locked: bool = False) -> ScheduleInstance | None:
    """The instance FOR UPDATE (SKIP LOCKED for the job: an instance the owner is acting on is left to the owner)."""
    return db.execute(
        select(ScheduleInstance)
        .where(ScheduleInstance.id == instance_id)
        .with_for_update(skip_locked=skip_locked)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def lock_instances(db: Session, definition_id: int) -> list[ScheduleInstance]:
    """Every instance of the definition, FOR UPDATE in ascending id order."""
    return list(
        db.scalars(
            select(ScheduleInstance)
            .where(ScheduleInstance.definition_id == definition_id)
            .order_by(ScheduleInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
