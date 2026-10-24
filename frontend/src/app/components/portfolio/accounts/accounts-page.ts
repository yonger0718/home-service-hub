import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { map } from 'rxjs';

import { BrokerAccount } from '../../../models/portfolio.model';
import { LayoutModeService } from '../../../services/layout-mode.service';
import { PageHeaderComponent, PageMenuItem } from '../../shell/page-header/page-header';
import { BtnComponent } from '../../ui/btn/btn';
import { PortfolioAccountDetailComponent } from './account-detail';
import { PortfolioAccountsListComponent } from './accounts-list';

/**
 * 現金帳戶 for `portfolio/accounts` and `portfolio/accounts/:id` (Decision 8). In 'panes' mode: one page header,
 * the list as a master column and the selected account's detail beside it. Below 'panes' (#116): the bare URL shows the
 * same header over the master card list in one column (no selection, no auto-navigation; a tap opens the detail), and
 * `/portfolio/accounts/:id` shows the detail with its own header.
 */
@Component({
  selector: 'app-portfolio-accounts-page',
  imports: [PageHeaderComponent, BtnComponent, PortfolioAccountsListComponent, PortfolioAccountDetailComponent],
  templateUrl: './accounts-page.html',
  styleUrl: './accounts-page.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PortfolioAccountsPageComponent {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly layout = inject(LayoutModeService);

  readonly panes = computed(() => this.layout.mode() === 'panes');
  readonly phone = computed(() => this.layout.mode() === 'phone');
  /** Phones: 新增帳戶 leaves the header row for the ⋯ menu. */
  readonly phoneMenu: PageMenuItem[] = [{ label: '新增帳戶', icon: 'pi-plus', run: () => this.openCreateDialog() }];
  /** The route's `:id`, or null on `/portfolio/accounts` (and for an id that is not a number). */
  readonly routeId = toSignal(this.route.paramMap.pipe(map(params => Number(params.get('id')) || null)), {
    initialValue: null,
  });
  /** From the list's latest load: the first active account, else the first; null when there is none. */
  readonly firstActiveId = signal<number | null>(null);
  readonly selectedId = computed(() => this.routeId() ?? this.firstActiveId());
  readonly list = viewChild(PortfolioAccountsListComponent);

  /**
   * The auto-selection has navigated since the URL last named an account: at most one navigation per visit to the
   * bare URL, however often the list loads or the mode flips before the router lands.
   */
  private autoNavigated = false;

  constructor() {
    // Panes on `/portfolio/accounts`: put the first active account in the URL (replacing the bare entry).
    effect(() => {
      const id = this.firstActiveId();
      if (this.routeId() !== null) {
        this.autoNavigated = false;
        return;
      }
      if (!this.panes() || id === null || this.autoNavigated) return;
      this.autoNavigated = true;
      untracked(() => void this.router.navigate(['/portfolio/accounts', id], { replaceUrl: true }));
    });
  }

  onLoaded(accounts: readonly BrokerAccount[]): void {
    this.firstActiveId.set((accounts.find(account => account.is_active) ?? accounts[0])?.id ?? null);
  }

  openCashForm(): void {
    this.list()?.openCashForm();
  }

  openCreateDialog(): void {
    this.list()?.openCreateDialog();
  }
}
