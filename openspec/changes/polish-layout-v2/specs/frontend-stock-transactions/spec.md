## ADDED Requirements

### Requirement: Transactions use the shared filter bar

The stock transactions screen SHALL use `<app-filter-bar>` with search and the 全部 / 買進 / 賣出 toggle inline; date range, broker and sort SHALL sit behind 篩選.

#### Scenario: One filter row
- **WHEN** `/portfolio/transactions` renders at 1440 px with no filters active
- **THEN** the filters SHALL occupy one row above the list
