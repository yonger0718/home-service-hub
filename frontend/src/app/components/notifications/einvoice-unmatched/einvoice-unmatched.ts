import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  effect,
  inject,
  output,
  signal,
  untracked,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';
import { Observable, concatMap, defer, from, last, switchMap, tap } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { injectToasts } from '../../../services/toast.service';
import { isHandledKey } from '../../accounting/accounting-ui';
import { shortDate } from '../../accounting/dates';
import { invoiceAmount } from '../../accounting/inbox/einvoice-format';
import { EinvoiceLinkTarget, InboxEinvoice } from '../../accounting/inbox/einvoice.model';
import { EinvoiceService } from '../../accounting/inbox/einvoice.service';
import { einvoiceErrorText } from '../../accounting/inbox/einvoice-section/einvoice-section';
import { AppEinvoiceTargetSheetComponent } from '../../accounting/inbox/einvoice-target-sheet/einvoice-target-sheet';
import { InboxService } from '../../accounting/inbox/inbox.service';
import { BannerComponent } from '../../ui/banner/banner';
import { ENTRY_FORM, invoiceEntryParams } from '../einvoice-flow/einvoice-flow-view';
import { OPEN_DIALOG, typing } from '../flow-keys';
import { EINVOICE_FLOW_ROUTE, INBOX_READ_LIMIT } from '../sources/recon';
import { UnmatchedGroup, unmatchedGroups, unmatchedHeadline } from './einvoice-unmatched-view';

/** What the page shows below its header. */
export type UnmatchedView = 'list' | 'blocked' | 'pending' | 'off' | 'empty';

/** The error text without its 「沒成功：」 lead, for a sentence that already says what failed. */
function why(err: unknown): string {
  return einvoiceErrorText(err).replace(/^沒成功[：，]/, '');
}

/**
 * 有發票沒有帳 (D11) at `/notifications/einvoices?state=unmatched`, in the flow slot: beside the list from 1024 px,
 * full screen below. The unmatched invoices grouped by seller; each row is recorded (the entry form, which links the
 * invoice at save), linked (選帳…) or dismissed (不用記帳); a group is dismissed at once or its seller muted. Every
 * write toasts with 復原. Never counted.
 */
@Component({
  selector: 'app-einvoice-unmatched',
  standalone: true,
  imports: [AppEinvoiceTargetSheetComponent, BannerComponent, RouterLink],
  templateUrl: './einvoice-unmatched.html',
  styleUrls: ['../recon-flow/recon-flow.scss', './einvoice-unmatched.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'region',
    'aria-label': '有發票沒有帳',
    tabindex: '-1',
    '[class.flow--panes]': 'panes()',
    '(document:keydown)': 'onKey($event)',
  },
})
export class EinvoiceUnmatchedComponent {
  private readonly inbox = inject(InboxService);
  private readonly einvoices = inject(EinvoiceService);
  private readonly accounting = inject(AccountingService);
  private readonly layout = inject(LayoutModeService);
  private readonly router = inject(Router);
  private readonly toasts = injectToasts();
  private readonly destroyRef = inject(DestroyRef);
  private readonly document = inject(DOCUMENT);
  /** The newest read: an older read's answer (an earlier write's refresh landing late) is dropped. */
  private readSeq = 0;

  readonly closed = output<void>();

  readonly flowRoute = EINVOICE_FLOW_ROUTE;
  /** The 記帳設定 › 發票 footer: hidden until that section exists (owner, 2026-11-28); F4 turns it on. */
  readonly settingsLink = false;
  readonly panes = computed(() => this.layout.mode() === 'panes');
  readonly load = signal<'loading' | 'failed' | 'ready'>('loading');
  /** From a press until its write (or 復原) answers: every action waits. */
  readonly busy = signal(false);
  /** The failed write's text, shown under the group it was for. */
  readonly error = signal<{ key: string; text: string } | null>(null);
  /** 選帳… is open for this invoice (of the group with this key). */
  readonly picking = signal<{ invoice: InboxEinvoice; key: string } | null>(null);

  /** The unmatched invoices of the last read (plus any 復原 brought back before the next read lists them). */
  private readonly invoices = signal<readonly InboxEinvoice[]>([]);
  /** Answered here: hidden at once, before the refresh lands. */
  private readonly hidden = signal<ReadonlySet<number>>(new Set());
  /** `counts.einvoice_unmatched` (the whole set) and `counts.einvoice_ask` of the last read. */
  private readonly count = signal(0);
  readonly asking = signal(0);
  /** `remind_unmatched`, or null before (or without) the preferences read. */
  private readonly remindOn = signal<boolean | null>(null);

  readonly groups = computed(() => {
    const hidden = this.hidden();
    return unmatchedGroups(this.invoices().filter((invoice) => !hidden.has(invoice.id)));
  });
  readonly headline = computed(() => unmatchedHeadline(this.groups()));
  /** Unmatched invoices the read did not list (the page is shared with the questions, which come first). */
  readonly missing = computed(() => Math.max(0, this.count() - this.invoices().length));
  /**
   * pending: every listed invoice is answered (hidden) but the last count says more exist beyond the page; the
   * re-read the answer triggered lists them. Never 都整理好了 while the count is above what this page answered.
   */
  readonly view = computed<UnmatchedView>(() => {
    if (this.groups().length > 0) return 'list';
    if (this.count() > 0 && this.invoices().length === 0) return 'blocked';
    if (this.count() - this.invoices().length > 0) return 'pending';
    if (this.remindOn() === false) return 'off';
    return 'empty';
  });
  readonly errorFor = (key: string) => {
    const e = this.error();
    return e && e.key === key ? e.text : null;
  };

  constructor() {
    this.einvoices
      .preferences()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({ next: (p) => this.remindOn.set(p.remind_unmatched), error: () => undefined });
    // Every entries write (this page's, the entry form's, another page's) re-reads: the list follows the server.
    effect(() => {
      this.accounting.entriesChanged();
      untracked(() => this.read());
    });
  }

  retryLoad(): void {
    this.read();
  }

  private read(): void {
    if (this.load() !== 'ready') this.load.set('loading');
    const seq = ++this.readSeq;
    this.inbox
      .get({ limit: INBOX_READ_LIMIT })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (out) => {
          if (seq !== this.readSeq) return;
          const listed = (out.einvoices ?? []).filter((invoice) => invoice.state === 'unmatched');
          const ids = new Set(listed.map((invoice) => invoice.id));
          this.invoices.set(listed);
          // An answer stays hidden while the server still lists it (a refresh that left before the write landed).
          this.hidden.update((set) => new Set([...set].filter((id) => ids.has(id))));
          this.count.set(out.counts.einvoice_unmatched ?? 0);
          this.asking.set(out.counts.einvoice_ask ?? 0);
          this.load.set('ready');
        },
        error: () => {
          if (seq !== this.readSeq) return;
          // A failed refresh keeps a usable list; with nothing left to show it offers 重試 instead.
          if (this.load() !== 'ready' || this.view() === 'pending') this.load.set('failed');
        },
      });
  }

  /** 記一筆: the entry form prefilled; the save links the invoice and the re-read drops it. */
  record(invoice: InboxEinvoice): void {
    if (this.busy()) return;
    void this.router.navigate(ENTRY_FORM, { queryParams: invoiceEntryParams(invoice) });
  }

  pick(invoice: InboxEinvoice, group: UnmatchedGroup): void {
    if (!this.busy()) this.picking.set({ invoice, key: group.key });
  }

  picked(invoice: InboxEinvoice, target: EinvoiceLinkTarget, key: string): void {
    this.picking.set(null);
    this.write(key, [invoice], this.einvoices.link(invoice.id, target), {
      text: `已連結：${invoice.seller_name} ${invoiceAmount(invoice.total_amount)}`,
      undo: () => this.undoLink(invoice),
    });
  }

  dismiss(invoice: InboxEinvoice, group: UnmatchedGroup): void {
    this.write(group.key, [invoice], this.einvoices.dismiss(invoice.id), {
      text: `已把 ${invoice.seller_name} ${shortDate(invoice.issue_date)} 標成不用記帳`,
      undo: () => this.undoDismiss([invoice]),
    });
  }

  /** 這 N 張都不用記帳: one dismiss after another; a failure stops there and keeps (and offers to undo) the done ones. */
  dismissAll(group: UnmatchedGroup): void {
    if (this.busy()) return;
    const list = group.rows.map((row) => row.invoice);
    const done: InboxEinvoice[] = [];
    this.start();
    from(list)
      .pipe(concatMap((invoice) => this.einvoices.dismiss(invoice.id).pipe(tap(() => done.push(invoice)))), last())
      .subscribe({
        next: () => {
          this.finish(list);
          this.toasts.done(`已把 ${group.seller} 的 ${list.length} 張標成不用記帳`, {
            undo: () => this.undoDismiss(list),
          });
        },
        error: (err: unknown) => {
          this.finish(done);
          if (done.length === 0) {
            this.error.set({ key: group.key, text: einvoiceErrorText(err) });
            return;
          }
          this.toasts.done(`標了 ${done.length} 張，第 ${done.length + 1} 張沒成功：${why(err)}`, {
            undo: () => this.undoDismiss([...done]),
          });
        },
      });
  }

  /**
   * 這家以後都不提醒: dismiss the newest invoice with `mute_seller`; the server dismisses the seller's other unmatched
   * invoices (seller_muted), so the whole group leaves.
   */
  mute(group: UnmatchedGroup): void {
    const invoice = group.rows[0]?.invoice;
    if (!invoice || !group.taxId) return;
    const list = group.rows.map((row) => row.invoice);
    this.write(group.key, list, this.einvoices.dismiss(invoice.id, true), {
      text: `以後不再提醒 ${group.seller}（${list.length} 張一起收起）`,
      undo: () => this.undoMute(group.taxId as string, invoice, list),
    });
  }

  onKey(event: KeyboardEvent): void {
    if (isHandledKey(event) || event.ctrlKey || event.metaKey || event.altKey) return;
    if (this.busy() || this.picking()) return;
    if (this.document.querySelector(OPEN_DIALOG) || typing(this.document.activeElement)) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closed.emit();
    }
  }


  private start(): void {
    this.busy.set(true);
    this.error.set(null);
  }

  /** The write answered: these invoices leave now (the refresh its bump started confirms it). */
  private finish(gone: readonly InboxEinvoice[]): void {
    this.busy.set(false);
    this.hidden.update((set) => new Set([...set, ...gone.map((invoice) => invoice.id)]));
  }

  private write(
    key: string,
    gone: readonly InboxEinvoice[],
    call: Observable<unknown>,
    toast: { text: string; undo: () => void },
  ): void {
    if (this.busy()) return;
    this.start();
    call.subscribe({
      next: () => {
        this.finish(gone);
        this.toasts.done(toast.text, { undo: toast.undo });
      },
      error: (err: unknown) => {
        this.busy.set(false);
        this.error.set({ key, text: einvoiceErrorText(err) });
      },
    });
  }

  /** 復原 brought these back: shown at once, before the refresh lists them again. */
  private restore(list: readonly InboxEinvoice[]): void {
    const ids = new Set(list.map((invoice) => invoice.id));
    this.hidden.update((set) => new Set([...set].filter((id) => !ids.has(id))));
    this.invoices.update((current) => [...current.filter((invoice) => !ids.has(invoice.id)), ...list]);
  }

  private runUndo(call: Observable<unknown>, list: readonly InboxEinvoice[], failText: (err: unknown) => string): void {
    this.busy.set(true);
    call.subscribe({
      next: () => {
        this.busy.set(false);
        this.restore(list);
        const one = list.length === 1 ? `${list[0].seller_name} ${invoiceAmount(list[0].total_amount)}` : null;
        this.toasts.done(one ? `已復原：${one}` : `已復原：${list[0].seller_name} ${list.length} 張`);
      },
      error: (err: unknown) => {
        this.busy.set(false);
        // Part of it may have gone through (the mute deleted, some undismissed): stop hiding them and let a fresh read
        // show what the server lists. Ones still 不用記帳 are not listed, so they stay out.
        const ids = new Set(list.map((invoice) => invoice.id));
        this.hidden.update((set) => new Set([...set].filter((id) => !ids.has(id))));
        this.read();
        this.toasts.error(failText(err));
      },
    });
  }

  private undoDismiss(list: readonly InboxEinvoice[]): void {
    let undone = 0;
    const call = from(list).pipe(
      concatMap((invoice) => this.einvoices.undismiss(invoice.id).pipe(tap(() => undone++))),
      last(),
    );
    this.runUndo(call, list, (err) =>
      undone === 0 ? `復原沒成功：${why(err)}` : `復原了 ${undone} 張，其餘沒成功：${why(err)}`,
    );
  }

  /** A link's 復原: unlink, then undismiss (back to unmatched); halfway leaves it 不用記帳 and says so. */
  private undoLink(invoice: InboxEinvoice): void {
    let unlinked = false;
    const call = this.einvoices.unlink(invoice.id).pipe(
      switchMap(() => {
        unlinked = true;
        return this.einvoices.undismiss(invoice.id);
      }),
    );
    this.runUndo(call, [invoice], (err) =>
      unlinked ? `已取消連結，但這張留在「不用記帳」：${why(err)}` : `復原沒成功：${why(err)}`,
    );
  }

  /**
   * A mute's 復原: remove the mute first (the seller's seller_muted invoices come back), then undismiss the one
   * dismissed with it (undismiss refuses while the mute exists). Halfway: the reminders are back but that invoice stays
   * 不用記帳.
   */
  private undoMute(taxId: string, invoice: InboxEinvoice, list: readonly InboxEinvoice[]): void {
    let unmuted = false;
    const call = defer(() => this.einvoices.deleteMute(taxId)).pipe(
      switchMap(() => {
        unmuted = true;
        return this.einvoices.undismiss(invoice.id);
      }),
    );
    this.runUndo(call, list, (err) =>
      unmuted
        ? `已恢復提醒，但 ${shortDate(invoice.issue_date)} 這張留在「不用記帳」：${why(err)}`
        : `復原沒成功：${why(err)}`,
    );
  }
}
