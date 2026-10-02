## REMOVED Requirements

### Requirement: Month navigator + summary row
**Reason**: The legacy transaction list is built on the removed TWD-only, account-less model.
**Migration**: Use `/accounting/accounts/:id` (this change). A month view returns with reports in `add-accounting-insights`.

### Requirement: Type-pill filter and live search
**Reason**: Superseded by the per-account entry history filters.
**Migration**: Use the kind and date filters on `/accounting/accounts/:id`.

### Requirement: Date-grouped expense/income timeline
**Reason**: Superseded by the per-account entry history with running balance.
**Migration**: Use `/accounting/accounts/:id`.

### Requirement: TxnDialog modal for adding a transaction
**Reason**: Phase 1 is read-only so that MOZE re-imports cannot wipe manual entries.
**Migration**: The entry page arrives in `add-accounting-entry-and-rules`.
