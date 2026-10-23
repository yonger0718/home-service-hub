import { ChangeDetectionStrategy, Component } from '@angular/core';

import { PageHeaderComponent } from '../shell/page-header/page-header';

@Component({
  selector: 'app-shopping-list',
  imports: [PageHeaderComponent],
  templateUrl: './shopping-list.html',
  styleUrl: './shopping-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShoppingListComponent {}
