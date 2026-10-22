import { NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  OnInit,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { focusSheetField, isHandledKey, restoreOverlayFocus, trapFocus } from '../../accounting-ui';
import { slashDate } from '../../dates';
import { formatMoney } from '../../format';
import { InboxCard, inboxErrorMessage } from '../inbox-view';
import { InboxAccount, InboxCandidate } from '../inbox.model';
import { InboxService } from '../inbox.service';

/** One entry per case, keyed by the case id as a string (the `/inbox/apply` `entry_ids` shape), with its title. */
export interface InboxMatchResult {
  entry_ids: Record<string, number>;
  titles: Record<string, string>;
}

export interface InboxMatchRow {
  candidate: InboxCandidate;
  label: string;
}

/** A search answered for one case: its rows not already listed, or the failure text (the button stays to retry). */
type SearchState = { rows: InboxCandidate[]; error: null } | { rows: []; error: string };

/**
 * 對到… (spec §7.2): the case's candidates as radio rows, 搜尋這個帳戶的記錄 for more (the server keeps them unclaimed,
 * ±14 days, same sign), one entry per member of a merged card (第 k/n 筆). The panel turns `confirmed` into the apply.
 */
@Component({
  selector: 'app-inbox-match-sheet',
  standalone: true,
  imports: [NgTemplateOutlet],
  templateUrl: './match-sheet.html',
  styleUrls: ['../../sheet.scss', './match-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class AppInboxMatchSheetComponent implements OnInit {
  private readonly inbox = inject(InboxService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private opener: HTMLElement | null = null;

  readonly card = input.required<InboxCard>();
  readonly account = input.required<InboxAccount>();
  readonly confirmed = output<InboxMatchResult>();
  readonly closed = output<void>();

  /** The member being answered (0-based). */
  readonly step = signal(0);
  /** The picked entry per case id. */
  private readonly picks = signal<ReadonlyMap<number, InboxCandidate>>(new Map());
  private readonly searches = signal<ReadonlyMap<number, SearchState>>(new Map());
  readonly searching = signal(false);

  readonly count = computed(() => this.card().items.length);
  readonly item = computed(() => this.card().items[this.step()]);
  readonly isLast = computed(() => this.step() === this.count() - 1);
  readonly stepText = computed(() =>
    this.count() > 1 ? `第 ${this.step() + 1}/${this.count()} 筆` : null,
  );
  /** The bank line (or ledger entry) this step matches. */
  readonly lineText = computed(() => {
    const item = this.item();
    const currency = this.account().currency;
    if (item.line) {
      return `${slashDate(item.line.date)} ${item.line.merchant} ${formatMoney(item.line.amount, currency, { sign: true })}`;
    }
    return item.entry
      ? `${slashDate(item.entry.date)} ${item.entry.title} ${formatMoney(item.entry.amount, currency, { sign: true })}`
      : `案件 #${item.case_id}`;
  });
  readonly candidates = computed(() =>
    this.item().candidates.map((candidate) => this.row(candidate)),
  );
  readonly search = computed(() => this.searches().get(this.item().case_id) ?? null);
  readonly others = computed(() =>
    (this.search()?.rows ?? []).map((candidate) => this.row(candidate)),
  );
  readonly picked = computed(() => this.picks().get(this.item().case_id)?.entry_id ?? null);
  readonly radioName = computed(() => `inbox-match-${this.item().case_id}`);

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    focusSheetField(this.host.nativeElement, '.sheet', this.injector);
  }

  pick(candidate: InboxCandidate): void {
    const caseId = this.item().case_id;
    this.picks.update((map) => new Map(map).set(caseId, candidate));
  }

  /** 搜尋這個帳戶的記錄 for the current member; rows already listed as its candidates are left out. */
  runSearch(): void {
    if (this.searching()) {
      return;
    }
    const item = this.item();
    const listed = new Set(item.candidates.map((candidate) => candidate.entry_id));
    this.searching.set(true);
    this.inbox
      .search(item.case_id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (rows) => {
          this.searching.set(false);
          this.setSearch(item.case_id, {
            rows: rows.filter((row) => !listed.has(row.entry_id)),
            error: null,
          });
        },
        error: (err: unknown) => {
          this.searching.set(false);
          this.setSearch(item.case_id, { rows: [], error: inboxErrorMessage(err) });
        },
      });
  }

  next(): void {
    if (this.picked() !== null && !this.isLast()) {
      this.step.update((step) => step + 1);
    }
  }

  back(): void {
    this.step.update((step) => Math.max(0, step - 1));
  }

  confirm(): void {
    const picks = this.picks();
    const items = this.card().items;
    if (!items.every((item) => picks.has(item.case_id))) {
      return;
    }
    const result: InboxMatchResult = { entry_ids: {}, titles: {} };
    for (const item of items) {
      const candidate = picks.get(item.case_id) as InboxCandidate;
      result.entry_ids[String(item.case_id)] = candidate.entry_id;
      result.titles[String(item.case_id)] = candidate.title;
    }
    this.confirmed.emit(result);
  }

  cancel(): void {
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  /** Overlay contract: Esc closes only this sheet; Tab stays inside. */
  onHostKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.cancel();
      return;
    }
    const sheet = this.host.nativeElement.querySelector<HTMLElement>('.sheet');
    if (event.key === 'Tab' && sheet) {
      trapFocus(sheet, event);
    }
  }

  private setSearch(caseId: number, state: SearchState): void {
    this.searches.update((map) => new Map(map).set(caseId, state));
  }

  private row(candidate: InboxCandidate): InboxMatchRow {
    return {
      candidate,
      label: `${slashDate(candidate.date)} ${candidate.title} ${formatMoney(candidate.amount, this.account().currency)}`,
    };
  }
}
