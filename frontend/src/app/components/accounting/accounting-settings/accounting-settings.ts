import { ChangeDetectionStrategy, Component, OnInit, computed, inject, signal } from '@angular/core';
import { DatePipe, DecimalPipe } from '@angular/common';
import { Observable, forkJoin } from 'rxjs';

import {
  AccountGroup,
  CategoryNode,
  Counterparty,
  ENTRY_KIND_LABELS,
  EntryKind,
  ImportRun,
  Preference,
  Project,
  ScheduleItem,
} from '../../../models/accounting.model';
import { AccountingService } from '../../../services/accounting.service';
import { slashDate } from '../dates';
import { formatMoney } from '../format';
import { writeErrorMessage } from '../http-errors';
import { ReportView, summarizeReport } from './import-report';

export type CategoryTab = 'expense' | 'income' | 'transfer_out' | 'receivable' | 'payable';

export const CATEGORY_TABS: [CategoryTab, string][] = [
  ['expense', '支出'],
  ['income', '收入'],
  ['transfer_out', '轉帳'],
  ['receivable', '應收'],
  ['payable', '應付'],
];

const SCHEDULE_LABELS: Record<string, string> = { period: '週期', installment: '分期', skipped_record: '未來記錄' };
const WEEKDAYS = ['星期日', '星期一', '星期二', '星期三', '星期四', '星期五', '星期六'];

function swap<T>(list: T[], index: number, delta: number): T[] | null {
  const target = index + delta;
  if (target < 0 || target >= list.length) {
    return null;
  }
  const copy = [...list];
  [copy[index], copy[target]] = [copy[target], copy[index]];
  return copy;
}

@Component({
  selector: 'app-accounting-settings',
  standalone: true,
  imports: [DatePipe, DecimalPipe],
  templateUrl: './accounting-settings.html',
  styleUrl: './accounting-settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountingSettingsComponent implements OnInit {
  private service = inject(AccountingService);

  readonly preference = signal<Preference | null>(null);
  readonly groups = signal<AccountGroup[]>([]);
  readonly categoryKind = signal<CategoryTab>('expense');
  readonly categories = signal<CategoryNode[]>([]);
  readonly projects = signal<Project[]>([]);
  readonly counterparties = signal<Counterparty[]>([]);
  readonly cpFilter = signal('');
  readonly latest = signal<ImportRun | null>(null);
  readonly schedules = signal<ScheduleItem[]>([]);
  readonly dataMessage = signal<string | null>(null);

  readonly newGroup = signal('');
  readonly newCategory = signal('');
  readonly newSub = signal<Record<number, string>>({});
  readonly newProject = signal('');
  readonly newCounterparty = signal('');

  readonly file = signal<File | null>(null);
  readonly dryRunFile = signal<File | null>(null);
  readonly report = signal<ReportView | null>(null);
  readonly reportMode = signal<'dry' | 'real' | null>(null);
  readonly importing = signal(false);
  readonly importError = signal<string | null>(null);

  readonly categoryTabs = CATEGORY_TABS;
  readonly weekdays = WEEKDAYS;
  readonly scheduleLabels = SCHEDULE_LABELS;
  readonly formatMoney = formatMoney;
  readonly slashDate = slashDate;

  readonly visibleCounterparties = computed(() => {
    const filter = this.cpFilter().trim().toLowerCase();
    return filter ? this.counterparties().filter(cp => cp.name.toLowerCase().includes(filter)) : this.counterparties();
  });
  readonly subCount = computed(() => this.categories().reduce((sum, node) => sum + (node.children?.length ?? 0), 0));
  readonly canImport = computed(() => !!this.file() && this.file() === this.dryRunFile() && !this.importing());

  ngOnInit(): void {
    this.service.getPreference().subscribe(preference => this.preference.set(preference));
    this.loadGroups();
    this.loadCategories();
    this.loadProjects();
    this.loadCounterparties();
    this.loadImportState();
  }

  private loadGroups(): void {
    this.service.getAccountGroups().subscribe(groups => this.groups.set(groups));
  }

  private categoryRequest = 0;

  /** Only the response for the latest request is applied, so a quick tab switch never shows another kind's tree. */
  private loadCategories(): void {
    const request = ++this.categoryRequest;
    this.service.getCategories(this.categoryKind()).subscribe(tree => {
      if (request === this.categoryRequest) {
        this.categories.set(tree);
      }
    });
  }

  private loadProjects(): void {
    this.service.getProjects().subscribe(projects => this.projects.set(projects));
  }

  private loadCounterparties(): void {
    this.service.getCounterparties().subscribe(list => this.counterparties.set(list));
  }

  private loadImportState(): void {
    this.service.getLatestImport().subscribe({ next: run => this.latest.set(run), error: () => this.latest.set(null) });
    this.service.getSchedules().subscribe({ next: items => this.schedules.set(items), error: () => this.schedules.set([]) });
  }

  /** Runs a data-list write, then reloads; 409 shows `conflict`, anything else the server message. */
  private write(request: Observable<unknown>, reload: () => void, conflict: string): void {
    this.dataMessage.set(null);
    request.subscribe({
      next: () => reload(),
      error: err => this.dataMessage.set((err as { status?: number })?.status === 409 ? conflict : writeErrorMessage(err)),
    });
  }

  /** ⏎ in an add field; skips handled events and ⏎ that commits an IME candidate (Zhuyin: `isComposing` / keyCode 229). */
  enterPressed(event: KeyboardEvent): boolean {
    if (event.defaultPrevented || event.isComposing || event.keyCode === 229) {
      return false;
    }
    event.preventDefault();
    return true;
  }

  // 帳戶分組
  renameGroup(group: AccountGroup, name: string): void {
    if (name.trim() && name.trim() !== group.name) {
      this.write(this.service.updateAccountGroup(group.id, { name: name.trim(), sort_order: group.sort_order }), () => this.loadGroups(), '名稱重複');
    }
  }

  moveGroup(index: number, delta: number): void {
    const reordered = swap(this.groups(), index, delta);
    if (reordered) {
      this.write(this.service.reorderAccountGroups(reordered.map(group => group.id)), () => this.loadGroups(), '無法排序');
    }
  }

  deleteGroup(group: AccountGroup): void {
    this.write(this.service.deleteAccountGroup(group.id), () => this.loadGroups(), `「${group.name}」仍有帳戶，無法刪除`);
  }

  addGroup(): void {
    const name = this.newGroup().trim();
    if (name) {
      this.newGroup.set('');
      this.write(this.service.createAccountGroup({ name, sort_order: this.groups().length }), () => this.loadGroups(), '名稱重複');
    }
  }

  // 類別
  setCategoryKind(kind: CategoryTab): void {
    this.categoryKind.set(kind);
    this.loadCategories();
  }

  saveCategory(node: CategoryNode, patch: Partial<Pick<CategoryNode, 'name' | 'icon' | 'color' | 'is_hidden'>>): void {
    const body = {
      kind: node.kind,
      parent_id: node.parent_id,
      name: node.name,
      icon: node.icon,
      color: node.color,
      sort_order: node.sort_order,
      is_hidden: node.is_hidden,
      ...patch,
    };
    if (body.name.trim() === '') {
      return;
    }
    this.write(this.service.updateCategory(node.id, body), () => this.loadCategories(), '同層已有相同名稱');
  }

  moveCategory(siblings: CategoryNode[], index: number, delta: number): void {
    const reordered = swap(siblings, index, delta);
    if (reordered) {
      this.write(this.service.reorderCategories(reordered.map(node => node.id)), () => this.loadCategories(), '無法排序');
    }
  }

  deleteCategory(node: CategoryNode): void {
    this.write(this.service.deleteCategory(node.id), () => this.loadCategories(), `「${node.name}」仍有記錄使用，無法刪除；可改為隱藏`);
  }

  addCategory(parent: CategoryNode | null): void {
    const name = (parent ? this.newSub()[parent.id] ?? '' : this.newCategory()).trim();
    if (!name) {
      return;
    }
    if (parent) {
      this.newSub.update(current => ({ ...current, [parent.id]: '' }));
    } else {
      this.newCategory.set('');
    }
    const siblings = parent ? parent.children ?? [] : this.categories();
    this.write(
      this.service.createCategory({
        kind: this.categoryKind(),
        parent_id: parent?.id ?? null,
        name,
        icon: null,
        color: null,
        sort_order: siblings.length,
        is_hidden: false,
      }),
      () => this.loadCategories(),
      '同層已有相同名稱',
    );
  }

  setNewSub(parentId: number, value: string): void {
    this.newSub.update(current => ({ ...current, [parentId]: value }));
  }

  // 專案
  saveProject(project: Project, patch: Partial<Pick<Project, 'name' | 'is_archived' | 'sort_order'>>): void {
    const body = { name: project.name, is_archived: project.is_archived, sort_order: project.sort_order, ...patch };
    if (body.name.trim()) {
      this.write(this.service.updateProject(project.id, body), () => this.loadProjects(), '名稱重複');
    }
  }

  moveProject(index: number, delta: number): void {
    const list = this.projects();
    const other = list[index + delta];
    const current = list[index];
    if (!other || !current) {
      return;
    }
    const [a, b] = current.sort_order === other.sort_order ? [index + delta, index] : [other.sort_order, current.sort_order];
    this.write(
      forkJoin([
        this.service.updateProject(current.id, { name: current.name, is_archived: current.is_archived, sort_order: a }),
        this.service.updateProject(other.id, { name: other.name, is_archived: other.is_archived, sort_order: b }),
      ]),
      () => this.loadProjects(),
      '無法排序',
    );
  }

  deleteProject(project: Project): void {
    this.write(this.service.deleteProject(project.id), () => this.loadProjects(), `「${project.name}」仍有記錄使用，無法刪除；可改為封存`);
  }

  addProject(): void {
    const name = this.newProject().trim();
    if (name) {
      this.newProject.set('');
      this.write(this.service.createProject({ name, is_archived: false, sort_order: this.projects().length }), () => this.loadProjects(), '名稱重複');
    }
  }

  // 對象
  renameCounterparty(counterparty: Counterparty, name: string): void {
    if (name.trim() && name.trim() !== counterparty.name) {
      this.write(this.service.updateCounterparty(counterparty.id, { name: name.trim() }), () => this.loadCounterparties(), '名稱重複');
    }
  }

  deleteCounterparty(counterparty: Counterparty): void {
    this.write(this.service.deleteCounterparty(counterparty.id), () => this.loadCounterparties(), `「${counterparty.name}」仍有記錄使用，無法刪除`);
  }

  addCounterparty(): void {
    const name = this.newCounterparty().trim();
    if (name) {
      this.newCounterparty.set('');
      this.write(this.service.createCounterparty({ name }), () => this.loadCounterparties(), '名稱重複');
    }
  }

  openAmounts(counterparty: Counterparty): string {
    return counterparty.open_amounts.map(item => formatMoney(item.amount, item.currency)).join(' · ');
  }

  // 顯示
  setPreference<K extends keyof Preference>(key: K, value: Preference[K]): void {
    const current = this.preference();
    if (!current) {
      return;
    }
    this.service.updatePreference({ ...current, [key]: value }).subscribe({
      next: saved => this.preference.set(saved),
      error: err => this.dataMessage.set(writeErrorMessage(err)),
    });
  }

  // 匯入
  onFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.file.set(input.files?.[0] ?? null);
    this.dryRunFile.set(null);
    this.report.set(null);
    this.reportMode.set(null);
    this.importError.set(null);
  }

  dryRun(): void {
    const file = this.file();
    if (!file) {
      return;
    }
    this.runImport(file, true);
  }

  realRun(): void {
    const file = this.file();
    if (!file || !this.canImport()) {
      return;
    }
    this.runImport(file, false);
  }

  private runImport(file: File, dryRun: boolean): void {
    this.importing.set(true);
    this.importError.set(null);
    this.service.importBackup(file, { dryRun, strict: true }).subscribe({
      next: report => {
        this.importing.set(false);
        if (dryRun && this.file() !== file) {
          return; // another file was chosen while the dry run was running; its result no longer applies
        }
        this.report.set(summarizeReport(report));
        this.reportMode.set(dryRun ? 'dry' : 'real');
        if (dryRun) {
          this.dryRunFile.set(file);
        } else {
          this.dryRunFile.set(null);
          this.loadImportState();
        }
      },
      error: err => {
        this.importing.set(false);
        this.importError.set(writeErrorMessage(err));
      },
    });
  }

  kindLabel(kind: string): string {
    return ENTRY_KIND_LABELS[kind as EntryKind] ?? kind;
  }
}
