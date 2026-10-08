import { Location } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  OnInit,
  afterNextRender,
  computed,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';

import { ENTRY_KIND_LABELS, EntryDetail } from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { AccountingToastService, scheduleActionError } from '../accounting-toast';
import { colorOf, iconOf, isHandledKey } from '../accounting-ui';
import { slashDate } from '../dates';
import { storedGroupNet } from '../entry-detail/split-card';
import { formatMoney } from '../format';
import { LockBannerComponent } from '../lock-banner/lock-banner';
import { SplitFolderComponent } from '../split-folder/split-folder';
import {
  GroupNavState,
  carriedState,
  childState,
  closeGroupPage,
  entryCommands,
  groupParentUrl,
  readGroupNavState,
} from './group-nav';

/**
 * The 多類別 group view (PR-9): the split as one record, MOZE style. Routed by one of its members
 * (`entries/:id/group`, or `accounts/:id/entries/:eid/group` from a passbook) because the API reads a split through a
 * member; served by the same phone / sheet / pane outlets as the entry detail. Its child list opens a member's detail
 * one history entry above it; ✕ goes back to the page the group view was opened from, never to a child.
 */
@Component({
  selector: 'app-split-group',
  standalone: true,
  imports: [RouterLink, LockBannerComponent, SplitFolderComponent],
  templateUrl: './split-group.html',
  styleUrls: ['../entry-detail/entry-detail.scss', './split-group.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onKeydown($event)' },
})
export class SplitGroupComponent implements OnInit {
  private service = inject(AccountingService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private location = inject(Location);
  private destroyRef = inject(DestroyRef);
  private layoutMode = inject(LayoutModeService);
  private host = inject<ElementRef<HTMLElement>>(ElementRef);
  private injector = inject(Injector);
  private readonly toast = inject(AccountingToastService);

  /** The member the route names (`:eid` under a passbook, else `:id`). */
  readonly routeEntryId = signal<number | null>(null);
  readonly passbookId = signal<number | null>(null);
  /** In the 760–1023 px sheet the sheet's own ✕ closes it. */
  readonly inSheet = computed(() => this.layoutMode.mode() === 'sheet');
  readonly detail = signal<EntryDetail | null>(null);
  readonly loadError = signal(false);
  readonly deleting = signal(false);
  readonly busy = signal(false);
  readonly formError = signal<string | null>(null);
  private readonly navState = signal<GroupNavState>({});
  private panelOpener: HTMLElement | null = null;
  private loadSeq = 0;

  readonly kindLabels = ENTRY_KIND_LABELS;
  readonly formatMoney = formatMoney;
  readonly slashDate = slashDate;
  readonly iconOf = iconOf;
  readonly colorOf = colorOf;

  readonly group = computed(() => this.detail()?.group ?? null);
  /** Every member in group order, protected ones included. */
  readonly members = computed(() => this.detail()?.group_members ?? []);
  readonly first = computed(() => this.members()[0] ?? this.detail());
  /** Per account currency, from the server-signed stored amounts (protected members included). */
  readonly net = computed(() => storedGroupNet(this.members()));
  readonly icons = computed(() => this.members().map(member => ({ icon: iconOf(member), color: colorOf(member) })));
  readonly accountCount = computed(() => new Set(this.members().map(member => member.account_id)).size);
  readonly title = computed(() => this.group()?.name?.trim() || '多類別');
  readonly locked = computed(() => this.detail()?.locked ?? false);
  /** A scheduled period's group is saved by its schedule (PUT /splits answers 409, D29), as the member detail rules. */
  readonly canEdit = computed(() => !!this.detail() && !this.locked() && !this.detail()?.schedule);
  /** 刪除整組 as the member detail offers it: not for a scheduled period's group. */
  readonly canDeleteGroup = computed(() => !!this.detail()?.group && !this.detail()?.schedule);
  /** A child opened from here sits one history entry above this view. */
  readonly childState = computed<GroupNavState>(() => childState(this.navState()));
  /** This view took a child's place (「← 多類別」): its children take its place in turn, nothing stacks. */
  readonly childReplacesUrl = computed(() => this.navState().viaGroup === true);

  ngOnInit(): void {
    this.route.paramMap.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(params => {
      const passbook = params.get('eid') !== null;
      const id = Number(passbook ? params.get('eid') : params.get('id'));
      this.navState.set(readGroupNavState(this.router, this.location));
      this.routeEntryId.set(id);
      this.passbookId.set(passbook ? Number(params.get('id')) : null);
      this.deleting.set(false);
      this.busy.set(false);
      this.formError.set(null);
      this.panelOpener = null;
      if (this.detail()?.id !== id) {
        this.detail.set(null);
      }
      this.load(id);
    });
  }

  private load(id: number): void {
    const seq = ++this.loadSeq;
    this.loadError.set(false);
    this.service
      .getEntry(id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: detail => {
          if (seq !== this.loadSeq) {
            return;
          }
          if (detail.group?.kind !== 'split') {
            // No longer a split (dissolved to one member): the entry's own detail takes this history entry.
            void this.router.navigate(entryCommands(id, this.passbookId()), { replaceUrl: true, state: carriedState(this.navState()) });
            return;
          }
          this.detail.set(detail);
        },
        error: () => {
          if (seq === this.loadSeq) {
            this.detail.set(null);
            this.loadError.set(true);
          }
        },
      });
  }

  childCommands(memberId: number): (string | number)[] {
    return entryCommands(memberId, this.passbookId());
  }

  memberTitle(member: EntryDetail['group_members'][number]): string {
    return member.name || member.category?.split('/').pop() || this.kindLabels[member.kind];
  }

  /** 應收／應付 for a receivable / payable member (its row shows 對象 instead of the account), else null. */
  partyMark(member: EntryDetail['group_members'][number]): string | null {
    return member.kind === 'receivable' ? '應收' : member.kind === 'payable' ? '應付' : null;
  }

  /**
   * ✕ and after 刪除整組: opened from a list row, back to that list (nothing added to history); with `closeTo` that
   * fixed page; else (deep link, ↑ ↓) the parent list.
   */
  close(): void {
    const state = this.navState();
    closeGroupPage(this.router, this.location, state, groupParentUrl(state, this.passbookId()));
  }

  /** 編輯: the entry form for this split with the parent (多類別) bubble selected. */
  edit(): void {
    const detail = this.detail();
    if (!detail || !this.canEdit()) {
      return;
    }
    void this.router.navigate(['/accounting/entries', detail.id, 'edit'], { queryParams: { select: 'parent' } });
  }

  openDelete(): void {
    if (!this.detail() || this.locked()) {
      return;
    }
    const active = this.host.nativeElement.ownerDocument.activeElement;
    this.panelOpener = active instanceof HTMLElement && this.host.nativeElement.contains(active) ? active : null;
    this.formError.set(null);
    this.deleting.set(true);
  }

  closePanel(): void {
    this.deleting.set(false);
    const opener = this.panelOpener;
    this.panelOpener = null;
    if (opener?.isConnected) {
      afterNextRender(() => opener.focus(), { injector: this.injector });
    }
  }

  /** Esc with the delete confirmation open closes it first and is marked handled (the sheet stays open). */
  onKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event) || !this.deleting() || event.key !== 'Escape') {
      return;
    }
    event.preventDefault();
    this.closePanel();
  }

  confirmDeleteGroup(): void {
    const detail = this.detail();
    const group = detail?.group;
    if (!detail || !group || this.locked() || this.busy()) {
      return;
    }
    const id = detail.id;
    this.busy.set(true);
    this.service
      .deleteSplit(group.id)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: () => {
          if (this.detail()?.id !== id) {
            return;
          }
          this.busy.set(false);
          this.close();
        },
        error: error => {
          if (this.detail()?.id !== id) {
            return;
          }
          this.busy.set(false);
          this.formError.set(scheduleActionError(error, this.toast));
        },
      });
  }
}
