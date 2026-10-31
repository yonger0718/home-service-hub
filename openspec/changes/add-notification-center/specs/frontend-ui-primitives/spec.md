## ADDED Requirements

### Requirement: Toast service

The SPA SHALL show transient feedback through one shell-level `ToastService`: one toast at a time, newest replacing older; variants 完成 (5 s, optional 復原), 進行中 (until replaced) and 失敗 (until closed, optional 重試). Toasts SHALL sit at the bottom-left of the content area at ≥ 760 px and above the tab bar below it, with `role="status"` or `role="alert"` for 失敗. No page SHALL render `<p-toast>` or its own toast.

#### Scenario: Success with undo
- **WHEN** a reconciliation answer is applied
- **THEN** a 完成 toast SHALL offer 復原, which calls the action's revert endpoint

#### Scenario: Failure stays
- **WHEN** a save fails with a network error
- **THEN** a 失敗 toast SHALL stay until closed and offer 重試

### Requirement: Banner primitive

The UI library SHALL provide `<app-banner>` with tones 錯誤, 注意 and 資訊, a title, one line of detail and one action. 錯誤 and 注意 SHALL not be dismissible and SHALL disappear when their condition clears; 資訊 MAY be dismissed and stay dismissed on the device. Page-level alerts (import failure, bill read error, failing schedule, quote delay) SHALL use it.

#### Scenario: Import failure banner
- **WHEN** the latest import failed
- **THEN** `/portfolio/import` SHALL show an 錯誤 banner with the time, file and reason, without a close button
