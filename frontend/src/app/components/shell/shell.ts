import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet, NavigationEnd } from '@angular/router';
import { filter } from 'rxjs';

import { DockComponent } from '../dock/dock';
import { MobileNavComponent } from '../mobile-nav/mobile-nav';
import { navItemForUrl } from './navigation';

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, DockComponent, MobileNavComponent],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ShellComponent {
  private readonly router = inject(Router);
  private readonly currentUrl = signal(this.router.url || '/');

  protected readonly activeItem = computed(() => navItemForUrl(this.currentUrl()));
  protected readonly title = computed(() => this.activeItem().title);

  constructor() {
    this.router.events
      .pipe(filter((event): event is NavigationEnd => event instanceof NavigationEnd))
      .subscribe(event => this.currentUrl.set(event.urlAfterRedirects));
  }
}
