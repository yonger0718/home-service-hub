import { Injectable, Signal, signal } from '@angular/core';
import { NAV_GROUPS, NavGroupId, SidebarDefault } from '../components/shell/navigation';

const HIDDEN_KEY = 'hh-nav-hidden';
const SIDEBAR_KEY = 'hh-nav-sidebar';
const AUTOCOLLAPSE_KEY = 'hh-nav-autocollapse';
const BADGES_KEY = 'hh-nav-badges';
const FOLDED_KEY = 'hh-nav-folded';

const DEFAULT_HIDDEN: readonly NavGroupId[] = ['supplies'];
const NEVER_HIDDEN: NavGroupId = 'system';

const KNOWN_GROUPS: ReadonlySet<string> = new Set<string>([
  ...NAV_GROUPS.map(group => group.id),
  NEVER_HIDDEN,
]);

@Injectable({ providedIn: 'root' })
export class NavigationPreferencesService {
  private readonly hidden = signal<readonly NavGroupId[]>(this.readGroups(HIDDEN_KEY, DEFAULT_HIDDEN, true));
  private readonly sidebar = signal<SidebarDefault>(this.readSidebar());
  private readonly autoCollapseState = signal<boolean>(this.readBool(AUTOCOLLAPSE_KEY, true));
  private readonly badges = signal<boolean>(this.readBool(BADGES_KEY, true));
  private readonly folded = signal<readonly NavGroupId[]>(this.readGroups(FOLDED_KEY, [], false));

  readonly hiddenGroups: Signal<readonly NavGroupId[]> = this.hidden.asReadonly();
  readonly sidebarDefault: Signal<SidebarDefault> = this.sidebar.asReadonly();
  readonly autoCollapse: Signal<boolean> = this.autoCollapseState.asReadonly();
  readonly showBadges: Signal<boolean> = this.badges.asReadonly();
  readonly foldedGroups: Signal<readonly NavGroupId[]> = this.folded.asReadonly();

  setHidden(group: NavGroupId, hidden: boolean): void {
    if (group === NEVER_HIDDEN) return;
    this.hidden.set(this.withMembership(this.hidden(), group, hidden));
    this.persist(HIDDEN_KEY, JSON.stringify(this.hidden()));
  }

  setSidebarDefault(value: SidebarDefault): void {
    this.sidebar.set(value);
    this.persist(SIDEBAR_KEY, value);
  }

  setAutoCollapse(value: boolean): void {
    this.autoCollapseState.set(value);
    this.persist(AUTOCOLLAPSE_KEY, value ? '1' : '0');
  }

  setShowBadges(value: boolean): void {
    this.badges.set(value);
    this.persist(BADGES_KEY, value ? '1' : '0');
  }

  setFolded(group: NavGroupId, folded: boolean): void {
    this.folded.set(this.withMembership(this.folded(), group, folded));
    this.persist(FOLDED_KEY, JSON.stringify(this.folded()));
  }

  private withMembership(list: readonly NavGroupId[], group: NavGroupId, member: boolean): readonly NavGroupId[] {
    const without = list.filter(id => id !== group);
    return member ? [...without, group] : without;
  }

  private read(key: string): string | null {
    try {
      return this.storage?.getItem(key) ?? null;
    } catch {
      return null;
    }
  }

  private persist(key: string, value: string): void {
    try {
      this.storage?.setItem(key, value);
    } catch {
      // localStorage write failed (quota / blocked); keep in-memory state
    }
  }

  private readBool(key: string, fallback: boolean): boolean {
    const stored = this.read(key);
    if (stored === '1') return true;
    if (stored === '0') return false;
    return fallback;
  }

  private readSidebar(): SidebarDefault {
    return this.read(SIDEBAR_KEY) === 'rail' ? 'rail' : 'expanded';
  }

  private readGroups(key: string, fallback: readonly NavGroupId[], excludeSystem: boolean): readonly NavGroupId[] {
    const raw = this.read(key);
    if (raw === null) return fallback;
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return fallback;
      const groups = parsed.filter(
        (id): id is NavGroupId =>
          typeof id === 'string' && KNOWN_GROUPS.has(id) && !(excludeSystem && id === NEVER_HIDDEN),
      );
      return [...new Set(groups)];
    } catch {
      return fallback;
    }
  }

  private get storage(): Storage | null {
    if (typeof window === 'undefined') return null;
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }
}
