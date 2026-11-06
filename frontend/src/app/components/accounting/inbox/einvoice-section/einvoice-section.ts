import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  input,
  linkedSignal,
  output,
  signal,
} from '@angular/core';
import { Observable } from 'rxjs';

import { slashDate } from '../../dates';
import { apiErrorText, splitApiCode } from '../../statements/statement-view';
import { invoiceAmount } from '../einvoice-format';
import { AppEinvoiceTargetSheetComponent, candidateLabel, targetBody } from '../einvoice-target-sheet/einvoice-target-sheet';
import { EinvoiceLinkTarget, EinvoiceTarget, InboxEinvoice } from '../einvoice.model';
import { EinvoiceService } from '../einvoice.service';
import { INBOX_GENERIC_ERROR } from '../inbox-view';

const EINVOICE_ERRORS: Record<string, string> = {
  einvoice_void: '發票已作廢',
  target_ineligible: '這筆帳不能連結',
  not_linked: '已解除連結',
  seller_muted: '這家已設為不提醒',
  retry: '請再試一次',
};

/** `沒成功：…` for a mapped 409 code (`detail` is `<code>` or `<code>: <detail>`), else the generic inbox text. */
export function einvoiceErrorText(err: unknown): string {
  const text = apiErrorText(err);
  const known = text ? EINVOICE_ERRORS[splitApiCode(text).code] : undefined;
  return known ? `沒成功：${known}` : INBOX_GENERIC_ERROR;
}

/** 發票待對帳 (spec §9): the invoices the matcher could not settle, answered in one tap each. */
@Component({
  selector: 'app-einvoice-section',
  standalone: true,
  imports: [AppEinvoiceTargetSheetComponent],
  templateUrl: './einvoice-section.html',
  styleUrls: ['../inbox-panel/inbox-panel.scss', './einvoice-section.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AppEinvoiceSectionComponent {
  private readonly service = inject(EinvoiceService);

  readonly items = input.required<InboxEinvoice[]>();
  readonly mode = input<'full' | 'preview'>('full');
  readonly changed = output<void>();

  /** Invoices answered since the last `items`: they leave at once, before the parent's reload lands. */
  private readonly gone = linkedSignal<InboxEinvoice[], ReadonlySet<number>>({
    source: this.items,
    computation: () => new Set(),
  });
  readonly refused = signal<ReadonlyMap<number, string>>(new Map());
  private readonly busy = signal<ReadonlySet<number>>(new Set());
  readonly sheetFor = signal<InboxEinvoice | null>(null);

  readonly cards = computed(() => {
    const gone = this.gone();
    const preview = this.mode() === 'preview';
    return this.items()
      .filter((item) => !gone.has(item.id))
      .filter((item) => !preview || item.state !== 'unmatched')
      .map((item) => ({
        item,
        date: slashDate(item.issue_date),
        total: invoiceAmount(item.total_amount),
        question:
          item.state === 'ambiguous'
            ? '哪一筆是這張發票？'
            : item.state === 'near'
              ? '金額或商家相近，是這筆嗎？'
              : null,
        candidates: item.candidates.map((target) => ({ target, label: candidateLabel(target) })),
        lines: item.items.map((line) => ({ name: line.name, subtotal: invoiceAmount(line.subtotal) })),
        refused: this.refused().get(item.id) ?? null,
        busy: this.busy().has(item.id),
      }));
  });

  link(item: InboxEinvoice, body: EinvoiceLinkTarget): void {
    this.sheetFor.set(null);
    this.write(item, this.service.link(item.id, body));
  }

  linkCandidate(item: InboxEinvoice, target: EinvoiceTarget): void {
    this.link(item, targetBody(target));
  }

  dismiss(item: InboxEinvoice, muteSeller = false): void {
    this.write(item, this.service.dismiss(item.id, muteSeller));
  }

  private write(item: InboxEinvoice, call: Observable<unknown>): void {
    if (this.busy().has(item.id)) {
      return;
    }
    this.setBusy(item.id, true);
    this.refused.update((map) => {
      const next = new Map(map);
      next.delete(item.id);
      return next;
    });
    call.subscribe({
      next: () => {
        this.setBusy(item.id, false);
        this.gone.update((set) => new Set([...set, item.id]));
        this.changed.emit();
      },
      error: (err: unknown) => {
        this.setBusy(item.id, false);
        this.refused.update((map) => new Map(map).set(item.id, einvoiceErrorText(err)));
      },
    });
  }

  private setBusy(id: number, on: boolean): void {
    this.busy.update((set) => {
      const next = new Set(set);
      if (on) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }
}
