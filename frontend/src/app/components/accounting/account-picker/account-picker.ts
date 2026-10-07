// frontend/src/app/components/accounting/account-picker/account-picker.ts
import { DOCUMENT, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  input,
  model,
  signal,
  viewChild,
} from '@angular/core';

import { AccountGroup, LedgerAccount } from '../../../models/accounting.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { isHandledKey, trapFocus } from '../accounting-ui';
import { formatMoney } from '../format';
import { pickerGroups, visibleAccounts } from './picker-groups';
import { readRecentAccounts } from './recent-accounts';

/** The search box appears from this many offerable accounts (spec §3.1). */
export const SEARCH_THRESHOLD = 8;
const RECENT_CHIPS = 4;
const POPOVER_MIN_WIDTH = 260;
const POPOVER_GAP = 4;

/**
 * 帳戶 chooser for every form (spec §3): trigger `[icon] name · balance`; a bottom sheet on phones, a fixed popover
 * under (or above) the trigger elsewhere. Owns its focus and implements the overlay keyboard contract.
 */
@Component({
  selector: 'app-account-picker',
  standalone: true,
  imports: [NgTemplateOutlet],
  templateUrl: './account-picker.html',
  styleUrls: ['../sheet.scss', './account-picker.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    '(keydown)': 'onHostKeydown($event)',
    '(document:pointerdown)': 'onDocumentPointerDown($event)',
    '(window:resize)': 'reposition()',
    '[class.compact]': 'compact()',
  },
})
export class AccountPickerComponent {
  private readonly layoutMode = inject(LayoutModeService);
  private readonly injector = inject(Injector);
  private readonly document = inject(DOCUMENT);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  constructor() {
    // Scroll does not bubble; capture ancestor-pane scrolling without reacting to the panel itself.
    const scroll = (event: Event) => this.onScroll(event);
    this.document.addEventListener('scroll', scroll, true);
    inject(DestroyRef).onDestroy(() => this.document.removeEventListener('scroll', scroll, true));
  }

  readonly accounts = input<LedgerAccount[]>([]);
  readonly groups = input<AccountGroup[] | null>(null);
  readonly value = model<number | null>(null);
  readonly label = input.required<string>();
  readonly allowArchived = input(false);
  readonly exclude = input<readonly number[]>([]);
  readonly compact = input(false);
  readonly disabled = input(false);

  private readonly trigger = viewChild.required<ElementRef<HTMLButtonElement>>('trigger');
  private readonly panel = viewChild<ElementRef<HTMLElement>>('panel');

  readonly open = signal(false);
  readonly query = signal('');
  readonly activeId = signal<number | null>(null);
  private readonly recent = signal<number[]>([]);
  readonly popoverStyle = signal<Record<string, string>>({});

  readonly phone = computed(() => this.layoutMode.mode() === 'phone');
  readonly selected = computed(() => this.accounts().find(account => account.id === this.value()) ?? null);
  private readonly offerable = computed(() =>
    visibleAccounts(this.accounts(), { allowArchived: this.allowArchived(), exclude: this.exclude() }),
  );
  readonly showSearch = computed(() => this.offerable().length >= SEARCH_THRESHOLD);
  readonly groupList = computed(() =>
    pickerGroups(this.accounts(), this.groups(), {
      allowArchived: this.allowArchived(),
      exclude: this.exclude(),
      query: this.query(),
    }),
  );
  readonly options = computed(() => this.groupList().flatMap(group => group.accounts));
  readonly recentAccounts = computed(() => {
    if (this.query().trim()) {
      return [];
    }
    const byId = new Map(this.offerable().filter(account => !account.is_archived).map(account => [account.id, account]));
    return this.recent()
      .map(id => byId.get(id))
      .filter((account): account is LedgerAccount => !!account)
      .slice(0, RECENT_CHIPS);
  });
  readonly triggerLabel = computed(() => {
    const account = this.selected();
    return account ? `${this.label()}：${account.name}` : `${this.label()}：選擇帳戶`;
  });

  balanceText(account: LedgerAccount): string {
    if (account.is_credit && account.available_credit !== null) {
      return `可用 ${formatMoney(account.available_credit, account.currency)}`;
    }
    return formatMoney(account.balance, account.currency);
  }

  toggle(): void {
    if (this.open()) {
      this.close();
    } else {
      this.openPanel();
    }
  }

  private openPanel(): void {
    if (this.disabled()) {
      return;
    }
    this.query.set('');
    this.recent.set(readRecentAccounts());
    this.activeId.set(this.firstActive());
    if (!this.phone()) {
      this.popoverStyle.set(this.placePopover());
    }
    this.open.set(true);
    afterNextRender(() => {
      this.reposition();
      this.focusInitial();
    }, { injector: this.injector });
  }

  /** Esc, selection and outside clicks close; focus returns to the trigger unless the click went elsewhere. */
  close(restoreFocus = true): void {
    if (!this.open()) {
      return;
    }
    this.open.set(false);
    if (restoreFocus) {
      this.trigger().nativeElement.focus();
    }
  }

  choose(id: number): void {
    this.close();
    if (id !== this.value()) {
      this.value.set(id);
    }
  }

  onSearch(text: string): void {
    this.query.set(text);
    this.activeId.set(this.options()[0]?.id ?? null);
  }

  onDocumentPointerDown(event: Event): void {
    if (this.open() && !this.phone() && !this.host.nativeElement.contains(event.target as Node | null)) {
      this.close(false);
    }
  }

  /**
   * Overlay keyboard contract (spec §3.1): every key handled here is marked handled, so the entry form (⏎ save, Esc
   * cancel) and the layout (Esc, shortcuts) leave it alone. Esc closes only the picker.
   */
  onHostKeydown(event: KeyboardEvent): void {
    if (!this.open() || isHandledKey(event)) {
      return;
    }
    const panel = this.panel()?.nativeElement;
    if (!panel) {
      return;
    }
    const target = event.target as HTMLElement | null;
    switch (event.key) {
      case 'Escape':
        event.preventDefault();
        this.close();
        return;
      case 'Tab':
        trapFocus(panel, event);
        return;
      case 'ArrowDown':
      case 'ArrowUp':
        event.preventDefault();
        this.move(event.key === 'ArrowDown' ? 1 : -1, event.key === 'ArrowDown' && target?.matches('.acct-search') === true);
        return;
      case 'Enter': {
        if (target?.tagName === 'BUTTON') {
          return; // a 最近使用 chip: its native click chooses (no form handler saves on a BUTTON)
        }
        event.preventDefault();
        const row = target?.closest<HTMLElement>('.acct-option[data-account-id]');
        const id = row ? Number(row.dataset['accountId']) : this.activeId();
        if (id !== null) {
          this.choose(id);
        }
        return;
      }
    }
  }

  private move(delta: 1 | -1, focusCurrent = false): void {
    const options = this.options();
    if (options.length === 0) {
      return;
    }
    const index = options.findIndex(account => account.id === this.activeId());
    const next = options[index === -1 ? 0 : focusCurrent ? index : (index + delta + options.length) % options.length];
    this.activeId.set(next.id);
    afterNextRender(() => this.optionElement(next.id)?.focus(), { injector: this.injector });
  }

  onOptionFocus(id: number): void {
    this.activeId.set(id);
  }

  private firstActive(): number | null {
    const value = this.value();
    const options = this.options();
    return options.some(account => account.id === value) ? value : (options[0]?.id ?? null);
  }

  private optionElement(id: number | null): HTMLElement | null {
    return id === null ? null : (this.panel()?.nativeElement.querySelector<HTMLElement>(`.acct-option[data-account-id="${id}"]`) ?? null);
  }

  /** Desktop: the search box when present, else the selected (or first) row; phone: never the search box. */
  private focusInitial(): void {
    const panel = this.panel()?.nativeElement;
    if (!panel) {
      return;
    }
    const search = this.phone() ? null : panel.querySelector<HTMLElement>('.acct-search');
    (search ?? this.optionElement(this.activeId()) ?? panel).focus();
  }

  /** Re-measure after rendering and on resize; scrolling the containing pane closes the anchored panel. */
  reposition(): void {
    if (this.open() && !this.phone()) {
      this.popoverStyle.set(this.placePopover());
    }
  }

  onScroll(event: Event): void {
    const target = event.target;
    if (this.open() && !this.phone() && target instanceof HTMLElement &&
        target.contains(this.trigger().nativeElement) && !this.panel()?.nativeElement.contains(target)) {
      this.close(false);
    }
  }

  /** Choose a direction first, then cap the content box within that side's viewport space. */
  private placePopover(): Record<string, string> {
    const rect = this.trigger().nativeElement.getBoundingClientRect();
    const view = this.document.defaultView;
    const height = view?.innerHeight ?? 800;
    const width = view?.innerWidth ?? 1024;
    const panel = this.panel()?.nativeElement;
    const css = panel && view ? view.getComputedStyle(panel) : null;
    const number = (value: string | undefined) => parseFloat(value ?? '') || 0;
    const padding = css ? number(css.paddingTop) + number(css.paddingBottom) : 12;
    const border = css ? number(css.borderTopWidth) + number(css.borderBottomWidth) : 2;
    const contentHeight = panel ? Math.max(0, panel.scrollHeight - padding) : 240;
    const below = Math.max(0, height - rect.bottom - POPOVER_GAP - 8);
    const above = Math.max(0, rect.top - POPOVER_GAP - 8);
    const belowFits = below >= Math.min(contentHeight, 240);
    const available = belowFits ? below : above;
    const popoverWidth = Math.min(Math.max(rect.width, POPOVER_MIN_WIDTH), Math.max(0, width - 30));
    const style: Record<string, string> = {
      position: 'fixed',
      left: `${Math.round(Math.max(8, Math.min(rect.left, width - popoverWidth - 22)))}px`,
      width: `${Math.round(popoverWidth)}px`,
      'max-height': `${Math.floor(Math.max(0, Math.min(height * 0.6, available - padding - border)))}px`,
    };
    if (belowFits) {
      style['top'] = `${Math.round(rect.bottom + POPOVER_GAP)}px`;
    } else {
      style['bottom'] = `${Math.round(height - rect.top + POPOVER_GAP)}px`;
    }
    return style;
  }
}
