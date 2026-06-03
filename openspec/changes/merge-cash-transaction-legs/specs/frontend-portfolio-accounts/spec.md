## ADDED Requirements

### Requirement: Merge toggle on account detail page

The account detail page SHALL provide a toggle control labeled `合併同筆交易` next to the cash transactions section header. When ON, the page sends `merge_related=true` to the cash-transactions endpoint and renders synthetic group rows with an expand/collapse chevron that reveals the original legs inline (no additional API call). When OFF, the page renders individual leg rows as before.

Toggle state SHALL persist per account in `localStorage` under key `accounts.merge.<account_id>`. Default state on first visit is OFF.

#### Scenario: Toggle defaults OFF for new account

- **GIVEN** the user visits `/portfolio/accounts/1` for the first time (no localStorage key)
- **WHEN** the cash transactions list loads
- **THEN** the toggle shows OFF
- **AND** the request to the cash-transactions endpoint omits `merge_related` (or sends `false`)
- **AND** individual leg rows are rendered

#### Scenario: Toggle ON groups BUY/SELL legs

- **GIVEN** a BUY transaction with 3 cash legs in the current page
- **WHEN** the user flips the toggle to ON
- **THEN** the request re-fires with `merge_related=true`
- **AND** the BUY row renders as ONE list item showing summed amount + leg-count badge + chevron control
- **AND** clicking the chevron expands the row inline to show the 3 legs (settle / fee / tax) with their individual amounts
- **AND** localStorage `accounts.merge.1` = `"1"`

#### Scenario: Toggle state persists across navigations

- **GIVEN** the user toggled merge ON for account 1 earlier in the session
- **WHEN** the user navigates away and returns to `/portfolio/accounts/1`
- **THEN** the toggle initializes ON
- **AND** the first fetch sends `merge_related=true`

#### Scenario: Toggle state independent per account

- **GIVEN** localStorage `accounts.merge.1` = `"1"` and no key for account 2
- **WHEN** the user opens `/portfolio/accounts/2`
- **THEN** the toggle for account 2 initializes OFF
- **AND** the fetch for account 2 omits `merge_related`

#### Scenario: Paginator resets to page 1 on toggle change

- **GIVEN** the user is on page 3 of an unmerged list
- **WHEN** they flip the toggle to ON
- **THEN** the paginator resets `offset` to 0 and the request re-fires with `merge_related=true&offset=0`
