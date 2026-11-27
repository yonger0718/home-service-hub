import { ChangeDetectionStrategy, Component, computed, inject, output } from '@angular/core';

import { LayoutModeService } from '../../../services/layout-mode.service';

/**
 * The e-invoice flow (D10) at `/notifications/einvoices`: one ambiguous / near invoice at a time, beside the list from
 * 1024 px, full screen below, in the review flow's frame (its header, progress and answer styles).
 */
@Component({
  selector: 'app-einvoice-flow',
  standalone: true,
  templateUrl: './einvoice-flow.html',
  styleUrls: ['../recon-flow/recon-flow.scss', './einvoice-flow.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {
    role: 'region',
    'aria-label': '電子發票',
    tabindex: '-1',
    '[class.flow--panes]': 'panes()',
  },
})
export class EinvoiceFlowComponent {
  private readonly layout = inject(LayoutModeService);

  readonly closed = output<void>();

  readonly panes = computed(() => this.layout.mode() === 'panes');
}
