import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  Injector,
  OnInit,
  computed,
  inject,
  input,
  linkedSignal,
  output,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

import { CategoryNode, Project } from '../../../../models/accounting.model';
import { AccountingService } from '../../../../services/accounting.service';
import { focusSheetField, isHandledKey, restoreOverlayFocus, trapFocus } from '../../accounting-ui';
import { CategoryPickerComponent } from '../../category-picker/category-picker';
import {
  AMOUNT_ANY_LABEL,
  InboxCard,
  cardTitle,
  inboxLearn,
  learnLabel,
  learnableAnswer,
  lineKind,
} from '../inbox-view';
import { InboxLearn } from '../inbox.model';

export interface InboxRecordResult {
  category_id: number;
  category_name: string;
  name: string | null;
  project_id: number | null;
  learn: InboxLearn;
}

/** The node with this id anywhere in the tree (main categories and their children). */
function findCategory(nodes: CategoryNode[], id: number): CategoryNode | null {
  for (const node of nodes) {
    if (node.id === id) {
      return node;
    }
    const child = findCategory(node.children, id);
    if (child) {
      return child;
    }
  }
  return null;
}

/**
 * 記為支出／收入 (spec §7.2): a category for the line's kind (preset from the rule hint), an optional name and project,
 * and the same 以後都這樣 tick as the card. The panel turns `confirmed` into the `/inbox/apply` request.
 */
@Component({
  selector: 'app-inbox-record-sheet',
  standalone: true,
  imports: [CategoryPickerComponent],
  templateUrl: './record-sheet.html',
  styleUrls: ['../../sheet.scss', './record-sheet.scss'],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: { '(keydown)': 'onHostKeydown($event)' },
})
export class AppInboxRecordSheetComponent implements OnInit {
  private readonly accounting = inject(AccountingService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);
  private readonly injector = inject(Injector);
  private opener: HTMLElement | null = null;

  readonly card = input.required<InboxCard>();
  readonly currency = input.required<string>();
  /** The card's own 以後都這樣 / 金額不同也算 ticks, so a tick set before 記為… carries over; null → the default. */
  readonly learnDefault = input<boolean | null>(null);
  readonly amountAnyDefault = input(false);
  readonly confirmed = output<InboxRecordResult>();
  readonly closed = output<void>();

  readonly amountAnyLabel = AMOUNT_ANY_LABEL;
  readonly kind = computed(() => lineKind(this.card().items[0]));
  readonly title = computed(() => (this.kind() === 'expense' ? '記為支出' : '記為收入'));
  readonly subtitle = computed(() => cardTitle(this.card(), this.currency()));
  readonly merchant = computed(() => this.card().items[0].line?.merchant ?? '');
  readonly merged = computed(() => this.card().items.length > 1);

  readonly categories = signal<CategoryNode[]>([]);
  readonly projects = signal<Project[]>([]);
  readonly category = signal<CategoryNode | null>(null);
  readonly name = signal('');
  readonly projectId = signal<number | null>(null);

  /** 以後都這樣 when the card's rule hint can learn a 記為 answer (spec §6.1); else no controls and `learn` null. */
  readonly learn = computed(() => {
    const card = this.card();
    return card.rule_hint !== null && learnableAnswer(card.question, 'record')
      ? {
          label: learnLabel(card.rule_hint, 'record'),
          amountOption: !this.merged(),
          lockedCaption: this.merged() ? `同金額 ×${card.items.length}` : null,
        }
      : null;
  });
  /** As on the card: ticked by default only on a merged card, whose members share the amount. */
  readonly learnTicked = linkedSignal(() => this.learnDefault() ?? this.merged());
  readonly amountAny = linkedSignal(() => this.amountAnyDefault());

  ngOnInit(): void {
    this.opener = this.host.nativeElement.ownerDocument.activeElement as HTMLElement | null;
    focusSheetField(this.host.nativeElement, '.sheet', this.injector);
    this.accounting
      .getCategories(this.kind())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((nodes) => {
        this.categories.set(nodes);
        const preset = this.card().rule_hint?.default_category_id ?? null;
        if (preset !== null && this.category() === null) {
          this.category.set(findCategory(nodes, preset));
        }
      });
    this.accounting
      .getProjects()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((projects) =>
        this.projects.set(projects.filter((project) => !project.is_archived)),
      );
  }

  setProject(value: string): void {
    this.projectId.set(value === '' ? null : Number(value));
  }

  setLearn(ticked: boolean): void {
    this.learnTicked.set(ticked);
  }

  confirm(): void {
    const category = this.category();
    if (!category) {
      return;
    }
    const name = this.name().trim();
    this.confirmed.emit({
      category_id: category.id,
      category_name: category.name,
      name: name === '' ? null : name,
      project_id: this.projectId(),
      learn: this.learn()
        ? inboxLearn(this.learnTicked(), !this.merged() && this.amountAny(), 'record')
        : null,
    });
  }

  cancel(): void {
    restoreOverlayFocus(this.opener, this.host.nativeElement);
    this.closed.emit();
  }

  /** Overlay contract: Esc closes only this sheet (the picker's own Esc is already handled); Tab stays inside. */
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
