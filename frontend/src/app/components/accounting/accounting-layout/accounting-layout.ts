import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

/** Host of every `/accounting` route. Passthrough until Task 21 adds panes, the sheet and the FAB. */
@Component({
  selector: 'app-accounting-layout',
  standalone: true,
  imports: [RouterOutlet],
  template: `<router-outlet />`,
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingLayoutComponent {}
