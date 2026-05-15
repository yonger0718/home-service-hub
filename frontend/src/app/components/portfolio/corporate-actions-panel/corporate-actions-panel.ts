import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { catchError, of } from 'rxjs';

import { CardModule } from 'primeng/card';
import { TableModule } from 'primeng/table';
import { TagModule } from 'primeng/tag';
import { SkeletonModule } from 'primeng/skeleton';

import { CorporateAction } from '../../../models/portfolio.model';
import { PortfolioService } from '../../../services/portfolio.service';

@Component({
  selector: 'app-corporate-actions-panel',
  standalone: true,
  imports: [CommonModule, CardModule, TableModule, TagModule, SkeletonModule],
  templateUrl: './corporate-actions-panel.html',
  styleUrl: './corporate-actions-panel.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CorporateActionsPanelComponent implements OnInit {
  private portfolioService = inject(PortfolioService);
  private destroyRef = inject(DestroyRef);

  readonly actions = signal<CorporateAction[]>([]);
  readonly loading = signal<boolean>(false);

  ngOnInit(): void {
    this.loading.set(true);
    this.portfolioService
      .getCorporateActions()
      .pipe(
        catchError(error => {
          console.error('Failed to load corporate actions', error);
          return of<CorporateAction[]>([]);
        }),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(rows => {
        this.actions.set(rows);
        this.loading.set(false);
      });
  }
}
