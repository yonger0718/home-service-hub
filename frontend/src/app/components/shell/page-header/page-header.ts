import { DOCUMENT, Location, NgTemplateOutlet } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  Renderer2,
  afterNextRender,
  computed,
  effect,
  inject,
  input,
  signal,
  viewChildren,
  viewChild,
} from '@angular/core';
import { Params, Router, RouterLink } from '@angular/router';

import { LayoutModeService } from '../../../services/layout-mode.service';
import { isHandledKey } from '../../accounting/accounting-ui';
import { NavigationStateService } from '../navigation-state.service';
import { SubNavComponent } from '../sub-nav/sub-nav';

export interface PageMenuItem {
  label: string;
  icon?: string;
  routerLink?: string | readonly unknown[];
  queryParams?: Params;
  run?: () => void;
  disabled?: boolean;
}

let nextMenuId = 0;

/**
 * Page title row (`<h1>` with an optional subtitle, projected page actions, phone gear) with the phone sub-nav below. With `back`, phones get a
 * ‹ button before the title (a pushed screen such as 設定).
 */
@Component({
  selector: 'app-page-header',
  imports: [NgTemplateOutlet, RouterLink, SubNavComponent],
  templateUrl: './page-header.html',
  styleUrl: './page-header.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class PageHeaderComponent {
  readonly title = input<string>();
  /** Phones only: a ‹ 返回 button before the title. */
  readonly back = input(false);
  /** Muted line under the h1 on every width (e.g. the dashboard's quote time). */
  readonly subtitle = input<string | null>(null);

  /** Phones only: secondary actions behind a ⋯ button after the projected actions. */
  readonly menu = input<PageMenuItem[]>([]);

  protected readonly menuOpen = signal(false);
  protected readonly menuId = `page-menu-${++nextMenuId}`;
  private readonly more = viewChild<ElementRef<HTMLButtonElement>>('more');
  private readonly menuItemEls = viewChildren<ElementRef<HTMLElement>>('menuItem');
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly renderer = inject(Renderer2);
  private readonly document = inject(DOCUMENT);
  private readonly injector = inject(Injector);
  /** A press is in progress inside the menu: Safari blurs a tapped button with relatedTarget null before click. */
  private pressing = false;
  private unlistenPointerDown: (() => void) | null = null;
  private unlistenPointerUp: (() => void) | null = null;

  protected readonly nav = inject(NavigationStateService);
  private readonly layout = inject(LayoutModeService);
  private readonly router = inject(Router);
  private readonly location = inject(Location);

  protected readonly heading = computed(() => this.title() ?? this.nav.activeItem().title);
  protected readonly phone = computed(() => this.layout.mode() === 'phone');
  /** No gear on 設定 itself: it would link to the page being viewed. */
  protected readonly gear = computed(
    () => this.phone() && this.nav.tabBar().settingsInHeader && this.nav.activeItem().id !== 'settings',
  );

  /**
   * True when this page was not reached by the router's first successful navigation, so the history entry behind it
   * belongs to the app (as `leavePage` in accounting-ui). Never `history.length`: a bookmark opened in a tab with
   * history would leave the app.
   */
  private readonly hasAppHistory = computed(() => !!this.router.lastSuccessfulNavigation()?.previousNavigation);
  /** Hidden when its fallback would be this very page (every hideable group hidden, so the landing path is 設定). */
  protected readonly showBack = computed(
    () =>
      this.back() &&
      this.phone() &&
      (this.hasAppHistory() || this.nav.landingPath() !== this.nav.activeItem().path),
  );

  constructor() {
    inject(DestroyRef).onDestroy(() => this.stopListening());
    effect(() => {
      if (!(this.phone() && this.menu().length)) this.closeMenu(false);
    });
  }

  protected toggleMenu(): void {
    if (this.menuOpen()) {
      this.closeMenu();
      return;
    }
    this.menuOpen.set(true);
    this.unlistenPointerDown = this.renderer.listen(this.document, 'pointerdown', (e: Event) => {
      const target = e.target as Node | null;
      if (!target || !this.host.nativeElement.contains(target)) this.closeMenu(false);
    });
    // A press that starts on an item and ends outside the panel would otherwise leave `pressing` set.
    this.unlistenPointerUp = this.renderer.listen(this.document, 'pointerup', () => (this.pressing = false));
    afterNextRender(() => this.menuItemEls()[0]?.nativeElement.focus({ preventScroll: true }), {
      injector: this.injector,
    });
  }

  protected closeMenu(restoreFocus = true): void {
    if (!this.menuOpen()) return;
    this.stopListening();
    this.pressing = false;
    this.menuOpen.set(false);
    if (restoreFocus) this.more()?.nativeElement.focus({ preventScroll: true });
  }

  protected onMenuKeydown(event: KeyboardEvent): void {
    if (isHandledKey(event)) return;
    const els = this.menuItemEls().map(r => r.nativeElement);
    const at = els.indexOf(this.document.activeElement as HTMLElement);
    let next: number;
    switch (event.key) {
      case 'ArrowDown':
        next = at < 0 ? 0 : (at + 1) % els.length;
        break;
      case 'ArrowUp':
        next = at < 0 ? els.length - 1 : (at - 1 + els.length) % els.length;
        break;
      case 'Home':
        next = 0;
        break;
      case 'End':
        next = els.length - 1;
        break;
      case 'Escape':
        event.preventDefault();
        this.closeMenu();
        return;
      default:
        return;
    }
    event.preventDefault();
    els[next]?.focus({ preventScroll: true });
  }

  protected runItem(item: PageMenuItem): void {
    if (item.disabled) return;
    item.run?.();
    this.closeMenu();
  }

  protected onMenuPress(down: boolean): void {
    this.pressing = down;
  }

  protected onMenuFocusout(event: FocusEvent): void {
    if (this.pressing) return;
    const next = event.relatedTarget as Node | null;
    const wrap = this.host.nativeElement.querySelector('.header-more-wrap');
    if (!next || !wrap?.contains(next)) this.closeMenu(false);
  }

  private stopListening(): void {
    this.unlistenPointerDown?.();
    this.unlistenPointerDown = null;
    this.unlistenPointerUp?.();
    this.unlistenPointerUp = null;
  }

  protected goBack(): void {
    if (this.hasAppHistory()) {
      this.location.back();
    } else {
      void this.router.navigateByUrl(this.nav.landingPath());
    }
  }
}
