import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ScheduleDefinitionInput } from '../models/accounting.model';
import { makeDefinition, makeInstance } from '../components/accounting/testing/fixtures';
import { AccountingService } from './accounting.service';

const INPUT: ScheduleDefinitionInput = {
  kind: 'recurring',
  name: 'Netflix',
  template: {
    lines: [
      {
        kind: 'expense', account_id: 2, to_account_id: null, to_amount: null, counterparty_id: null, category_id: 41,
        project_id: null, amount: '390', currency: 'TWD', loan_entry_id: null, name: null, merchant: null,
      },
    ],
    description: null,
    tags: [],
  },
  interval_unit: 'month',
  interval_n: 1,
  anchor_date: '2026-10-22',
  day_of_month: null,
  times: null,
  end_date: null,
  total_amount: null,
  posting_mode: 'auto',
  loan: null,
};

describe('AccountingService schedules', () => {
  let service: AccountingService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    service = TestBed.inject(AccountingService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('lists definitions with filters and reads one', () => {
    service.getScheduleDefinitions({ status: 'active', kind: 'installment' }).subscribe();
    const list = http.expectOne(r => r.url === '/api/accounting/schedules/definitions');
    expect(list.request.params.get('status')).toBe('active');
    expect(list.request.params.get('kind')).toBe('installment');
    list.flush([makeDefinition()]);
    service.getScheduleDefinition(5).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/5').flush({ ...makeDefinition({ id: 5 }), instances: [] });
  });

  it('creates and updates a definition and bumps entriesChanged', () => {
    const before = service.entriesChanged();
    service.createScheduleDefinition(INPUT).subscribe();
    const create = http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions');
    expect(create.request.body).toEqual(INPUT);
    create.flush(makeDefinition({ id: 9 }));
    const { kind: _kind, loan: _loan, ...update } = INPUT;
    service.updateScheduleDefinition(9, update).subscribe();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/definitions/9');
    expect(put.request.body).not.toHaveProperty('kind');
    put.flush(makeDefinition({ id: 9 }));
    expect(service.entriesChanged()).toBe(before + 2);
  });

  it('sends the state actions', () => {
    service.pauseSchedule(3).subscribe();
    http.expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/definitions/3/pause').flush(makeDefinition());
    service.resumeSchedule(3, 'post').subscribe();
    const resume = http.expectOne('/api/accounting/schedules/definitions/3/resume');
    expect(resume.request.body).toEqual({ backlog: 'post' });
    resume.flush(makeDefinition());
    service.setScheduleMode(3, 'confirm').subscribe();
    const mode = http.expectOne('/api/accounting/schedules/definitions/3/mode');
    expect([mode.request.method, mode.request.body]).toEqual(['PUT', { posting_mode: 'confirm' }]);
    mode.flush(makeDefinition());
    service.endSchedule(3).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/3/end').flush(makeDefinition());
    service.catchUpSchedule(3).subscribe();
    http.expectOne('/api/accounting/schedules/definitions/3/catch-up').flush({ posted: [], failed: null, definition: makeDefinition() });
    service.deleteScheduleDefinition(3).subscribe();
    http.expectOne(r => r.method === 'DELETE' && r.url === '/api/accounting/schedules/definitions/3').flush(null);
  });

  it('reads the queue and sends the instance actions', () => {
    service.getScheduleInstances({ queue: true, until: '2026-11-02' }).subscribe();
    const queue = http.expectOne(r => r.url === '/api/accounting/schedules/instances');
    expect(queue.request.params.get('until')).toBe('2026-11-02');
    expect(queue.request.params.get('queue')).toBe('true');
    queue.flush([makeInstance()]);
    for (const [call, path] of [
      [() => service.postScheduleInstance(7), 'post'],
      [() => service.skipScheduleInstance(7), 'skip'],
      [() => service.reopenScheduleInstance(7), 'reopen'],
      [() => service.acceptPartialScheduleInstance(7), 'accept-partial'],
    ] as const) {
      call().subscribe();
      http.expectOne(r => r.method === 'POST' && r.url === `/api/accounting/schedules/instances/7/${path}`).flush(makeInstance());
    }
    service.repostScheduleInstance(7, ['8333', '598']).subscribe();
    const repost = http.expectOne('/api/accounting/schedules/instances/7/repost');
    expect(repost.request.body).toEqual({ amounts: ['8333', '598'] });
    repost.flush(makeInstance());
    service.updateScheduleInstance(7, { due_date: '2026-11-12' }).subscribe();
    const put = http.expectOne(r => r.method === 'PUT' && r.url === '/api/accounting/schedules/instances/7');
    expect(put.request.body).toEqual({ due_date: '2026-11-12' });
    put.flush(makeInstance());
  });

  it('runs the job now', () => {
    service.runSchedulesNow().subscribe();
    http
      .expectOne(r => r.method === 'POST' && r.url === '/api/accounting/schedules/run-now')
      .flush({ trigger: 'manual', today: '2026-10-03', status: 'completed', generated: 0, posted: [], failed: [], stopped_definitions: [] });
  });
});
