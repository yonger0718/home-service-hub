import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
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
  input,
  linkedSignal,
  output,
  signal,
  untracked,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { NotificationCenterService } from '../../../services/notification-center.service';
import { injectToasts } from '../../../services/toast.service';
import { BannerComponent } from '../../ui/banner/banner';
import { isHandledKey } from '../../accounting/accounting-ui';
import { formatMoney } from '../../accounting/format';
import {
  InboxCard,
  buttonLabel,
  inboxLearn,
  learnLabel,
  learnableAnswer,
  lineKind,
  matchAnswer,
  recordAnswer,
} from '../../accounting/inbox/inbox-view';
import {
  InboxAccount,
  InboxApplyBody,
  InboxApplyOut,
  InboxButton,
  InboxItem,
  InboxOut,
} from '../../accounting/inbox/inbox.model';
import { InboxService } from '../../accounting/inbox/inbox.service';
import {
  AppInboxMatchSheetComponent,
  InboxMatchResult,
} from '../../accounting/inbox/match-sheet/match-sheet';
import {
  AppInboxRecordSheetComponent,
  InboxRecordResult,
} from '../../accounting/inbox/record-sheet/record-sheet';
import { INBOX_READ_LIMIT } from '../sources/recon';
import {
  FlowState,
  advance,
  afterRead,
  askItems,
  followers,
  goTo,
  move,
  progress,
  resume,
  skip,
  SavedAnswer,
  itemKey,
  restore,
  startFlow,
} from './recon-flow-state';
import {
  FlowAnswer,
  FlowError,
  compareRows,
  flowActions,
  flowAnswerLabel,
  flowCard,
  flowChip,
  flowEntry,
  flowError,
  flowExcluded,
  flowHeadline,
  flowLearnScope,
  flowLearnable,
  flowNote,
  flowPartOf,
  isCaseAction,
} from './recon-flow-view';
import { ReconReverted, ReconUndoService } from './recon-undo.service';

/** Above this many cases the bar is one plain fill instead of one segment per case. */
const MAX_SEGMENTS = 20;

/** ARIA text widgets whose letters are input (as the shell's global keys). */
const TYPING_ROLES = '[role="combobox"], [role="listbox"], [role="spinbutton"]';
const TYPING_TAGS: ReadonlySet<string> = new Set(['INPUT', 'TEXTAREA', 'SELECT']);
const OPEN_DIALOG = '[role="dialog"], [aria-modal="true"]';
const EDITABLE = '[contenteditable]:not([contenteditable="false"])';

function typing(node: Element | null): boolean {
  if (!(node instanceof HTMLElement)) {
    return false;
  }
  return (
    TYPING_TAGS.has(node.tagName) ||
    node.isContentEditable ||
    !!node.closest(EDITABLE) ||
    !!node.closest(TYPING_ROLES)
  );
}

/** `2026-09-30` → `9/30`. */
function monthDay(iso: string): string {
  return `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
}

type OneTap = Exclude<InboxButton, 'record' | 'match_candidate' | 'apply_proposal' | 'reject_proposal'>;

/** An answer the server took (or refused) whose re-read has not landed yet. */
interface PendingAnswer {
  readonly caseId: number;
  readonly out: InboxApplyOut;
}

type SegmentState = 'done' | 'current' | 'rest';

/**
 * 對帳 one question at a time for one statement (spec D5): reads the inbox, shows the statement's open ask items as
 * steps, applies one answer per press through `/inbox/apply` (the inbox panel's body shape, one case), re-reads with
 * the account id and moves on. 記為… / 對到… open the panel's record / match sheets.
 */
@Component({
  selector: 'app-recon-flow',
  standalone: true,
  imports: [
    NgTemplateOutlet,
    RouterLink,
    BannerComponent,
    AppInboxRecordSheetComponent,
    AppInboxMatchSheetComponent,
  ],
  templateUrl: './recon-flow.html',
  styleUrl: './recon-flow.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'region',
    'aria-label': '對帳',
    tabindex: '-1',
    '[class.flow--panes]': 'panes()',
    '(document:keydown)': 'onKey($event)',
  },
})
export class ReconFlowComponent {
  private readonly inbox = inject(InboxService);
  private readonly accounting = inject(AccountingService);
  private readonly center = inject(NotificationCenterService);
  private readonly undo = inject(ReconUndoService);
  private readonly layout = inject(LayoutModeService);
  private readonly toasts = injectToasts();
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);

  readonly statementId = input.required<number>();
  readonly closed = output<void>();

  readonly panes = computed(() => this.layout.mode() === 'panes');

  readonly load = signal<'loading' | 'failed' | 'ready'>('loading');
  readonly account = signal<InboxAccount | null>(null);
  private readonly periodEnd = signal<string | null>(null);
  readonly flow = signal<FlowState>(startFlow([]));
  /** From a press until its re-read settles (or the apply fails). */
  readonly busy = signal(false);
  /**
   * A read after the first one failed: `answer` (the answer went through), `undo` (a 復原 went through; the question
   * to restore is kept) or `reload` (重新讀取 on a changed question). Only 重試 (a read) is offered.
   */
  readonly rereadFailed = signal<'answer' | 'undo' | 'reload' | null>(null);
  readonly rereadText = computed(() => {
    switch (this.rereadFailed()) {
      case 'undo':
        return '已復原，但清單讀取失敗';
      case 'reload':
        return '清單讀取失敗';
      default:
        return '答案已送出，但清單讀取失敗';
    }
  });
  readonly locked = computed(() => this.busy() || this.rereadFailed());

  readonly recordCard = signal<InboxCard | null>(null);
  readonly matchCard = signal<InboxCard | null>(null);
  /** The entry the open 對到… sheet must not offer (the one the question already compares). */
  readonly matchExclude = computed<readonly number[]>(() => {
    const excluded = this.matchCard() ? flowExcluded(this.matchCard()!.items[0]) : null;
    return excluded === null ? [] : [excluded];
  });

  private readonly cursor = computed(() => this.flow().cursor);
  /** Off on every step change. */
  readonly learnOn = linkedSignal<number | null, boolean>({ source: this.cursor, computation: () => false });
  /** The refusal under the answers; cleared on every step change. */
  readonly inlineError = linkedSignal<number | null, FlowError | null>({ source: this.cursor, computation: () => null });

  private gen = 0;
  private reading: Subscription | null = null;
  private pending: PendingAnswer | null = null;
  /** Per applied answer (its case id at answer time): what 復原 must find again after the reconcile. */
  private readonly saved = new Map<number, SavedAnswer>();
  /** A 復原 of this statement finished: the next read restores that answer's question. */
  private pendingRevert: number | null = null;
  private destroyed = false;

  readonly item = computed<InboxItem | null>(() => {
    const cursor = this.cursor();
    return cursor === null ? null : (this.flow().open.get(cursor) ?? null);
  });
  private readonly currency = computed(() => this.account()?.currency ?? 'TWD');
  readonly progressInfo = computed(() => progress(this.flow()));

  /**
   * 「對帳 · 帳戶」. Full screen the heading already says 對帳 until the statement is known (loading, a failed read, a
   * statement the inbox does not list), so there is no kicker then; the pane has no heading in those states and keeps it.
   */
  readonly kicker = computed(() => {
    const account = this.account();
    if (account) {
      return `對帳 · ${account.name}`;
    }
    return this.panes() ? '對帳' : null;
  });
  readonly titleText = computed(() => {
    const end = this.periodEnd();
    return end ? `至 ${monthDay(end)} 帳單` : null;
  });
  /**
   * Below panes width the flow is the whole page and its title is the page's h1: the statement once it is known, else
   * 「對帳」 (while loading, after a failed read, for a statement the inbox does not list).
   */
  readonly heading = computed(() => (this.load() === 'ready' ? this.titleText() : null) ?? '對帳');
  /** 「已對上 k / n 行 · 這次要你決定 N 筆」; the second half only while something is left to decide. */
  readonly subText = computed(() => {
    const task = this.center.tasks().find((t) => t.id === `recon:${this.statementId()}`);
    const { total, remaining } = this.progressInfo();
    const parts = [
      task?.progress ? `已對上 ${task.progress.k} / ${task.progress.n} 行` : null,
      remaining > 0 ? `這次要你決定 ${total} 筆` : null,
    ].filter((part): part is string => part !== null);
    return parts.length ? parts.join(' · ') : null;
  });
  readonly statementLink = computed(() => {
    const account = this.account();
    return account ? `/accounting/accounts/${account.id}/statements/${this.statementId()}` : null;
  });

  /** One state per case up to MAX_SEGMENTS; null above it (a plain bar). */
  readonly segments = computed<SegmentState[] | null>(() => {
    const state = this.flow();
    if (state.order.length > MAX_SEGMENTS) {
      return null;
    }
    return state.order.map((id) => (!state.open.has(id) ? 'done' : id === state.cursor ? 'current' : 'rest'));
  });
  readonly barColumns = computed(() => `repeat(${this.flow().order.length}, 1fr)`);
  readonly handledPercent = computed(() => {
    const { handled, total } = this.progressInfo();
    return total === 0 ? 0 : (handled / total) * 100;
  });
  readonly progressText = computed(() => {
    const { position, total, handled } = this.progressInfo();
    if (this.cursor() === null) {
      return `${handled} / ${total} 筆`;
    }
    return this.panes() ? `第 ${position} / ${total} 筆` : `${position} / ${total}`;
  });

  readonly step = computed(() => {
    const item = this.item();
    if (!item) {
      return null;
    }
    const currency = this.currency();
    const hint = item.rule_hint;
    const answers = flowActions(item).map((action) => ({
      action,
      label: flowAnswerLabel(action, item, currency),
      learns: hint !== null && !isCaseAction(action) && learnableAnswer(item.question, action),
    }));
    const partOf = flowPartOf(item) ? (item.part_of ?? [])[0] : undefined;
    return {
      item,
      chip: flowChip(item.question),
      headline: flowHeadline(item, currency),
      rows: compareRows(item, currency),
      pair: this.pairNote(item),
      note: flowNote(item, currency),
      /** 打開那筆: the recorded entry the note says this line may be part of. */
      noteLink: partOf ? `/accounting/entries/${partOf.id}` : null,
      highlight: !flowPartOf(item),
      answers,
      keys: this.keyHint(Math.min(answers.length, 3)),
      learn:
        hint && flowLearnable(item)
          ? {
              label: learnLabel(hint, item.actions.find((a) => learnableAnswer(item.question, a)) ?? 'dismiss'),
              scope: flowLearnScope(item, currency),
            }
          : null,
    };
  });

  readonly endTitle = computed(() => {
    const { handled, remaining, total } = this.progressInfo();
    if (total === 0) {
      return '這張帳單沒有要你決定的了';
    }
    return remaining > 0 ? `已處理 ${handled} 筆，還有 ${remaining} 筆` : '這張帳單都決定好了';
  });

  constructor() {
    effect(() => {
      this.statementId();
      untracked(() => this.restart());
    });

    let lastSeq = untracked(this.undo.reverted)?.seq ?? 0;
    effect(() => {
      const reverted = this.undo.reverted();
      if (!reverted || reverted.seq <= lastSeq) {
        return;
      }
      lastSeq = reverted.seq;
      untracked(() => this.onReverted(reverted));
    });

    inject(DestroyRef).onDestroy(() => {
      this.destroyed = true;
      this.reading?.unsubscribe();
    });
  }

  // ----- owner actions -------------------------------------------------------------------------------------------

  press(answer: FlowAnswer): void {
    const item = this.item();
    const account = this.account();
    if (!item || !account || this.locked() || isCaseAction(answer)) {
      return;
    }
    const action: InboxButton = answer;
    switch (action) {
      case 'record':
        this.recordCard.set(flowCard(item, account));
        return;
      case 'match_candidate':
        this.matchCard.set(flowCard(item, account));
        return;
      case 'apply_proposal':
      case 'reject_proposal':
        return;
    }
    this.applyOneTap(item, action);
  }

  record(card: InboxCard, result: InboxRecordResult): void {
    this.recordCard.set(null);
    const item = card.items[0];
    const params: Record<string, unknown> = { category_id: result.category_id };
    if (result.name !== null) {
      params['name'] = result.name;
    }
    if (result.project_id !== null) {
      params['project_id'] = result.project_id;
    }
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action: 'record',
      params,
      answer: recordAnswer(lineKind(item), result.category_name),
      learn: result.learn,
    });
  }

  match(card: InboxCard, result: InboxMatchResult): void {
    this.matchCard.set(null);
    const item = card.items[0];
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action: 'match_candidate',
      params: { entry_ids: result.entry_ids },
      answer: matchAnswer(result.titles[String(item.case_id)]),
      learn: null,
    });
  }

  toggleLearn(): void {
    if (!this.locked()) {
      this.learnOn.update((on) => !on);
    }
  }

  skipStep(): boolean {
    return !this.locked() && this.owned((state) => skip(state));
  }

  moveStep(delta: -1 | 1): boolean {
    return !this.locked() && this.owned((state) => move(state, delta));
  }

  resumeFlow(): void {
    if (!this.locked()) {
      this.owned((state) => resume(state));
    }
  }

  /** 重新讀取 on a question that changed: a read only; the step shows what the question is now. */
  reloadStep(): void {
    if (this.locked()) {
      return;
    }
    this.inlineError.set(null);
    this.busy.set(true);
    this.read(false);
  }

  /** The record sheet's 以後…都這樣 is this step's switch: a tick there moves it. */
  setLearn(on: boolean): void {
    this.learnOn.set(on);
  }

  retryLoad(): void {
    this.restart();
  }

  /** 重試 after a failed re-read: a read only, never the answer again. */
  retryRead(): void {
    if (this.busy()) {
      return;
    }
    this.rereadFailed.set(null);
    this.busy.set(true);
    this.read(false);
  }

  onKey(event: KeyboardEvent): void {
    if (isHandledKey(event) || event.ctrlKey || event.metaKey || event.altKey) {
      return;
    }
    if (this.busy() || this.recordCard() || this.matchCard()) {
      return;
    }
    if (this.document.querySelector(OPEN_DIALOG) || typing(this.document.activeElement)) {
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      this.closed.emit();
      return;
    }
    if (this.load() !== 'ready') {
      return;
    }
    // Only keys that act are consumed: nothing while locked (a failed re-read), no step, or no such answer.
    const step = this.step();
    if (!step || this.locked()) {
      return;
    }
    switch (event.key) {
      case '1':
      case '2':
      case '3': {
        const answer = step.answers[Number(event.key) - 1];
        if (!answer || event.repeat) {
          return; // a held key must not answer the next question once busy clears
        }
        event.preventDefault();
        this.press(answer.action);
        return;
      }
      case 's':
      case 'S':
        if (!event.repeat && this.skipStep()) {
          event.preventDefault();
        }
        return;
      case 'ArrowLeft':
      case 'ArrowRight':
        if (this.moveStep(event.key === 'ArrowLeft' ? -1 : 1)) {
          event.preventDefault();
        }
        return;
    }
  }

  // ----- reading ---------------------------------------------------------------------------------------------------

  private restart(): void {
    this.gen += 1;
    this.pending = null;
    this.pendingRevert = null;
    this.saved.clear();
    this.account.set(null);
    this.periodEnd.set(null);
    this.flow.set(startFlow([]));
    this.busy.set(false);
    this.rereadFailed.set(null);
    this.recordCard.set(null);
    this.matchCard.set(null);
    this.load.set('loading');
    this.read(true);
  }

  /** One read in flight: a newer read cancels the older one (its answer would be stale). */
  private read(initial: boolean): void {
    this.reading?.unsubscribe();
    const statementId = this.statementId();
    const accountId = this.account()?.id;
    const source =
      initial || accountId === undefined
        ? this.inbox.get({ limit: INBOX_READ_LIMIT })
        : this.inbox.get({ accountId, limit: INBOX_READ_LIMIT });
    this.reading = source.subscribe({
      next: (data) => {
        this.reading = null;
        if (initial) {
          this.started(data, statementId);
        } else {
          this.landed(data, statementId, accountId);
        }
      },
      error: () => {
        this.reading = null;
        if (initial) {
          this.load.set('failed');
          return;
        }
        this.busy.set(false);
        // The restore target stays: 重試 reads again and restores.
        this.rereadFailed.set(this.pending ? 'answer' : this.pendingRevert !== null ? 'undo' : 'reload');
      },
    });
  }

  private started(data: InboxOut, statementId: number): void {
    const account =
      data.accounts.find((a) => a.items.some((item) => item.statement.id === statementId)) ?? null;
    const items = account ? askItems(account.items, statementId) : [];
    this.account.set(account);
    this.periodEnd.set(account?.items.find((item) => item.statement.id === statementId)?.statement.period_end ?? null);
    this.flow.set(startFlow(items));
    this.load.set('ready');
  }

  private landed(data: InboxOut, statementId: number, accountId: number | undefined): void {
    const items = data.accounts.find((a) => a.id === accountId)?.items ?? [];
    const before = this.flow().cursor;
    let state = afterRead(this.flow(), askItems(items, statementId));
    const pending = this.pending;
    this.pending = null;
    let error: FlowError | null = null;
    if (pending && pending.caseId !== this.pendingRevert) {
      error = this.settle(pending, state); // an answer undone meanwhile is open again on purpose: no refusal text
    }
    if (state.cursor !== null && !state.open.has(state.cursor)) {
      state = advance(state);
    }
    // After 復原 the question is back under a new case id: find it by its line / entry, put it at its old place.
    if (this.pendingRevert !== null) {
      const saved = this.saved.get(this.pendingRevert);
      state = saved ? restore(state, saved) : goTo(state, this.pendingRevert);
      this.saved.delete(this.pendingRevert);
      this.pendingRevert = null;
    }
    const focus = pending !== null || this.hasFocus();
    this.flow.set(state);
    if (pending) {
      this.inlineError.set(error);
    }
    this.busy.set(false);
    this.rereadFailed.set(null);
    if (focus && state.cursor !== before) {
      this.focusStep();
    }
  }

  /** After the re-read: still open → the refusal text; a refused `case_closed` that left → 這筆已經處理過了. */
  private settle(pending: PendingAnswer, state: FlowState): FlowError | null {
    const result = pending.out.results.find((r) => r.case_id === pending.caseId);
    if (state.open.has(pending.caseId)) {
      return flowError(result?.code ?? null);
    }
    if (result?.status === 'refused' && result.code === 'case_closed') {
      this.toasts.done('這筆已經處理過了'); // answered elsewhere: nothing of ours to undo
    }
    return null;
  }

  /**
   * 完成 with 復原 as soon as the apply says the answer went in, before the re-read: a failed re-read followed by
   * closing the flow must not lose the 復原. A `rule_error` raises its error instead (no 復原 on that one).
   */
  private announce(item: InboxItem, answer: string, out: InboxApplyOut, accountId: number, statementId: number): void {
    const result = out.results.find((r) => r.case_id === item.case_id);
    if (!result || result.status === 'refused') {
      return;
    }
    if (out.rule_error) {
      this.toasts.error('答案已記下，但規則沒存：' + flowError(out.rule_error).text.replace(/^沒成功：/, ''));
      return;
    }
    this.undo.offer({
      answer,
      caseId: item.case_id,
      actionIds: out.results
        .filter((r) => r.status !== 'refused' && r.action_id !== null)
        .map((r) => r.action_id as number),
      accountId,
      statementId,
      rule: out.rule_id !== null ? { id: out.rule_id, pattern: item.rule_hint?.merchant_pattern ?? '' } : null,
    });
  }

  /**
   * Only a GET that started after the 復原 finished may restore its question: a read already in flight may have been
   * answered by the server before the revert, however late its response arrives. So a read in flight (the first read,
   * an answer's re-read, a 重試) is superseded by a fresh one; an apply still in flight issues its re-read afterwards
   * anyway. One extra GET per 復原 at most.
   */
  private onReverted(reverted: ReconReverted): void {
    if (reverted.statementId !== this.statementId() || this.load() === 'failed') {
      return; // another statement; or the failed first read's 重試 reads afresh anyway
    }
    if (this.load() === 'loading') {
      this.read(true); // the opening snapshot, no restore needed: the server order places the question
      return;
    }
    if (!this.account()) {
      this.restart(); // opened on a finished statement: only a full read can find the account again
      return;
    }
    this.pendingRevert = reverted.caseId;
    if (this.reading || !this.busy()) {
      this.read(false);
    }
  }

  // ----- applying --------------------------------------------------------------------------------------------------

  private applyOneTap(item: InboxItem, action: OneTap): void {
    const learnable = item.rule_hint !== null && learnableAnswer(item.question, action);
    this.send(item, {
      cases: [{ case_id: item.case_id, case_version: item.case_version }],
      action,
      params: {},
      answer: buttonLabel(action, item, this.currency()),
      learn: learnable ? inboxLearn(this.learnOn(), false, action) : null,
    });
  }

  /**
   * One `/inbox/apply`. Not tied to the component's life: a request cancelled on close could still have been applied,
   * so it runs to its end and bumps `entriesChanged`; only the screen work is skipped once the flow is gone.
   */
  private send(item: InboxItem, body: InboxApplyBody): void {
    const account = this.account();
    if (this.locked() || !account) {
      return;
    }
    const gen = this.gen;
    const statementId = this.statementId();
    const saved = this.snapshot(item);
    this.busy.set(true);
    this.inlineError.set(null);
    // A background read (after 復原) must not land mid-apply and clear `busy`; `pendingGoTo` stays queued for the
    // answer's own re-read.
    this.reading?.unsubscribe();
    this.reading = null;
    this.inbox.apply(body).subscribe({
      next: (out) => {
        this.accounting.markEntriesChanged();
        this.announce(item, body.answer, out, account.id, statementId);
        if (this.destroyed || gen !== this.gen) {
          return;
        }
        if (out.results.some((r) => r.case_id === item.case_id && r.status !== 'refused')) {
          this.saved.set(item.case_id, saved);
        }
        this.pending = { caseId: item.case_id, out };
        this.read(false);
      },
      error: (err: unknown) => {
        if (this.destroyed || gen !== this.gen) {
          return;
        }
        this.busy.set(false);
        this.inlineError.set(flowError(err));
        if (this.pendingRevert !== null) {
          this.read(false); // the 復原 read this press cancelled
        }
      },
    });
  }

  // ----- helpers ---------------------------------------------------------------------------------------------------

  /** The answered item's place and, by line / entry, it and the followers that close with it. */
  private snapshot(item: InboxItem): SavedAnswer {
    const state = this.flow();
    const keys = new Map<number, string>();
    const riders = [...followers(state)].filter(([, leader]) => leader === item.case_id).map(([id]) => id);
    for (const id of [item.case_id, ...riders]) {
      const it = state.open.get(id);
      const key = it ? itemKey(it) : null;
      if (key !== null) {
        keys.set(id, key);
      }
    }
    return { position: state.order.indexOf(item.case_id), keys };
  }

  /** A step change the owner asked for in the flow: focus follows it. */
  private owned(change: (state: FlowState) => FlowState): boolean {
    const before = this.flow();
    const after = change(before);
    if (after === before) {
      return false;
    }
    this.flow.set(after);
    if (after.cursor !== before.cursor) {
      this.focusStep();
    }
    return true;
  }

  /** 「1–3 選擇 · …」 with the digits this step really has. */
  private keyHint(digits: number): string {
    const pick = digits === 0 ? null : digits === 1 ? '1 選擇' : `1–${digits} 選擇`;
    return [pick, 'S 略過', '← → 上下筆', 'Esc 關閉'].filter((part) => part !== null).join(' · ');
  }

  private pairNote(item: InboxItem): string | null {
    const state = this.flow();
    const follower = [...followers(state)].find(([, leader]) => leader === item.case_id)?.[0];
    const entry = follower === undefined ? null : flowEntry(state.open.get(follower) as InboxItem);
    if (!entry) {
      return null;
    }
    const amount = formatMoney(entry.amount, this.currency(), { sign: true });
    return `你這筆 ${amount} 也列在「你記了，銀行沒有」。這裡對上或改金額後，那一題會自動消失。`;
  }

  private hasFocus(): boolean {
    return this.host.nativeElement.contains(this.document.activeElement);
  }

  /** The new question's headline (or the end state's heading), so a screen reader reads it. */
  private focusStep(): void {
    afterNextRender(
      () => this.host.nativeElement.querySelector<HTMLElement>('h2[tabindex="-1"]')?.focus(),
      { injector: this.injector },
    );
  }
}
