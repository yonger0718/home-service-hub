import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  OnInit,
  inject,
  input,
  linkedSignal,
  output,
} from '@angular/core';

import { focusSheetField, isHandledKey, restoreOverlayFocus, trapFocus } from '../../../accounting/accounting-ui';
import { SheetPullDirective } from '../../../accounting/sheet-pull.directive';

/** One choice in the picker: the id the answer carries, and the text shown. */
export interface FlowPickOption {
  id: number;
  label: string;
}

/**
 * A small picker sheet for the review flow: radio rows, 確定 once one is picked (a single option is pre-picked).
 * Same overlay contract as the inbox match sheet: Esc closes only the sheet, Tab stays inside.
 */
@Component({
  selector: 'app-flow-pick-sheet',
  standalone: true,
  imports: [SheetPullDirective],
  templateUrl: './pick-sheet.html',
  styleUrls: ['../../../accounting/sheet.scss', './pick-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class AppFlowPickSheetComponent implements OnInit {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private opener: HTMLElement | null = null;

  readonly heading = input.required<string>();
  readonly sub = input<string | null>(null);
  readonly options = input.required<readonly FlowPickOption[]>();
  readonly picked = output<number>();
  readonly closed = output<void>();

  /** The chosen id; starts over when the options change (one option is chosen for the user). */
  readonly choice = linkedSignal<readonly FlowPickOption[], number | null>({
    source: this.options,
    computation: (options) => (options.length === 1 ? options[0].id : null),
  });

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    focusSheetField(this.host.nativeElement, '.sheet', this.injector);
  }

  confirm(): void {
    const id = this.choice();
    if (id !== null) {
      this.picked.emit(id);
    }
  }

  cancel(): void {
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  /** Overlay contract: Esc closes only this sheet; Tab stays inside. */
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
