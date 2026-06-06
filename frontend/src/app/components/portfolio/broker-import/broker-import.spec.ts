import { TestBed } from '@angular/core/testing';
import { MessageService } from 'primeng/api';
import { of, throwError } from 'rxjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BrokerCsvImportResult } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';
import { PortfolioBrokerImportComponent } from './broker-import';

describe('PortfolioBrokerImportComponent', () => {
  let portfolioService: {
    uploadBrokerCsv: ReturnType<typeof vi.fn>;
  };

  beforeEach(async () => {
    portfolioService = {
      uploadBrokerCsv: vi.fn().mockReturnValue(of(result({ dry_run: true }))),
    };

    await TestBed.configureTestingModule({
      imports: [PortfolioBrokerImportComponent],
      providers: [{ provide: PortfolioService, useValue: portfolioService }],
    }).compileComponents();
  });

  it('shows dry-run preview rows after selecting a file', () => {
    const fixture = TestBed.createComponent(PortfolioBrokerImportComponent);
    fixture.detectChanges();

    fixture.componentInstance.onFileSelected(fileEvent(csvFile('ft.csv')));
    fixture.detectChanges();

    expect(portfolioService.uploadBrokerCsv).toHaveBeenCalledWith(expect.any(File), true);
    expect(fixture.nativeElement.textContent).toContain('FIRSTRADE');
    expect(fixture.nativeElement.textContent).toContain('AAPL');
    expect(fixture.nativeElement.textContent).toContain('deposit');
    expect(fixture.nativeElement.textContent).not.toContain('已新增 1');
  });

  it('commits the selected file and emits a success toast', () => {
    const fixture = TestBed.createComponent(PortfolioBrokerImportComponent);
    fixture.detectChanges();
    const messageService = fixture.debugElement.injector.get(MessageService);
    const toastSpy = vi.spyOn(messageService, 'add');

    fixture.componentInstance.file.set(csvFile('ib.csv'));
    portfolioService.uploadBrokerCsv.mockReturnValue(of(result({
      detected_broker: 'IB',
      dry_run: false,
      counts: { created: 2, skipped: 1, rejected: 0 },
    })));

    fixture.componentInstance.commit();
    fixture.detectChanges();

    expect(portfolioService.uploadBrokerCsv).toHaveBeenCalledWith(expect.any(File), false);
    expect(toastSpy).toHaveBeenCalledWith(expect.objectContaining({ severity: 'success' }));
    expect(fixture.nativeElement.textContent).toContain('已新增 2');
    expect(fixture.componentInstance.file()).toBeNull();
  });

  it('surfaces row-indexed parser errors from a failed upload response', () => {
    const fixture = TestBed.createComponent(PortfolioBrokerImportComponent);
    fixture.detectChanges();
    fixture.componentInstance.file.set(csvFile('cs.csv'));
    portfolioService.uploadBrokerCsv.mockReturnValue(throwError(() => ({
      error: {
        errors: [{ row_index: 3, reason: 'missing FX rate for 2026-06-02 USD' }],
      },
    })));

    fixture.componentInstance.preview();
    fixture.detectChanges();

    expect(fixture.nativeElement.textContent).toContain('第 3 列');
    expect(fixture.nativeElement.textContent).toContain('missing FX rate for 2026-06-02 USD');
    expect(fixture.componentInstance.file()).not.toBeNull();
  });
});

function csvFile(name: string): File {
  return new File(['symbol,type\nAAPL,BUY'], name, { type: 'text/csv' });
}

function fileEvent(file: File): Event {
  return { target: { files: [file], value: '' } } as unknown as Event;
}

function result(overrides: Partial<BrokerCsvImportResult> = {}): BrokerCsvImportResult {
  return {
    detected_broker: 'FIRSTRADE',
    dry_run: true,
    transactions: [{
      trade_date: '2026-06-05',
      symbol: 'AAPL',
      type: 'BUY',
      quantity: '1',
      price: '190.50',
      broker: 'FIRSTRADE',
    }],
    cash_flows: [{
      date: '2026-06-05',
      broker: 'FIRSTRADE',
      type: 'deposit',
      amount: '2500.00',
      currency: 'USD',
    }],
    counts: { created: 1, skipped: 0, rejected: 0 },
    errors: [],
    ...overrides,
  };
}
