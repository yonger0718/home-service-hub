import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  effect,
  inject,
  output,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { switchMap } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { injectToasts } from '../../../services/toast.service';
import { isHandledKey } from '../../accounting/accounting-ui';
import { EinvoiceLinkTarget, InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { EinvoiceService } from '../../accounting/inbox/einvoice.service';
import { invoiceAmount } from '../../accounting/inbox/einvoice-format';
import { einvoiceErrorText } from '../../accounting/inbox/einvoice-section/einvoice-section';
import { AppEinvoiceTargetSheetComponent, targetBody } from '../../accounting/inbox/einvoice-target-sheet/einvoice-target-sheet';
import { InboxService } from '../../accounting/inbox/inbox.service';
import { BannerComponent } from '../../ui/banner/banner';
import { OPEN_DIALOG, keyHint, typing } from '../flow-keys';
import { EINVOICE_UNMATCHED_LINK, INBOX_READ_LIMIT } from '../sources/recon';
import {
  EinvoiceFlowSession,
  EinvoiceFlowState,
  EinvoiceOutcome,
  afterRead,
  answer,
  move,
  progress,
  restore,
  resume,
  skip,
  startFlow,
} from './einvoice-flow-session';
import { ENTRY_FORM, answerKeys, einvoiceStep, invoiceEntryParams } from './einvoice-flow-view';

/**
 * The e-invoice flow (D10) at `/notifications/einvoices`: the ambiguous / near invoices one at a time, beside the list
 * from 1024 px, full screen below, in the review flow's frame. Keys 1–N pick a candidate, N + 1 is 都不是, S skips,
 * ← → move, Esc closes. The order and answers live in `EinvoiceFlowSession`, so 記成新的一筆 (the entry form, another
 * route) comes back to the next invoice.
 */
@Component({
  selector: 'app-einvoice-flow',
  standalone: true,
  imports: [BannerComponent, AppEinvoiceTargetSheetComponent, RouterLink],
  templateUrl: './einvoice-flow.html',
  styleUrls: ['../recon-flow/recon-flow.scss', './einvoice-flow.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'region',
    'aria-label': '電子發票',
    tabindex: '-1',
    '[class.flow--panes]': 'panes()',
    '(document:keydown)': 'onKey($event)',
  },
})
export class EinvoiceFlowComponent {
  private readonly inbox = inject(InboxService);
  private readonly einvoices = inject(EinvoiceService);
  private readonly accounting = inject(AccountingService);
  private readonly layout = inject(LayoutModeService);
  private readonly session = inject(EinvoiceFlowSession);
  private readonly router = inject(Router);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);
  private readonly toasts = injectToasts();
  private readonly destroyRef = inject(DestroyRef);
  /** The newest read: an older read's answer (an earlier write's refresh landing late) is dropped. */
  private readSeq = 0;

  readonly closed = output<void>();

  readonly panes = computed(() => this.layout.mode() === 'panes');
  readonly load = signal<'loading' | 'failed' | 'ready'>('loading');
  /** From a press until its write answers. */
  readonly busy = signal(false);
  /** The failed write's text, shown under the answers of the invoice it was for. */
  readonly error = signal<{ id: number; text: string } | null>(null);
  /** `counts.einvoice_unmatched` of the last read: the end state links to those invoices (D10). */
  readonly unmatched = signal(0);
  readonly unmatchedLink = EINVOICE_UNMATCHED_LINK;
  /** 選其他帳… is open for this invoice. */
  readonly picking = signal<InboxEinvoice | null>(null);

  private readonly state = this.session.state;
  readonly invoice = computed(() => {
    const s = this.state();
    return s && s.cursor !== null ? (s.open.get(s.cursor) ?? null) : null;
  });
  readonly step = computed(() => {
    const invoice = this.invoice();
    return invoice ? einvoiceStep(invoice) : null;
  });
  readonly keys = computed(() => answerKeys(this.invoice()?.candidates.length ?? 0));
  readonly keyText = computed(() => {
    const k = this.keys();
    return keyHint(k.dismiss ?? k.candidates.filter((d) => d !== null).length, '張');
  });
  readonly progressInfo = computed(() => {
    const s = this.state();
    return s ? progress(s) : { position: 0, total: 0, handled: 0, skipped: 0 };
  });
  readonly progressText = computed(() => {
    const p = this.progressInfo();
    if (p.position === 0) return `已處理 ${p.handled} / ${p.total} 張`;
    return this.panes() ? `第 ${p.position} / ${p.total} 張` : `${p.position} / ${p.total}`;
  });
  readonly handledPercent = computed(() => {
    const p = this.progressInfo();
    return p.total ? (p.handled / p.total) * 100 : 0;
  });
  readonly errorText = computed(() => {
    const e = this.error();
    return e && e.id === this.invoice()?.id ? e.text : null;
  });
  readonly endTitle = computed(() => {
    const skipped = this.progressInfo().skipped;
    return skipped > 0 ? `還有 ${skipped} 張略過` : '發票都處理完了';
  });
  /** 「連結 a 張 · 都不是 b 張 · 記成新的一筆 c 張 · 略過 K 張」, zero parts left out. */
  readonly endLine = computed(() => {
    const s = this.state();
    if (!s) return '';
    const count = (o: EinvoiceOutcome) => [...s.outcome.values()].filter((v) => v === o).length;
    return [
      ['連結', count('linked')],
      ['都不是', count('dismissed')],
      ['記成新的一筆', count('recorded')],
      ['略過', s.skipped.size],
    ]
      .filter(([, n]) => (n as number) > 0)
      .map(([label, n]) => `${label} ${n} 張`)
      .join(' · ');
  });

  constructor() {
    if (this.session.pendingRecord() === null) {
      this.session.state.set(null); // a fresh open (not the way back from 記成新的一筆)
    }
    // Every entries write (this flow's, the entry form's, another page's) re-reads: invoices answered elsewhere leave.
    let first = true;
    effect(() => {
      this.accounting.entriesChanged();
      untracked(() => {
        if (first) {
          first = false;
          this.read();
        } else if (this.state()) {
          this.read();
        }
      });
    });
  }

  retryLoad(): void {
    this.read();
  }

  private read(): void {
    if (!this.state()) this.load.set('loading');
    const seq = ++this.readSeq;
    // Cancelled with the flow: a read landing after the flow closed (on the way to the entry form) must not consume
    // the pending 記成新的一筆.
    this.inbox.get({ limit: INBOX_READ_LIMIT }).pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (out) => {
        if (seq !== this.readSeq) return;
        const list = out.einvoices ?? [];
        this.unmatched.set(out.counts.einvoice_unmatched ?? 0);
        // A cut page (more to confirm than listed) proves nothing about an invoice it does not list.
        const complete = list.filter((invoice) => invoice.state !== 'unmatched').length >= (out.counts.einvoice_ask ?? 0);
        let current = this.state();
        const pending = this.session.pendingRecord();
        // Counted only when an entry carrying this invoice's number was created since: not any write (a schedule
        // definition, another page's edit), not a cancel; several saves (連續記帳) count once. Answered before the
        // read is applied: the save links the invoice (F3a), so the read no longer lists it and would drop it.
        const saved = !!pending && this.accounting.invoiceCreatedSince(pending.number, pending.mark);
        if (pending) {
          this.session.pendingRecord.set(null);
          if (saved && current?.open.has(pending.id)) {
            current = answer(current, pending.id, 'recorded');
          }
        }
        let next: EinvoiceFlowState = current ? afterRead(current, list, complete) : startFlow(list);
        if (pending && next.open.has(pending.id)) {
          next = saved ? answer(next, pending.id, 'recorded') : { ...next, cursor: pending.id };
        }
        this.state.set(next);
        this.load.set('ready');
      },
      error: () => {
        if (seq !== this.readSeq) return;
        if (!this.state()) this.load.set('failed');
      },
    });
  }

  link(index: number): void {
    const invoice = this.invoice();
    const target = invoice?.candidates[index];
    if (invoice && target) this.write(invoice, 'linked', this.einvoices.link(invoice.id, targetBody(target)));
  }

  dismiss(): void {
    const invoice = this.invoice();
    if (invoice) this.write(invoice, 'dismissed', this.einvoices.dismiss(invoice.id));
  }

  pickOther(): void {
    this.picking.set(this.invoice());
  }

  picked(invoice: InboxEinvoice, target: EinvoiceLinkTarget): void {
    this.picking.set(null);
    this.write(invoice, 'linked', this.einvoices.link(invoice.id, target));
  }

  /** 記成新的一筆: the entry form prefilled; the flow moves on when the form saved (the invoice links later). */
  record(): void {
    const invoice = this.invoice();
    if (!invoice || this.busy()) return;
    this.session.awaitRecord(invoice.id, invoice.number, this.accounting.invoiceCreateMark());
    void this.router.navigate(ENTRY_FORM, { queryParams: invoiceEntryParams(invoice) });
  }

  skipStep(): boolean {
    const s = this.state();
    if (!s || s.cursor === null || this.busy()) return false;
    this.state.set(skip(s));
    this.focusStep();
    return true;
  }

  moveStep(by: -1 | 1): boolean {
    const s = this.state();
    if (!s || this.busy()) return false;
    const next = move(s, by);
    if (next === s) return false;
    this.state.set(next);
    this.focusStep();
    return true;
  }

  resumeFlow(): void {
    const s = this.state();
    if (s) {
      this.state.set(resume(s));
      this.focusStep();
    }
  }

  onKey(event: KeyboardEvent): void {
    if (isHandledKey(event) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (this.busy() || this.picking()) return;
    if (this.document.querySelector(OPEN_DIALOG) || typing(this.document.activeElement)) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closed.emit();
      return;
    }
    if (this.load() !== 'ready' || !this.invoice()) return;
    if (/^[1-9]$/.test(event.key)) {
      if (event.repeat) return; // a held key must not answer the next invoice once busy clears
      const digit = Number(event.key);
      const keys = this.keys();
      const index = keys.candidates.indexOf(digit);
      if (index >= 0) {
        event.preventDefault();
        this.link(index);
      } else if (keys.dismiss === digit) {
        event.preventDefault();
        this.dismiss();
      }
      return;
    }
    if ((event.key === 's' || event.key === 'S') && !event.repeat) {
      if (this.skipStep()) event.preventDefault();
      return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      if (this.moveStep(event.key === 'ArrowLeft' ? -1 : 1)) event.preventDefault();
    }
  }

  private write(invoice: InboxEinvoice, outcome: EinvoiceOutcome, call: ReturnType<EinvoiceService['link']>): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set(null);
    call.subscribe({
      next: () => {
        this.busy.set(false);
        const s = this.state();
        if (s) this.state.set(answer(s, invoice.id, outcome));
        const what = `${invoice.seller_name} ${invoiceAmount(invoice.total_amount)}`;
        this.toasts.done(outcome === 'linked' ? `已連結：${what}` : `已標成都不是：${what}`, {
          undo: () => this.undo(invoice, outcome),
        });
        this.focusStep();
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.error.set({ id: invoice.id, text: einvoiceErrorText(err) });
      },
    });
  }

  /**
   * 復原 a link or 都不是: unlink + undismiss, or undismiss. The API leaves the invoice `unmatched` until the next
   * matcher run; the flow shows it again at once with its candidates, so it can be answered again.
   */
  private undo(invoice: InboxEinvoice, outcome: EinvoiceOutcome): void {
    // A link's 復原 is two writes; when only the unlink went through, the invoice is left as 都不是 and the error says so.
    let unlinked = false;
    const call =
      outcome === 'linked'
        ? this.einvoices.unlink(invoice.id).pipe(
            switchMap(() => {
              unlinked = true;
              return this.einvoices.undismiss(invoice.id);
            }),
          )
        : this.einvoices.undismiss(invoice.id);
    this.busy.set(true);
    call.subscribe({
      next: () => {
        this.busy.set(false);
        const s = this.state();
        if (s) this.state.set(restore(s, invoice));
        this.toasts.done(`已復原：${invoice.seller_name} ${invoiceAmount(invoice.total_amount)}`);
        this.focusStep();
      },
      error: (err: unknown) => {
        this.busy.set(false);
        const why = einvoiceErrorText(err).replace(/^沒成功[：，]/, '');
        this.toasts.error(unlinked ? `已取消連結，但這張留在「都不是」：${why}` : `復原沒成功：${why}`);
      },
    });
  }

  /** The new invoice's headline (or the end state's heading), so a screen reader reads it. */
  private focusStep(): void {
    afterNextRender(
      () => this.host.nativeElement.querySelector<HTMLElement>('h2[tabindex="-1"]')?.focus(),
      { injector: this.injector },
    );
  }
}
