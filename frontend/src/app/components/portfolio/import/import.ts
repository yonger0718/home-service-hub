import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { CheckboxModule } from 'primeng/checkbox';
import { ConfirmDialogModule } from 'primeng/confirmdialog';
import { FileUploadModule } from 'primeng/fileupload';
import { InputTextModule } from 'primeng/inputtext';
import { ConfirmationService, MessageService } from 'primeng/api';
import { SelectButtonModule } from 'primeng/selectbutton';
import { TableModule } from 'primeng/table';
import { ToastModule } from 'primeng/toast';
import { TooltipModule } from 'primeng/tooltip';

import { ImportKind, ImportResult, OverrideStatus, RecalcStatus, UnresolvedName } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

interface ImportOption {
  label: string;
  value: ImportKind;
  hint: string;
}

const POLL_INTERVAL_MS = 5_000;

@Component({
  selector: 'app-portfolio-import',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    CardModule,
    CheckboxModule,
    ConfirmDialogModule,
    FileUploadModule,
    InputTextModule,
    SelectButtonModule,
    TableModule,
    ToastModule,
    TooltipModule,
  ],
  providers: [ConfirmationService, MessageService],
  templateUrl: './import.html',
  styleUrl: './import.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioImportComponent implements OnInit, OnDestroy {
  private portfolioService = inject(PortfolioService);
  private messageService = inject(MessageService);
  private confirmationService = inject(ConfirmationService);

  readonly kindOptions: ImportOption[] = [
    {
      label: '交易',
      value: 'transactions',
      hint:
        '代號(symbol), 類別(type), 股數(quantity), 價格(price), 交易日期(trade_date), 手續費(fee), 稅金(tax), 名稱(name) — 任一語言皆可；' +
        '另可附加 order_id(委託書號/訂單編號/委託編號) 區分同日同價同量交易',
    },
    {
      label: '股利',
      value: 'dividends',
      hint: '代號(symbol), 金額(amount), 除息日(ex_dividend_date), 入帳日(received_date) — 任一語言皆可',
    },
  ];

  readonly kind = signal<ImportKind>('transactions');
  readonly file = signal<File | null>(null);
  readonly busy = signal<boolean>(false);
  readonly result = signal<ImportResult | null>(null);
  readonly recalcStatus = signal<RecalcStatus>({ state: 'idle' });
  readonly hasHeader = signal<boolean>(true);
  readonly nameOverrides = signal<Record<string, string>>({});
  readonly confirmedOverrides = signal<Set<string>>(new Set());
  readonly verifyingNames = signal<Set<string>>(new Set());
  readonly localValidations = signal<Map<string, { status: OverrideStatus; expected_name?: string | null; fetched_name?: string | null }>>(new Map());
  // Persisted across parses so the override panel stays visible after a name moves from
  // "unresolved" → "has override" (otherwise the row would vanish and user couldn't fix a bad code).
  readonly unresolvedNameMeta = signal<Map<string, { occurrences: number; sample_dates: string[] }>>(new Map());

  private pollHandle: ReturnType<typeof setTimeout> | null = null;
  private statusRequestInFlight = false;

  ngOnInit(): void {
    // Refresh might have happened mid-recalc — surface the current state on mount.
    this.fetchRecalcStatus(/*startPollingIfRunning=*/ true);
  }

  ngOnDestroy(): void {
    this.stopPolling();
  }

  get headerHint(): string {
    return this.kindOptions.find(option => option.value === this.kind())?.hint ?? '';
  }

  onSelect(event: { files: File[] }): void {
    const next = event.files?.[0] ?? null;
    this.file.set(next);
    this.result.set(null);
  }

  onClear(): void {
    this.file.set(null);
    this.result.set(null);
    this.nameOverrides.set({});
    this.confirmedOverrides.set(new Set());
    this.verifyingNames.set(new Set());
    this.localValidations.set(new Map());
    this.unresolvedNameMeta.set(new Map());
  }

  readonly unresolvedRows = computed<UnresolvedName[]>(() => {
    const meta = this.unresolvedNameMeta();
    const overrides = this.nameOverrides();
    const names = new Set<string>([...meta.keys(), ...Object.keys(overrides)]);
    return Array.from(names).map(name => {
      const m = meta.get(name);
      return {
        name,
        occurrences: m?.occurrences ?? 0,
        sample_dates: m?.sample_dates ?? [],
      };
    });
  });

  readonly hasOverrideRows = computed<boolean>(() =>
    this.unresolvedNameMeta().size > 0 || Object.keys(this.nameOverrides()).length > 0,
  );

  trackByName = (_: number, u: { name: string }): string => u.name;

  setOverride(name: string, symbol: string): void {
    const trimmed = symbol.trim();
    const next = { ...this.nameOverrides() };
    if (trimmed) {
      next[name] = trimmed;
    } else {
      delete next[name];
    }
    this.nameOverrides.set(next);
    // Editing the override invalidates any prior confirmation + local validation for that name.
    const confirmed = new Set(this.confirmedOverrides());
    confirmed.delete(name);
    this.confirmedOverrides.set(confirmed);
    const local = new Map(this.localValidations());
    local.delete(name);
    this.localValidations.set(local);
  }

  verifyName(name: string, sampleDates: string[]): void {
    const code = this.nameOverrides()[name]?.trim();
    if (!code) {
      this.messageService.add({ severity: 'warn', summary: '請先填入代號', life: 3000 });
      return;
    }
    const tradeDate = sampleDates?.[sampleDates.length - 1] || sampleDates?.[0];
    if (!tradeDate) {
      this.messageService.add({ severity: 'warn', summary: '無樣本日期可驗證', life: 3000 });
      return;
    }
    const pending = new Set(this.verifyingNames());
    pending.add(name);
    this.verifyingNames.set(pending);
    this.portfolioService.verifyOverrideSymbol(name, code, tradeDate).subscribe({
      next: result => {
        const local = new Map(this.localValidations());
        local.set(name, {
          status: result.status,
          expected_name: result.expected_name,
          fetched_name: result.fetched_name,
        });
        this.localValidations.set(local);
        const next = new Set(this.verifyingNames());
        next.delete(name);
        this.verifyingNames.set(next);
      },
      error: err => {
        const next = new Set(this.verifyingNames());
        next.delete(name);
        this.verifyingNames.set(next);
        const detail = err?.error?.detail || err?.message || '未知錯誤';
        this.messageService.add({ severity: 'error', summary: '驗證失敗', detail, life: 5000 });
      },
    });
  }

  isVerifying(name: string): boolean {
    return this.verifyingNames().has(name);
  }

  toggleConfirm(name: string, checked: boolean): void {
    const next = new Set(this.confirmedOverrides());
    if (checked) {
      next.add(name);
    } else {
      next.delete(name);
    }
    this.confirmedOverrides.set(next);
  }

  pendingOverrideCount(): number {
    const meta = this.unresolvedNameMeta();
    const overrides = this.nameOverrides();
    let pending = 0;
    for (const name of meta.keys()) {
      if (!overrides[name]?.trim()) pending += 1;
    }
    return pending;
  }

  validationFor(name: string): { status: OverrideStatus; expected_name?: string | null; fetched_name?: string | null } | null {
    // Latest backend response wins so post-commit (where the server actually
    // verified during the import) supersedes any stale local-per-row state.
    // Local cache only fills the gap before the next preview/commit lands.
    return this.result()?.override_validations?.find(v => v.name === name)
      ?? this.localValidations().get(name)
      ?? null;
  }

  validationIcon(status: OverrideStatus): { icon: string; color: string; label: string } {
    switch (status) {
      case 'verified':
      case 'user_overridden':
        return { icon: 'pi pi-check-circle', color: '#2ecc71', label: '已驗證' };
      case 'name_mismatch':
        return { icon: 'pi pi-exclamation-triangle', color: '#f1c40f', label: '名稱不符' };
      case 'not_traded_on_date':
        return { icon: 'pi pi-exclamation-triangle', color: '#e67e22', label: '當日無交易' };
      case 'fetch_failed':
        return { icon: 'pi pi-question-circle', color: '#95a5a6', label: '查詢失敗' };
    }
  }

  needsConfirm(status: OverrideStatus): boolean {
    return status === 'name_mismatch' || status === 'not_traded_on_date' || status === 'fetch_failed';
  }

  preview(): void {
    this.upload(true);
  }

  commit(): void {
    const unverified = this.unverifiedOverrideNames();
    if (unverified.length === 0) {
      this.upload(false);
      return;
    }
    this.confirmationService.confirm({
      header: '尚有代號未驗證',
      message:
        `您填了 ${Object.keys(this.nameOverrides()).length} 個代號，其中 ${unverified.length} 個尚未通過驗證` +
        `（${unverified.slice(0, 3).join('、')}${unverified.length > 3 ? '…' : ''}）。` +
        '系統會在匯入時自動驗證，未通過的列將被略過。確定送出？',
      acceptLabel: '仍要送出',
      rejectLabel: '取消',
      acceptButtonStyleClass: 'p-button-warning',
      accept: () => this.upload(false),
    });
  }

  private unverifiedOverrideNames(): string[] {
    const overrides = this.nameOverrides();
    const localValidations = this.localValidations();
    const remoteValidations = this.result()?.override_validations ?? [];
    const passed = new Set<string>();
    for (const [name, v] of localValidations.entries()) {
      if (v.status === 'verified' || v.status === 'user_overridden') passed.add(name);
    }
    for (const v of remoteValidations) {
      if (v.status === 'verified' || v.status === 'user_overridden') passed.add(v.name);
    }
    return Object.keys(overrides).filter(n => overrides[n].trim() && !passed.has(n));
  }

  retryRecalc(): void {
    this.portfolioService.triggerRecalc().subscribe({
      next: () => {
        this.messageService.add({
          severity: 'info', summary: '重新觸發重算', life: 3000,
        });
        this.startPolling();
      },
      error: err => {
        const detail = err?.error?.detail || err?.message || '未知錯誤';
        this.messageService.add({
          severity: 'error', summary: '重算觸發失敗', detail, life: 6000,
        });
      },
    });
  }

  private upload(dryRun: boolean): void {
    const file = this.file();
    if (!file) {
      this.messageService.add({ severity: 'warn', summary: '請先選擇 CSV', life: 3000 });
      return;
    }
    this.busy.set(true);
    this.portfolioService.uploadCsv(
      this.kind(),
      file,
      dryRun,
      this.hasHeader(),
      this.nameOverrides(),
      Array.from(this.confirmedOverrides()),
    ).subscribe({
      next: result => {
        this.result.set(result);
        this.busy.set(false);
        // Accumulate unresolved-name metadata so the override panel persists even after
        // the user types an override (which removes the name from result.unresolved_names).
        if (result.unresolved_names && result.unresolved_names.length > 0) {
          const merged = new Map(this.unresolvedNameMeta());
          for (const u of result.unresolved_names) {
            const prior = merged.get(u.name);
            const sampleDates = (prior?.sample_dates?.length ?? 0) >= (u.sample_dates?.length ?? 0)
              ? prior!.sample_dates
              : u.sample_dates;
            merged.set(u.name, {
              occurrences: Math.max(prior?.occurrences ?? 0, u.occurrences ?? 0),
              sample_dates: sampleDates,
            });
          }
          this.unresolvedNameMeta.set(merged);
        }
        const summary = dryRun ? '預覽完成' : '匯入完成';
        const parts = [
          `已解析 ${result.parsed} 筆`,
          `${dryRun ? '可新增' : '已新增'} ${result.created} 筆`,
          `重複略過 ${result.skipped_duplicates} 筆`,
          `錯誤 ${result.errors.length} 筆`,
        ];
        if (result.rehashed && result.rehashed > 0) {
          parts.push(`重算指紋 ${result.rehashed} 筆`);
        }
        if (result.skipped_unresolved && result.skipped_unresolved > 0) {
          parts.push(`未識別股名 ${result.skipped_unresolved} 筆（請於下方填代號）`);
        }
        if (result.skipped_unverified && result.skipped_unverified > 0) {
          parts.push(`代號未驗證 ${result.skipped_unverified} 筆（請於下方確認）`);
        }
        const detail = parts.join('；');
        const severity =
          result.errors.length > 0 ||
          (result.skipped_unresolved ?? 0) > 0 ||
          (result.skipped_unverified ?? 0) > 0
            ? 'warn'
            : 'success';
        this.messageService.add({ severity, summary, detail, life: 5000 });
        if (!dryRun && result.recalc_scheduled) {
          this.messageService.add({
            severity: 'info', summary: '資料重算執行中…',
            detail: '稍後將自動更新淨值圖與股利紀錄', life: 4000,
          });
          this.startPolling();
        }
      },
      error: err => {
        this.busy.set(false);
        const detail = err?.error?.detail || err?.error?.message || err?.message || '未知錯誤';
        this.messageService.add({ severity: 'error', summary: '匯入失敗', detail, life: 6000 });
      },
    });
  }

  private startPolling(): void {
    this.stopPolling();
    this.fetchRecalcStatus(true);
  }

  private scheduleNextPoll(): void {
    this.stopPolling();
    this.pollHandle = setTimeout(() => this.fetchRecalcStatus(false), POLL_INTERVAL_MS);
  }

  private stopPolling(): void {
    if (this.pollHandle !== null) {
      clearTimeout(this.pollHandle);
      this.pollHandle = null;
    }
  }

  private fetchRecalcStatus(startPollingIfRunning: boolean): void {
    // In-flight guard: a slow status response must not race a freshly-fired one.
    // The chained setTimeout below only schedules the next poll after the current
    // request settles, so this guard is mainly belt-and-suspenders for the
    // ngOnInit + post-commit double-call edges.
    if (this.statusRequestInFlight) return;
    this.statusRequestInFlight = true;
    this.portfolioService.getRecalcStatus().pipe(
      finalize(() => {
        this.statusRequestInFlight = false;
      }),
    ).subscribe({
      next: status => {
        const prevState = this.recalcStatus().state;
        this.recalcStatus.set(status);
        if (status.state === 'running') {
          // Keep polling as long as we were polling already (prevState=running),
          // or the caller explicitly asked us to start (ngOnInit / startPolling).
          if (startPollingIfRunning || prevState === 'running') {
            this.scheduleNextPoll();
          }
          return;
        }
        // Settled: emit completion toast only on transitions out of 'running'.
        // (status.state is narrowed away from 'running' by the early return above.)
        const justFinished = prevState === 'running';
        if (status.state === 'completed' && justFinished) {
          this.messageService.add({
            severity: 'success', summary: '資料重算完成', life: 4000,
          });
        } else if (status.state === 'partial' && justFinished) {
          const failed = (status.steps ?? [])
            .filter(s => s.status === 'failed' || s.status === 'partial')
            .map(s => s.name)
            .join(', ');
          this.messageService.add({
            severity: 'warn',
            summary: '資料重算部分失敗',
            detail: `失敗步驟：${failed || '未知'}。可點擊「重試」`,
            life: 8000,
          });
        } else if (status.state === 'failed' && justFinished) {
          const firstError = (status.steps ?? []).find(s => s.error)?.error ?? '未知錯誤';
          this.messageService.add({
            severity: 'error',
            summary: '資料重算失敗',
            detail: firstError,
            life: 8000,
          });
        }
        this.stopPolling();
      },
      error: () => {
        // Status endpoint failing should not loop forever, and should not leave
        // the banner stuck on 'running' (which would mislead the user into
        // thinking work is still happening).
        this.recalcStatus.update(prev =>
          prev.state === 'running' ? { ...prev, state: 'failed' } : prev,
        );
        this.messageService.add({
          severity: 'warn',
          summary: '無法取得重算狀態',
          detail: '請稍後重試，或手動點擊「重試」',
          life: 5000,
        });
        this.stopPolling();
      },
    });
  }

  payloadKeys(rows: ImportResult['rows']): string[] {
    return rows.length === 0 ? [] : Object.keys(rows[0].payload);
  }
}
