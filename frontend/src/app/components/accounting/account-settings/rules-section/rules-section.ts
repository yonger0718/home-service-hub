import { DOCUMENT } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  Renderer2,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';

import { RewardRule, RewardRuleState, RewardUsage } from '../../../../models/accounting.model';
import { CapBarComponent } from '../../../ui/cap-bar/cap-bar';
import { SwitchComponent } from '../../../ui/switch/switch';
import { isHandledKey } from '../../accounting-ui';
import {
  RULE_STATE_LABELS, RewardUnit, mutedLine, postingSummary, rateChip, ruleState, ruleTitle, usageUnit,
} from '../../reward-rule-text';

export interface RuleRowView {
  rule: RewardRule;
  state: RewardRuleState;
  title: string;
  rate: string;
  /** The state tag on a non-active rule; null on an active one. */
  tag: string | null;
  /** 基本 on an active basic rule only — one tag per row, the state wins. */
  basic: boolean;
  posting: string;
  /** The usage row behind the cap bar — active rules only. */
  usage: RewardUsage | null;
  unit: RewardUnit | null;
  /** The muted line in place of the bar on a non-active rule. */
  muted: string | null;
}

/** `ids` with the id at `from` removed and inserted at `to`. */
export function movedIds(ids: number[], from: number, to: number): number[] {
  const out = ids.slice();
  const [id] = out.splice(from, 1);
  out.splice(to, 0, id);
  return out;
}

/** Vertical movement (px) a press must exceed before it becomes a drag; less is a tap or a jitter. */
const DRAG_SLOP = 4;

interface DragSession {
  pointerId: number;
  handle: HTMLElement;
  startY: number;
  /** Row rects in the original order, recorded once at pointerdown (rows are not reordered while dragging). */
  rects: { top: number; height: number }[];
  /** The `rules` input at pointerdown: a different array (a re-read landed) cancels the drag. */
  rules: RewardRule[];
  stopListening: () => void;
}

let nextRulesId = 0;

/** The account settings 回饋規則 rows (D6): handle, name + rate chip + tags, posting summary, cap bar or muted line,
 *  enable switch and ⋯. Presentational: tapping the row emits `open`, the switch emits `toggle`, a drag on the handle
 *  or 上移 / 下移 in ⋯ emit `reorder` (the full id list), 刪除 emits `remove`; the page writes. */
@Component({
  selector: 'app-account-rules',
  imports: [CapBarComponent, SwitchComponent],
  templateUrl: './rules-section.html',
  styleUrl: './rules-section.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountRulesComponent {
  readonly rules = input.required<RewardRule[]>();
  /** Rule id → the usage row shown on that rule (a shared-cap group already collapsed to its first listed member). */
  readonly usage = input<ReadonlyMap<number, RewardUsage>>(new Map());
  /** `YYYY-MM-DD`. */
  readonly today = input.required<string>();
  readonly busy = input(false);

  readonly open = output<RewardRule>();
  readonly toggle = output<{ rule: RewardRule; enabled: boolean }>();
  readonly reorder = output<number[]>();
  readonly remove = output<RewardRule>();

  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly renderer = inject(Renderer2);
  private readonly document = inject(DOCUMENT);
  private readonly injector = inject(Injector);

  protected readonly menuPrefix = `rule-menu-${++nextRulesId}`;
  /** The rule id whose ⋯ menu is open. */
  protected readonly menuFor = signal<number | null>(null);
  /** A press is in progress inside the menu: Safari blurs a tapped button with relatedTarget null before click. */
  private pressing = false;
  private pressTimer: ReturnType<typeof setTimeout> | undefined;
  private unlistenMenu: (() => void) | null = null;

  /** An armed or active drag: `from` / `to` are indices in the `rules` order captured at pointerdown. */
  protected readonly drag = signal<{ id: number; from: number; to: number } | null>(null);
  /** The drag has moved past the slop: the row shows `.dragging` and a release can drop. */
  protected readonly dragMoved = signal(false);
  private session: DragSession | null = null;

  /** Announced after a move (polite live region). */
  protected readonly liveText = signal('');

  protected readonly rows = computed<RuleRowView[]>(() => {
    const today = this.today();
    const usage = this.usage();
    return this.rules().map((rule): RuleRowView => {
      const state = ruleState(rule, today);
      const active = state === 'active';
      const use = active ? usage.get(rule.id) ?? null : null;
      return {
        rule,
        state,
        title: ruleTitle(rule),
        rate: rateChip(rule),
        tag: active ? null : RULE_STATE_LABELS[state],
        basic: active && rule.is_basic,
        posting: postingSummary(rule),
        usage: use,
        unit: use ? usageUnit(use) : null,
        muted: mutedLine({ state, starts_on: rule.starts_on }),
      };
    });
  });

  /** Index in the original row order before which the drop line is drawn (`rows().length` = after the last row). */
  protected readonly dropBefore = computed<number | null>(() => {
    const d = this.drag();
    if (!d || !this.dragMoved() || d.to === d.from) return null;
    return d.to < d.from ? d.to : d.to + 1;
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => {
      this.stopMenuListening();
      clearTimeout(this.pressTimer);
      this.endDrag();
    });
    // Only `rules` is tracked: setting `drag` at pointerdown must not re-run this and cancel the drag.
    effect(() => {
      const rules = this.rules();
      untracked(() => {
        if (this.session && rules !== this.session.rules) this.cancelDrag();
        const open = this.menuFor();
        if (open !== null && !rules.some(r => r.id === open)) this.closeMenu(false);
      });
    });
  }

  // ⋯ menu ---------------------------------------------------------------------------------------------------------

  protected menuId(rule: RewardRule): string {
    return `${this.menuPrefix}-${rule.id}`;
  }

  protected toggleMenu(rule: RewardRule, event: Event): void {
    event.stopPropagation();
    if (this.menuFor() === rule.id) {
      this.closeMenu();
      return;
    }
    this.closeMenu(false);
    this.menuFor.set(rule.id);
    const unlisten = [
      this.renderer.listen(this.document, 'pointerdown', (e: Event) => {
        const target = e.target as Node | null;
        if (!target || !this.menuWrap()?.contains(target)) this.closeMenu(false);
      }),
      // A press that starts on an item and ends outside the panel would otherwise leave `pressing` set.
      // Deferred: on touch, pointerup precedes the compatibility mousedown that moves focus, so the guard must outlive it.
      this.renderer.listen(this.document, 'pointerup', () => {
        clearTimeout(this.pressTimer);
        this.pressTimer = setTimeout(() => (this.pressing = false), 350);
      }),
    ];
    this.unlistenMenu = () => unlisten.forEach(u => u());
    afterNextRender(() => this.menuItems()[0]?.focus({ preventScroll: true }), { injector: this.injector });
  }

  protected closeMenu(restoreFocus = true): void {
    const id = this.menuFor();
    if (id === null) return;
    this.stopMenuListening();
    clearTimeout(this.pressTimer);
    this.pressing = false;
    this.menuFor.set(null);
    if (restoreFocus) this.moreButton(id)?.focus({ preventScroll: true });
  }

  protected onMenuKeydown(event: KeyboardEvent): void {
    if (this.menuFor() === null || isHandledKey(event)) return;
    const els = this.menuItems();
    const at = els.indexOf(this.document.activeElement as HTMLButtonElement);
    let next: number;
    switch (event.key) {
      case 'ArrowDown':
        next = at < 0 ? 0 : (at + 1) % els.length;
        break;
      case 'ArrowUp':
        next = at < 0 ? els.length - 1 : (at - 1 + els.length) % els.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = els.length - 1;
        break;
      case 'Escape':
        event.preventDefault();
        this.closeMenu();
        return;
      default:
        return;
    }
    event.preventDefault();
    els[next]?.focus({ preventScroll: true });
  }

  protected onMenuPress(down: boolean): void {
    this.pressing = down;
  }

  protected onMenuFocusout(event: FocusEvent): void {
    if (this.pressing || this.menuFor() === null) return;
    const next = event.relatedTarget as Node | null;
    if (!next || !this.menuWrap()?.contains(next)) this.closeMenu(false);
  }

  protected runMenu(action: 'edit' | 'up' | 'down' | 'delete', rule: RewardRule, index: number): void {
    this.closeMenu(false);
    if (action === 'edit' || action === 'delete') {
      if (action === 'edit') this.open.emit(rule);
      else this.remove.emit(rule);
      // Only take focus back when nothing else (the rule sheet) claimed it.
      const active = this.document.activeElement;
      if (!active || active === this.document.body || this.host.nativeElement.contains(active)) {
        this.moreButton(rule.id)?.focus({ preventScroll: true });
      }
      return;
    }
    const ids = this.rules().map(r => r.id);
    const to = action === 'up' ? index - 1 : index + 1;
    if (this.busy() || to < 0 || to >= ids.length) return;
    this.emitMove(ids, index, to);
    // Keep focus on this rule's ⋯, not <body>: now, and again once the host has re-rendered the row at its new place
    // (moving a focused node in the DOM drops its focus).
    this.moreButton(rule.id)?.focus({ preventScroll: true });
    afterNextRender(() => this.moreButton(rule.id)?.focus({ preventScroll: true }), { injector: this.injector });
  }

  private menuWrap(): HTMLElement | null {
    const id = this.menuFor();
    return id === null
      ? null
      : this.host.nativeElement.querySelector<HTMLElement>(`.rule-row[data-rule-id="${id}"] .rule-more-wrap`);
  }

  /** The open menu's enabled items in order: a disabled 上移 / 下移 is skipped by the arrow keys and Home / End. */
  private menuItems(): HTMLButtonElement[] {
    const wrap = this.menuWrap();
    return wrap ? Array.from(wrap.querySelectorAll<HTMLButtonElement>('.rule-menu [role="menuitem"]:not(:disabled)')) : [];
  }

  private moreButton(id: number): HTMLButtonElement | null {
    return this.host.nativeElement.querySelector<HTMLButtonElement>(`.rule-row[data-rule-id="${id}"] .rule-more`);
  }

  private stopMenuListening(): void {
    this.unlistenMenu?.();
    this.unlistenMenu = null;
  }

  // Drag -----------------------------------------------------------------------------------------------------------

  protected onHandleDown(event: PointerEvent, index: number): void {
    if (event.button !== 0 || event.isPrimary === false || this.session) return;
    const rules = this.rules();
    if (this.busy() || rules.length < 2) return;
    event.preventDefault();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture?.(event.pointerId);
    const rects = this.rowEls().map(el => {
      const r = el.getBoundingClientRect();
      return { top: r.top, height: r.height };
    });
    this.closeMenu(false);
    this.session = {
      pointerId: event.pointerId,
      handle,
      startY: event.clientY,
      rects,
      rules,
      stopListening: this.listenDuringDrag(),
    };
    this.dragMoved.set(false);
    this.drag.set({ id: rules[index].id, from: index, to: index });
  }

  protected onHandleMove(event: PointerEvent): void {
    const s = this.session;
    const d = this.drag();
    if (!s || !d || event.pointerId !== s.pointerId) return;
    if (!this.dragMoved()) {
      if (Math.abs(event.clientY - s.startY) <= DRAG_SLOP) return;
      this.dragMoved.set(true);
    }
    const y = event.clientY;
    const above = s.rects.filter((r, i) => i !== d.from && r.top + r.height / 2 < y).length;
    const to = Math.min(Math.max(above, 0), s.rects.length - 1);
    if (to !== d.to) this.drag.set({ ...d, to });
  }

  protected onHandleUp(event: PointerEvent): void {
    const s = this.session;
    if (!this.drag() || !s || event.pointerId !== s.pointerId) return;
    // The release point counts as a final move (moves may be coalesced); pointer capture reports it outside the list.
    this.onHandleMove(event);
    const d = this.drag()!;
    const moved = this.dragMoved();
    const ids = s.rules.map(r => r.id);
    this.endDrag();
    if (moved && d.to !== d.from && !this.busy()) this.emitMove(ids, d.from, d.to);
  }

  /** Ends a drag without emitting: pointercancel, Escape, a scroll or wheel, or a new `rules` list. */
  protected cancelDrag(): void {
    this.endDrag();
  }

  private endDrag(): void {
    const s = this.session;
    if (s) {
      s.stopListening();
      if (s.handle.hasPointerCapture?.(s.pointerId)) s.handle.releasePointerCapture?.(s.pointerId);
    }
    this.session = null;
    this.drag.set(null);
    this.dragMoved.set(false);
  }

  /** Escape, a scroll (any scroller: capture phase on window) and wheel cancel a drag; listened only during one. */
  private listenDuringDrag(): () => void {
    const win = this.document.defaultView;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      this.cancelDrag();
    };
    const onCancel = () => this.cancelDrag();
    this.document.addEventListener('keydown', onKey);
    this.document.addEventListener('wheel', onCancel, { passive: true });
    win?.addEventListener('scroll', onCancel, { capture: true, passive: true });
    return () => {
      this.document.removeEventListener('keydown', onKey);
      this.document.removeEventListener('wheel', onCancel);
      win?.removeEventListener('scroll', onCancel, { capture: true });
    };
  }

  private rowEls(): HTMLElement[] {
    return Array.from(this.host.nativeElement.querySelectorAll<HTMLElement>(':scope > .rule-row'));
  }

  private emitMove(ids: number[], from: number, to: number): void {
    this.reorder.emit(movedIds(ids, from, to));
    this.liveText.set(`已移到第 ${to + 1} 項，共 ${ids.length} 項`);
  }
}
