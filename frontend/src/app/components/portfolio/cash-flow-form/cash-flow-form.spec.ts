import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BrokerCashBalance } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { ToastService } from '../../../services/toast.service';
import { CashFlowFormComponent } from './cash-flow-form';

describe('CashFlowFormComponent save feedback', () => {
  const result = { broker: 'TW_CATHAY', currency: 'TWD' } as unknown as BrokerCashBalance;
  let service: { createBrokerCashFlow: ReturnType<typeof vi.fn> };

  beforeEach(() => {
    service = { createBrokerCashFlow: vi.fn() };
    TestBed.configureTestingModule({ providers: [{ provide: PortfolioService, useValue: service }] });
  });

  function open() {
    const fixture = TestBed.createComponent(CashFlowFormComponent);
    fixture.componentRef.setInput('visible', true);
    fixture.detectChanges();
    return fixture;
  }

  it('a 重試 that succeeds after the dialog was closed and reopened leaves what the user typed alone', () => {
    service.createBrokerCashFlow.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 500 })));
    const fixture = open();
    const component = fixture.componentInstance;
    const created: unknown[] = [];
    component.created.subscribe(value => created.push(value));
    component.form.patchValue({ amount: '1234' });
    component.submit();
    const toasts = TestBed.inject(ToastService);
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error' }));

    component.close();
    fixture.componentRef.setInput('visible', false);
    fixture.detectChanges();
    fixture.componentRef.setInput('visible', true);
    fixture.detectChanges();
    component.form.patchValue({ amount: '99' });

    service.createBrokerCashFlow.mockReturnValueOnce(of(result));
    toasts.current()!.action!.run();

    expect(created).toEqual([result]);
    expect(component.visible).toBe(true);
    expect(component.form.getRawValue().amount).toBe('99');
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'done' }));
  });

  it('a 重試 pressed while a fresh save is in flight sends nothing and keeps the toast', () => {
    service.createBrokerCashFlow.mockReturnValueOnce(throwError(() => new HttpErrorResponse({ status: 500 })));
    const fixture = open();
    const component = fixture.componentInstance;
    component.form.patchValue({ amount: '1234' });
    component.submit();
    const toasts = TestBed.inject(ToastService);
    const retry = toasts.current()!.action!;

    service.createBrokerCashFlow.mockReturnValueOnce(new Subject());
    component.submit();
    expect(service.createBrokerCashFlow).toHaveBeenCalledTimes(2);

    retry.run();
    expect(service.createBrokerCashFlow).toHaveBeenCalledTimes(2);
    expect(toasts.current()).toEqual(expect.objectContaining({ kind: 'error' }));
    expect(toasts.current()?.action?.label).toBe('重試');
  });
});
