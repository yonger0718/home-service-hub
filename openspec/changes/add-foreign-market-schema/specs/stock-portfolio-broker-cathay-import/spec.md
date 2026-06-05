## ADDED Requirements

### Requirement: Cathay import explicitly stamps TW market and TWD currency on every inserted row

The 國泰 broker import path SHALL set `market='TW'`, `currency='TWD'`, and `fx_rate_to_twd=NULL` explicitly on every `Transaction` (and any `Dividend` it might emit) it persists, rather than relying on column defaults. This makes the importer's market scope unambiguous and serves as a template for future foreign-broker importers (e.g. IBKR) which will pass their own market / currency / FX combinations.

#### Scenario: Imported Cathay row carries market and currency explicitly

- **WHEN** a Cathay CSV row is parsed and inserted via the import pipeline
- **THEN** the persisted `Transaction` SHALL have `market='TW'`, `currency='TWD'`, and `fx_rate_to_twd=NULL`
- **AND** the values SHALL be visible in the row read back from the database immediately after commit

#### Scenario: Default-omitted insert path remains forbidden in the importer

- **WHEN** code inside the Cathay importer constructs a `Transaction` instance
- **THEN** static analysis (review or lint rule) SHALL confirm that `market`, `currency`, and `fx_rate_to_twd` are passed explicitly, even though column defaults would satisfy the constraint
