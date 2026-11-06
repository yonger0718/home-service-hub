import {
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  Injector,
  OnInit,
  inject,
  input,
  output,
} from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { catchError, map, of, startWith, switchMap } from 'rxjs';

import { focusSheetField, isHandledKey, restoreOverlayFocus, trapFocus } from '../../accounting-ui';
import { slashDate } from '../../dates';
import { formatMoney } from '../../format';
import { EinvoiceLinkTarget, EinvoiceTarget, InboxEinvoice } from '../einvoice.model';
import { EinvoiceService } from '../einvoice.service';

/** The body that links an invoice to this target. */
export function targetBody(target: EinvoiceTarget): EinvoiceLinkTarget {
  return target.kind === 'group' ? { group_id: target.id } : { entry_id: target.id };
}

/** account · date · amount · category · name, skipping empty parts. */
export function candidateLabel(target: EinvoiceTarget): string {
  return [
    target.account_name,
    slashDate(target.date),
    formatMoney(target.amount, target.currency),
    target.category,
    target.name,
  ]
    .filter((part): part is string => !!part)
    .join(' · ');
}

type TargetsState =
  | { status: 'loading' }
  | { status: 'error' }
  | { status: 'ready'; rows: { target: EinvoiceTarget; label: string }[] };

/** 選帳…: the entries and split groups near the invoice, one tap to pick. The section turns `picked` into the link. */
@Component({
  selector: 'app-einvoice-target-sheet',
  standalone: true,
  templateUrl: './einvoice-target-sheet.html',
  styleUrls: ['../../sheet.scss', './einvoice-target-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class AppEinvoiceTargetSheetComponent implements OnInit {
  private readonly service = inject(EinvoiceService);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private opener: HTMLElement | null = null;

  readonly invoice = input.required<InboxEinvoice>();
  readonly picked = output<EinvoiceLinkTarget>();
  readonly closed = output<void>();

  readonly state = toSignal(
    toObservable(this.invoice).pipe(
      switchMap((invoice) =>
        this.service.targets(invoice.id).pipe(
          map((targets): TargetsState => ({
            status: 'ready',
            rows: targets.map((target) => ({ target, label: candidateLabel(target) })),
          })),
          catchError(() => of<TargetsState>({ status: 'error' })),
          startWith<TargetsState>({ status: 'loading' }),
        ),
      ),
    ),
    { initialValue: { status: 'loading' } as TargetsState },
  );

  readonly rows = computed(() => {
    const state = this.state();
    return state.status === 'ready' ? state.rows : null;
  });

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    focusSheetField(this.host.nativeElement, '.sheet', this.injector);
  }

  pick(target: EinvoiceTarget): void {
    this.picked.emit(targetBody(target));
  }

  cancel(): void {
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

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
}
