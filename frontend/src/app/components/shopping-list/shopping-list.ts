import { ChangeDetectionStrategy, Component } from '@angular/core';

@Component({
  selector: 'app-shopping-list',
  templateUrl: './shopping-list.html',
  styleUrl: './shopping-list.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShoppingListComponent {}
