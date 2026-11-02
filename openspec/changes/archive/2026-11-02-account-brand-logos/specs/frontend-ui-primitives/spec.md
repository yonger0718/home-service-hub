## MODIFIED Requirements

### Requirement: Line icons for interface chrome

Buttons, badges, markers and navigation SHALL use the app's line icon set (PrimeIcons). Emoji SHALL appear only in icon slots that show owner-chosen category, account or entry-kind icons; such a slot MAY fall back to a default emoji when the owner chose none, so it stays visually consistent with its neighbours. An owner-chosen account logo image from the app's bundled brand-logo set MAY render in the same slots. The text glyphs ✕ ✓ ‹ › remain allowed. A unit test SHALL fail when a template places an emoji outside an interpolation.

#### Scenario: Entry detail actions
- **WHEN** an entry detail renders its 編輯, 複製, 退款 and 刪除 actions
- **THEN** each SHALL use a line icon, not a Unicode glyph or emoji

#### Scenario: Template gate
- **WHEN** a template adds a static emoji (for example `<span>🗑</span>`) outside an interpolation or comment
- **THEN** the unit suite SHALL fail and name the file and line
