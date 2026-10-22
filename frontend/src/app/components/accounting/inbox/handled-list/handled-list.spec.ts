import { HttpErrorResponse, provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AccountingService } from '../../../../services/accounting.service';
import { inboxErrorMessage } from '../inbox-view';
import { ReconciliationAction } from '../inbox.model';
import { button, text } from '../testing';
import { AppInboxHandledListComponent } from './handled-list';

const ACTIONS_URL = '/api/accounting/reconciliation/actions';

function makeAction(overrides: Partial<ReconciliationAction> = {}): ReconciliationAction {
  return {
    id: 1,
    case_id: 11,
    proposal_id: null,
    action: 'dismiss',
    params: {},
    actor: 'owner',
    status: 'applied',
    effect_slot: 'case',
    created_entry_ids: [],
    deleted_entry_ids: [],
    touched_entry_ids: [],
    touched_group_ids: [],
    replayed: false,
    label: 'owner',
    answer: '忽略',
    created_at: '2026-10-20T08:00:00Z',
    reverted_by_action_id: null,
    ...overrides,
  };
}

/** A rule-applied 忽略 (rule #7), a policy link and the owner's 記為, then two rows that cannot be reverted. */
const RULE_ROW = makeAction({ id: 1, actor: 'policy', label: 'rule:7', answer: null, action: 'dismiss' });
const POLICY_ROW = makeAction({
  id: 2,
  actor: 'policy',
  label: 'policy',
  answer: null,
  action: 'link_entry',
  created_at: '2026-10-19T08:00:00Z',
});
const OWNER_ROW = makeAction({ id: 3, answer: '記為 餐飲', action: 'record', created_at: '2026-10-18T08:00:00Z' });
const REVERTED_ROW = makeAction({ id: 4, reverted_by_action_id: 9, created_at: '2026-10-17T08:00:00Z' });
/** Each of the two hide conditions on its own. */
const UNAPPLIED_ROW = makeAction({ id: 5, status: 'reverted', answer: null, created_at: '2026-10-16T08:00:00Z' });

describe('AppInboxHandledListComponent', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [AppInboxHandledListComponent],
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function flushActions(rows: ReconciliationAction[]): void {
    const req = http.expectOne((r) => r.method === 'GET' && r.url === ACTIONS_URL);
    expect(req.request.params.get('limit')).toBe('50');
    req.flush(rows);
  }

  function render(rows: ReconciliationAction[]) {
    const fixture = TestBed.createComponent(AppInboxHandledListComponent);
    const changed = vi.fn();
    fixture.componentInstance.changed.subscribe(changed);
    const entriesChanged = vi.spyOn(TestBed.inject(AccountingService), 'markEntriesChanged');
    fixture.detectChanges();
    flushActions(rows);
    fixture.detectChanges();
    return { fixture, el: fixture.nativeElement as HTMLElement, changed, entriesChanged };
  }

  function row(el: HTMLElement, id: number): HTMLElement {
    return el.querySelector<HTMLElement>(`.handled-row[data-action-id="${id}"]`)!;
  }

  function settle(fixture: ComponentFixture<AppInboxHandledListComponent>, rows: ReconciliationAction[]): void {
    fixture.detectChanges();
    flushActions(rows);
    fixture.detectChanges();
  }

  it('labels rule, policy and owner rows', () => {
    const { el } = render([RULE_ROW, POLICY_ROW, OWNER_ROW, REVERTED_ROW, UNAPPLIED_ROW]);
    expect(Array.from(el.querySelectorAll('.handled-row .what')).map(text)).toEqual([
      '規則 #7 · dismiss · 2026/10/20',
      '系統 · link_entry · 2026/10/19',
      '你 · 記為 餐飲 · 2026/10/18',
      '你 · 忽略 · 2026/10/17',
      '你 · dismiss · 2026/10/16',
    ]);
    expect(button(row(el, 1), '撤銷')).toBeDefined();
    expect(button(row(el, 2), '撤銷')).toBeDefined();
    expect(button(row(el, 3), '撤銷')).toBeDefined();
    // Already reverted, or not an applied action: nothing to undo.
    expect(row(el, 4).querySelector('button')).toBeNull();
    expect(row(el, 5).querySelector('button')).toBeNull();
  });

  it('revert posts and emits changed', () => {
    const { fixture, el, changed, entriesChanged } = render([OWNER_ROW]);
    button(row(el, 3), '撤銷').click();
    const req = http.expectOne((r) => r.method === 'POST' && r.url === `${ACTIONS_URL}/3/revert`);
    expect(req.request.body).toEqual({});
    req.flush({});
    expect(changed).toHaveBeenCalledTimes(1);
    expect(entriesChanged).toHaveBeenCalledTimes(1);

    // The revert bumped inbox.changed: the list re-reads and the row has nothing left to undo.
    settle(fixture, [makeAction({ ...OWNER_ROW, reverted_by_action_id: 20 })]);
    expect(row(el, 3).querySelector('button')).toBeNull();
    expect(text(el)).not.toContain('同時停用這條規則');
  });

  it('rule row offers 同時停用這條規則 after revert', () => {
    const { fixture, el, changed } = render([RULE_ROW]);
    expect(text(row(el, 1))).not.toContain('同時停用這條規則');

    button(row(el, 1), '撤銷').click();
    http.expectOne((r) => r.method === 'POST' && r.url === `${ACTIONS_URL}/1/revert`).flush({});
    expect(changed).toHaveBeenCalledTimes(1);
    settle(fixture, [makeAction({ ...RULE_ROW, reverted_by_action_id: 21 })]);
    expect(button(row(el, 1), '撤銷')).toBeUndefined();

    button(row(el, 1), '同時停用這條規則').click();
    const patch = http.expectOne(
      (r) => r.method === 'PATCH' && r.url === '/api/accounting/reconciliation/owner-rules/7',
    );
    expect(patch.request.body).toEqual({ enabled: false });
    patch.flush({});
    settle(fixture, [makeAction({ ...RULE_ROW, reverted_by_action_id: 21 })]);
    expect(text(row(el, 1))).not.toContain('同時停用這條規則');
  });

  it('not_revertable shows 不能撤銷', () => {
    const { fixture, el, changed, entriesChanged } = render([OWNER_ROW, POLICY_ROW]);
    button(row(el, 3), '撤銷').click();
    http
      .expectOne((r) => r.method === 'POST' && r.url === `${ACTIONS_URL}/3/revert`)
      .flush(
        { detail: [{ loc: ['action_id'], msg: 'not_revertable: later action 12', type: 'value_error' }] },
        { status: 422, statusText: 'Unprocessable Entity' },
      );
    fixture.detectChanges();
    expect(text(row(el, 3).querySelector('.handled-error'))).toBe('不能撤銷');
    expect(changed).not.toHaveBeenCalled();
    expect(entriesChanged).not.toHaveBeenCalled();

    // Any other failure: the inbox wording, on that row only.
    button(row(el, 2), '撤銷').click();
    http
      .expectOne((r) => r.method === 'POST' && r.url === `${ACTIONS_URL}/2/revert`)
      .flush('boom', { status: 500, statusText: 'Server Error' });
    fixture.detectChanges();
    const expected = inboxErrorMessage(
      new HttpErrorResponse({ error: 'boom', status: 500, statusText: 'Server Error' }),
    );
    expect(text(row(el, 2).querySelector('.handled-error'))).toBe(expected);
    expect(text(row(el, 3).querySelector('.handled-error'))).toBe('不能撤銷');
  });
});
