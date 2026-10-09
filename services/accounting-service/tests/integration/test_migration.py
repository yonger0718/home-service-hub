import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

LEGACY_TABLES = (
    "categories",
    "credit_cards",
    "installments",
    "payment_methods",
    "subscriptions",
    "transactions",
)
LEDGER_TABLES = (
    "account", "account_group", "category", "counterparty", "entry_group", "entry_reward_rule", "fx_rate",
    "import_run", "ledger_entry", "schedule_definition", "schedule_instance", "preference", "project", "reward_rule",
)
PHASE_1_HEAD = "5d2e7c9a1b3f"
PHASE_2A_HEAD = "7b1e4a2c9d05"
SCHEDULES_HEAD = "c4e8b2f1a7d3"
RECONCILE_HEAD = "d1f3a7c2e9b4"


def _schema(url) -> dict:
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        schema: dict = {"tables": tables}
        for table in tables:
            if table == "alembic_version":
                continue
            schema[table] = {
                "columns": sorted(
                    (c["name"], str(c["type"]), c["nullable"], str(c.get("default")))
                    for c in inspector.get_columns(table)
                ),
                "pk": inspector.get_pk_constraint(table)["constrained_columns"],
                "fks": sorted(
                    (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                    for fk in inspector.get_foreign_keys(table)
                ),
                "indexes": sorted(
                    (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                    for i in inspector.get_indexes(table)
                ),
                "uniques": sorted(tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)),
            }
        return schema
    finally:
        engine.dispose()


def _version(url) -> str:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    finally:
        engine.dispose()


def test_upgrade_replaces_legacy_tables_with_ledger_tables(database_factory, alembic_config):
    url = database_factory()
    command.upgrade(alembic_config(url), "head")

    tables = set(_schema(url)["tables"])
    assert set(LEDGER_TABLES) <= tables
    assert not (set(LEGACY_TABLES) & tables)


def test_upgrade_aborts_when_a_legacy_table_has_rows(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "8a4c4f9b2d1b")
    engine = create_engine(url)
    with engine.begin() as conn:
        category_id = conn.execute(
            text("INSERT INTO categories (name) VALUES ('legacy') RETURNING id")
        ).scalar_one()
        conn.execute(
            text("INSERT INTO transactions (category_id, item) VALUES (:c, 'legacy row')"),
            {"c": category_id},
        )
    engine.dispose()
    before = _schema(url)

    with pytest.raises(RuntimeError, match="transactions, categories"):
        command.upgrade(config, "head")

    assert _schema(url) == before
    assert _version(url) == "8a4c4f9b2d1b"


def test_downgrade_restores_revision_8a4c4f9b2d1b_schema(database_factory, alembic_config):
    reference_url = database_factory()
    command.upgrade(alembic_config(reference_url), "8a4c4f9b2d1b")

    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "8a4c4f9b2d1b")

    assert _version(url) == "8a4c4f9b2d1b"
    assert _schema(url) == _schema(reference_url)


def test_upgrade_holds_legacy_table_locks_before_the_emptiness_check(database_factory, alembic_config, monkeypatch):
    """A legacy writer must not be able to slip a row in between the empty check and DROP TABLE."""
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import OperationalError

    url = database_factory()
    command.upgrade(alembic_config(url), "8a4c4f9b2d1b")

    path = next(Path(__file__).parents[2].joinpath("alembic/versions").glob("5d2e7c9a1b3f_*.py"))
    spec = importlib.util.spec_from_file_location("moze_ledger_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine(url)
    writer_engine = create_engine(url)
    outcome: dict = {}
    real_check = migration._assert_legacy_tables_empty

    def check_with_concurrent_writer(connection):
        with writer_engine.connect() as writer:
            writer.execute(text("SET lock_timeout = '500ms'"))
            try:
                writer.execute(text("INSERT INTO categories (name) VALUES ('racer')"))
                outcome["blocked"] = False
            except OperationalError as exc:
                outcome["blocked"] = "lock timeout" in str(exc)
        real_check(connection)

    monkeypatch.setattr(migration, "_assert_legacy_tables_empty", check_with_concurrent_writer)
    try:
        with engine.begin() as conn:
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
        assert outcome["blocked"] is True
    finally:
        engine.dispose()
        writer_engine.dispose()


def _models_schema(url) -> dict:
    """Schema built by Base.metadata.create_all (plus the sequence the models reference)."""
    from app.database import Base

    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SEQUENCE ledger_entry_seq_seq AS BIGINT"))
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    schema = _schema(url)
    schema["tables"] = [t for t in schema["tables"] if t != "alembic_version"]
    return schema


def _phase_1_rows(url) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        for day, counterparty in ((1, "Alan"), (2, "Alan"), (3, "Alan"), (4, "Bob"), (5, None)):
            conn.execute(
                text(
                    "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, counterparty, source) "
                    "VALUES (:a, 'receivable', -100, 'TWD', :d, :c, 'moze_import')"
                ),
                {"a": account_id, "d": f"2026-09-0{day}", "c": counterparty},
            )
    engine.dispose()


def test_head_schema_matches_the_models(database_factory, alembic_config):
    migrated = database_factory()
    command.upgrade(alembic_config(migrated), "head")
    schema = _schema(migrated)
    schema["tables"] = [t for t in schema["tables"] if t != "alembic_version"]

    assert schema == _models_schema(database_factory())


def test_upgrade_backfills_posted_date_and_links_counterparties(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, PHASE_1_HEAD)
    _phase_1_rows(url)

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        counterparties = dict(conn.execute(text("SELECT name, id FROM counterparty")).all())
        links = conn.execute(
            text("SELECT counterparty_id, count(*) FROM ledger_entry GROUP BY counterparty_id")
        ).all()
        mismatched = conn.execute(text("SELECT count(*) FROM ledger_entry WHERE posted_date <> entry_date")).scalar_one()
    engine.dispose()
    assert sorted(counterparties) == ["Alan", "Bob"]
    assert sorted(links, key=lambda row: (row[0] is None, row[0])) == sorted(
        [(counterparties["Alan"], 3), (counterparties["Bob"], 1), (None, 1)],
        key=lambda row: (row[0] is None, row[0]),
    )
    assert mismatched == 0


def test_upgrade_backfills_is_settlement_for_phase_1_collections(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, PHASE_1_HEAD)
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        # Phase 1 CSV rows: 借出 (-420), 收款 (+200), 借入 (+300), 還款 (-100), an expense.
        for kind, amount in (("receivable", -420), ("receivable", 200), ("payable", 300), ("payable", -100), ("expense", -60)):
            conn.execute(
                text(
                    "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, source) "
                    "VALUES (:a, :k, :m, 'TWD', '2026-09-01', 'moze_import')"
                ),
                {"a": account_id, "k": kind, "m": amount},
            )
    engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.connect() as conn:
        flags = conn.execute(
            text("SELECT kind::text, amount::int, is_settlement FROM ledger_entry ORDER BY id")
        ).all()
    with pytest.raises(IntegrityError, match="ck_ledger_entry_settlement_sign"), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, source) "
                "VALUES (:a, 'receivable', 50, 'TWD', '2026-09-02', 'manual')"
            ),
            {"a": account_id},
        )
    engine.dispose()
    assert flags == [
        ("receivable", -420, False), ("receivable", 200, True), ("payable", 300, False),
        ("payable", -100, True), ("expense", -60, False),
    ]


def test_downgrade_restores_phase_1_schema_and_counterparty_text(database_factory, alembic_config):
    reference = database_factory()
    command.upgrade(alembic_config(reference), PHASE_1_HEAD)

    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, PHASE_1_HEAD)
    _phase_1_rows(url)
    command.upgrade(config, "head")
    command.downgrade(config, PHASE_1_HEAD)

    assert _version(url) == PHASE_1_HEAD
    assert _schema(url) == _schema(reference)
    engine = create_engine(url)
    with engine.connect() as conn:
        restored = conn.execute(
            text("SELECT counterparty, count(*) FROM ledger_entry GROUP BY counterparty ORDER BY counterparty")
        ).all()
        sources = conn.execute(
            text("SELECT enum_range(NULL::entry_source)::text, enum_range(NULL::fx_source)::text")
        ).one()
    engine.dispose()
    assert restored == [("Alan", 3), ("Bob", 1), (None, 1)]
    assert sources == ("{moze_import,manual,hermes}", "{fx_api,moze_backup}")


def test_downgrade_refuses_when_phase_2a_data_exists(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, posted_date, source) "
                "VALUES (:a, 'reward', 30, 'TWD', '2026-10-01', '2026-11-05', 'manual')"
            ),
            {"a": account_id},
        )
        conn.execute(text("INSERT INTO account_group (name) VALUES ('信用卡')"))
    engine.dispose()

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(config, PHASE_1_HEAD)

    message = str(excinfo.value)
    assert "1 ledger_entry rows with source = manual" in message
    assert "1 ledger_entry rows whose posted_date differs from entry_date" in message
    assert "1 account_group rows" in message
    assert _version(url) == RECONCILE_HEAD


def test_downgrade_refusal_names_every_blocker(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, source) "
                "VALUES (:a, 'expense', -60, 'TWD', '2026-09-01', 'moze_backup')"
            ),
            {"a": account_id},
        )
        conn.execute(
            text(
                "INSERT INTO reward_rule (account_id, name, method, rate, posting) "
                "VALUES (:a, '國內 1%', 'percent', 1, 'after_window')"
            ),
            {"a": account_id},
        )
    engine.dispose()

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(config, PHASE_1_HEAD)

    message = str(excinfo.value)
    assert message.startswith("refusing to downgrade 7b1e4a2c9d05: phase 1 cannot hold ")
    assert "1 ledger_entry rows with source = moze_backup" in message
    assert "1 reward_rule rows" in message
    assert "source = manual" not in message
    assert _version(url) == RECONCILE_HEAD


def test_self_referencing_entry_links_are_indexed(pg_engine):
    """Every ledger_entry delete runs FK actions on these columns; without an index each one scans the table."""
    indexes = {i["name"]: tuple(i["column_names"]) for i in inspect(pg_engine).get_indexes("ledger_entry")}
    assert indexes["ix_ledger_entry_refunds_entry_id"] == ("refunds_entry_id",)
    assert indexes["ix_ledger_entry_parent_entry_id"] == ("parent_entry_id",)


def _insert_schedule_rows(url, *, acted_by: str | None, schedule_entry: bool) -> None:
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('錢包', 'TWD') RETURNING id")
        ).scalar_one()
        definition_id = conn.execute(
            text(
                "INSERT INTO schedule_definition (kind, name, template, interval_unit, anchor_date, auto_post_from) "
                "VALUES ('recurring', 'Netflix', '{\"lines\": []}', 'month', '2026-10-22', '2026-10-03') RETURNING id"
            )
        ).scalar_one()
        entry_ids = "[]"
        if schedule_entry:
            entry_id = conn.execute(
                text(
                    "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, posted_date, source) "
                    "VALUES (:a, 'expense', -390, 'TWD', '2026-10-22', '2026-10-22', 'schedule') RETURNING id"
                ),
                {"a": account_id},
            ).scalar_one()
            entry_ids = f"[{entry_id}]"
        if acted_by is None:
            conn.execute(
                text(
                    "INSERT INTO schedule_instance (definition_id, seq, rule_date, due_date) "
                    "VALUES (:d, 1, '2026-10-22', '2026-10-22')"
                ),
                {"d": definition_id},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO schedule_instance (definition_id, seq, rule_date, due_date, status, posted_entry_ids, "
                    "acted_at, acted_by) VALUES (:d, 1, '2026-10-22', '2026-10-22', 'posted', CAST(:ids AS jsonb), "
                    "now(), CAST(:by AS schedule_actor))"
                ),
                {"d": definition_id, "ids": entry_ids if entry_ids != "[]" else "[999]", "by": acted_by},
            )
    engine.dispose()


def test_upgrade_adds_schedule_source_and_tables_and_drops_moze_schedule(database_factory, alembic_config):
    url = database_factory()
    command.upgrade(alembic_config(url), "head")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        definition_columns = {column["name"]: column for column in inspect(engine).get_columns("schedule_definition")}
        with engine.connect() as conn:
            sources = conn.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text")).scalars().all()
            kinds = conn.execute(text("SELECT count(*) FROM pg_type WHERE typname = 'moze_schedule_kind'")).scalar_one()
    finally:
        engine.dispose()
    assert {"schedule_definition", "schedule_instance"} <= tables
    owner_edited = definition_columns["template_owner_edited"]  # proposal decision 24
    assert (owner_edited["nullable"], owner_edited["default"]) == (False, "false")
    assert "moze_schedule" not in tables
    assert "schedule" in sources
    assert kinds == 0
    assert _version(url) == RECONCILE_HEAD


def test_downgrade_recreates_an_empty_moze_schedule(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    _insert_schedule_rows(url, acted_by=None, schedule_entry=False)  # a pending period only: nothing HomeHub posted
    command.downgrade(config, PHASE_2A_HEAD)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT count(*) FROM moze_schedule")).scalar_one()
            sources = conn.execute(text("SELECT unnest(enum_range(NULL::entry_source))::text")).scalars().all()
    finally:
        engine.dispose()
    assert "moze_schedule" in tables and rows == 0
    assert not {"schedule_definition", "schedule_instance"} & tables
    assert "schedule" not in sources
    assert _version(url) == PHASE_2A_HEAD


def test_downgrade_refuses_while_homehub_posted_data_exists(database_factory, alembic_config):
    url = database_factory()
    config = alembic_config(url)
    command.upgrade(config, "head")
    _insert_schedule_rows(url, acted_by="auto", schedule_entry=True)

    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(config, PHASE_2A_HEAD)

    message = str(excinfo.value)
    assert message.startswith("refusing to downgrade c4e8b2f1a7d3: ")
    assert "1 ledger_entry rows with source = schedule" in message
    assert "1 schedule instances posted or skipped by HomeHub (acted_by auto or owner)" in message
    assert _version(url) == RECONCILE_HEAD


def test_reconcile_downgrade_refuses_with_statement_rows(database_factory, alembic_config):
    url = database_factory()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO account (name, currency) VALUES ('A', 'TWD')"))
        conn.execute(text("INSERT INTO ingest_run (trigger, principal) VALUES ('timer', 'worker')"))
    engine.dispose()
    with pytest.raises(RuntimeError, match="refusing to downgrade d1f3a7c2e9b4: 1 ingest runs"):
        command.downgrade(cfg, SCHEDULES_HEAD)
    assert _version(url) == RECONCILE_HEAD


def test_reconcile_downgrade_refuses_with_a_manual_statement_and_no_run(database_factory, alembic_config):
    url = database_factory()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('A', 'TWD') RETURNING id")
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO account_statement (account_id, kind, currency, period_start, period_end, "
                "statement_total, origin, mode) "
                "VALUES (:a, 'card', 'TWD', '2026-09-01', '2026-09-30', 100, 'manual', 'historical')"
            ),
            {"a": account_id},
        )
    engine.dispose()
    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(cfg, SCHEDULES_HEAD)
    assert str(excinfo.value) == "refusing to downgrade d1f3a7c2e9b4: 1 statements"
    assert _version(url) == RECONCILE_HEAD


@pytest.mark.parametrize(
    ("sql", "label"),
    [
        (
            "INSERT INTO ledger_entry (account_id, kind, amount, currency, entry_date, posted_date, source) "
            "VALUES (:a, 'expense', -10, 'TWD', '2026-10-01', '2026-10-01', 'statement')",
            "1 statement-created entries",
        ),
        ("UPDATE account SET statement_live_from = '2026-10-01' WHERE id = :a", "1 accounts with statement settings"),
        ("INSERT INTO reconciliation_settings (data) VALUES ('{\"policy\": true}')", "1 settings changed"),
    ],
    ids=["statement_entry", "account_statement_settings", "settings_changed"],
)
def test_reconcile_downgrade_refusal_names_each_guard(database_factory, alembic_config, sql, label):
    url = database_factory()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    with engine.begin() as conn:
        account_id = conn.execute(
            text("INSERT INTO account (name, currency) VALUES ('A', 'TWD') RETURNING id")
        ).scalar_one()
        conn.execute(text(sql), {"a": account_id})
    engine.dispose()
    with pytest.raises(RuntimeError) as excinfo:
        command.downgrade(cfg, SCHEDULES_HEAD)
    assert str(excinfo.value) == f"refusing to downgrade d1f3a7c2e9b4: {label}"
    assert _version(url) == RECONCILE_HEAD


def test_reconcile_head_has_exact_fk_actions_predicates_and_triggers(pg_engine):
    """_schema() compares neither FK ON DELETE actions, index predicates nor triggers: check them on the migrated DB."""
    with pg_engine.connect() as conn:
        named_fks = dict(
            conn.execute(
                text(
                    "SELECT conname, confdeltype FROM pg_constraint WHERE contype = 'f' AND conname IN "
                    "('fk_account_statement_current_revision', 'fk_statement_event_first_line', "
                    "'fk_statement_event_current_line')"
                )
            ).all()
        )
        coverage_fks = dict(
            conn.execute(
                text(
                    "SELECT a.attname, c.confdeltype FROM pg_constraint c "
                    "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                    "WHERE c.contype = 'f' AND c.conrelid = 'statement_coverage'::regclass "
                    "AND c.confrelid IN ('ledger_entry'::regclass, 'entry_group'::regclass)"
                )
            ).all()
        )
        indexdefs = dict(
            conn.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE indexname IN "
                    "('ux_statement_coverage_active_entry', 'ux_reconciliation_action_effect')"
                )
            ).all()
        )
        triggers = dict(
            conn.execute(
                text(
                    "SELECT tgname, tgisinternal FROM pg_trigger WHERE tgname IN "
                    "('trg_ledger_entry_reconciliation_dirty', 'trg_entry_group_reconciliation_dirty', "
                    "'trg_account_reconciliation_dirty')"
                )
            ).all()
        )

    assert named_fks == {
        "fk_account_statement_current_revision": "r",
        "fk_statement_event_first_line": "r",
        "fk_statement_event_current_line": "n",
    }
    assert coverage_fks == {"entry_id": "n", "group_id": "n"}
    coverage = indexdefs["ux_statement_coverage_active_entry"]
    assert coverage.startswith("CREATE UNIQUE INDEX") and "(entry_id)" in coverage
    assert "status = 'active'" in coverage and "entry_id IS NOT NULL" in coverage
    effect = indexdefs["ux_reconciliation_action_effect"]
    assert effect.startswith("CREATE UNIQUE INDEX") and "(event_id, action, effect_slot)" in effect
    assert "status = 'applied'" in effect
    assert triggers == {
        "trg_ledger_entry_reconciliation_dirty": False,
        "trg_entry_group_reconciliation_dirty": False,
        "trg_account_reconciliation_dirty": False,
    }
