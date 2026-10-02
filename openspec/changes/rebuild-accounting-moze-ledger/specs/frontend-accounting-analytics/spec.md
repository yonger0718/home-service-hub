## REMOVED Requirements

### Requirement: Expense doughnut with custom legend
**Reason**: Built on the removed single-level category model.
**Migration**: Category reports over the new two-level categories arrive in `add-accounting-insights`.

### Requirement: Category-change list
**Reason**: Built on the removed category model.
**Migration**: Replaced by reports in `add-accounting-insights`.

### Requirement: Credit-card limit monitor
**Reason**: The legacy `credit_cards` table is removed.
**Migration**: Credit-card statement cycles and limits arrive in `add-accounting-entry-and-rules`.

### Requirement: Cashflow colours decoupled from data-gainloss
**Reason**: The dashboard it styles is removed.
**Migration**: New report views define their own colour rules in `add-accounting-insights`.
