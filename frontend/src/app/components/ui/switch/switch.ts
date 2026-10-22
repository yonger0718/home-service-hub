import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'app-switch',
  template: `
    <button
      type="button"
      class="switch"
      role="switch"
      [class.on]="checked()"
      [attr.aria-checked]="checked()"
      [attr.aria-label]="ariaLabel()"
      [disabled]="disabled()"
      (click)="toggle()"
    >
      <span class="thumb"></span>
    </button>
  `,
  styleUrl: './switch.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SwitchComponent {
  readonly checked = input(false);
  readonly disabled = input(false);
  readonly ariaLabel = input.required<string>();
  readonly checkedChange = output<boolean>();

  protected toggle(): void {
    if (this.disabled()) return;
    this.checkedChange.emit(!this.checked());
  }
}
