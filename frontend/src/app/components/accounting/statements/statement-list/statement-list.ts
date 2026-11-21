import { ChangeDetectionStrategy, Component, computed, inject, signal, ElementRef, Injector } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { LayoutModeService } from '../../../../services/layout-mode.service';
import { SkeletonComponent } from '../../skeleton/skeleton';
import { StatementSummary } from '../statement.model';
import {
  consistencyLabel,
  consistencyMark,
  periodText,
  reconProgress,
  statementChips,
} from '../statement-view';
import { StatementsService } from '../statements.service';
import { BannerComponent } from '../../../ui/banner/banner';
import { focusPageIfLost } from '../../retry-focus';

/** `/accounting/accounts/:id/statements`: live rows, plus the historical periods summary table. */
@Component({
  selector: 'app-statement-list',
  standalone: true,
  imports: [RouterLink, SkeletonComponent, BannerComponent],
  templateUrl: './statement-list.html',
  styleUrls: ['../../entry-row.scss', './statement-list.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StatementListComponent {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private readonly route = inject(ActivatedRoute);
  private readonly statementsApi = inject(StatementsService);
  private readonly layoutMode = inject(LayoutModeService);
  private requestId = 0;

  readonly accountId = signal(0);
  readonly statements = signal<StatementSummary[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal(false);
  readonly isPhone = computed(() => this.layoutMode.mode() === 'phone');
  readonly live = computed(() =>
    this.statements().filter((statement) => statement.mode !== 'historical'),
  );
  readonly historical = computed(() =>
    this.statements().filter((statement) => statement.mode === 'historical'),
  );

  readonly periodText = periodText;
  readonly statementChips = statementChips;
  readonly reconProgress = reconProgress;
  readonly consistencyMark = consistencyMark;
  readonly consistencyLabel = consistencyLabel;

  constructor() {
    this.route.paramMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      const accountId = Number(params.get('id'));
      if (!accountId) {
        return;
      }
      this.accountId.set(accountId);
      this.load();
    });
  }


  /** A 重試 in flight: the banner stays (same element, nothing re-announced) with its button disabled. */
  readonly retrying = signal(false);

  private endRetry(ok: boolean): void {
    if (!this.retrying()) {
      return;
    }
    this.retrying.set(false);
    if (ok) {
      focusPageIfLost(this.host.nativeElement, this.injector, 'h1');
    }
  }

  retry(): void {
    if (this.retrying()) {
      return;
    }
    this.retrying.set(true);
    this.load();
  }

  private load(): void {
    const accountId = this.accountId();
    const id = ++this.requestId;
    this.loading.set(true);
    this.statements.set([]);
    this.statementsApi.list(accountId).subscribe({
      next: (rows) => {
        if (id !== this.requestId) {
          return;
        }
        this.statements.set(rows);
        this.loading.set(false);
        this.loadError.set(false);
        this.endRetry(true);
      },
      error: () => {
        if (id !== this.requestId) {
          return;
        }
        this.loading.set(false);
        this.loadError.set(true);
        this.endRetry(false);
      },
    });
  }
}
