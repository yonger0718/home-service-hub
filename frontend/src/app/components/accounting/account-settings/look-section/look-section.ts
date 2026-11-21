import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal, untracked } from '@angular/core';

import { ACCOUNT_COLOR_SWATCHES } from '../../../../theme/account-colors';
import { ACCOUNT_LOGOS, accountLogo } from '../../../../theme/account-logos';
import { SegToggleComponent, SegToggleOption } from '../../../ui/seg-toggle/seg-toggle';
import { AccountIconComponent } from '../../account-icon/account-icon';
import { LOGO_ACCEPT } from '../../logo-upload-errors';

export type LookSource = 'upload' | 'logo' | 'text';

type RadioGroup = 'logo' | 'color';

/** What the account icon shows, in `app-account-icon`'s precedence: uploaded image > bundled logo > text. */
export function lookSummary(logoHash: string | null, logo: string | null, icon: string): string {
  if (logoHash) return '上傳的圖片';
  if (logo) return `內建 logo · ${accountLogo(logo)?.label ?? '未知 logo'}`;
  return icon ? `文字 ${icon}` : '預設圖示';
}

/** The tab that edits what the icon shows today. */
export function lookSourceOf(logoHash: string | null, logo: string | null): LookSource {
  if (logoHash) return 'upload';
  return logo ? 'logo' : 'text';
}

function sameHex(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}

const SOURCE_OPTIONS: SegToggleOption[] = [
  { value: 'upload', label: '上傳圖片' },
  { value: 'logo', label: '內建 logo' },
  { value: 'text', label: '文字＋顏色' },
];

const ARROW_OFFSET: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/** The account settings 外觀 block: preview, source tabs (upload / bundled logo / text + colour) and their controls.
 *  Presentational: every change is emitted; switching tabs writes nothing. */
@Component({
  selector: 'app-account-look',
  imports: [AccountIconComponent, SegToggleComponent],
  templateUrl: './look-section.html',
  styleUrl: './look-section.scss',
  host: { '[class.stacked]': 'stacked()' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountLookSectionComponent {
  readonly accountId = input<number | null>(null);
  readonly logoHash = input<string | null>(null);
  readonly logo = input<string | null>(null);
  readonly icon = input('');
  readonly color = input('');
  readonly busy = input(false);
  /** One column, the source toggle full-width: the phone sheet. */
  readonly stacked = input(false);

  readonly logoChange = output<string | null>();
  readonly iconChange = output<string>();
  readonly colorChange = output<string>();
  readonly upload = output<File>();
  readonly remove = output<void>();

  protected readonly SOURCE_OPTIONS = SOURCE_OPTIONS;
  protected readonly LOGO_ACCEPT = LOGO_ACCEPT;
  protected readonly logos = ACCOUNT_LOGOS;
  protected readonly swatches = ACCOUNT_COLOR_SWATCHES;

  protected readonly source = signal<LookSource>('text');
  protected readonly dragOver = signal(false);
  protected readonly summary = computed(() => lookSummary(this.logoHash(), this.logo(), this.icon()));
  protected readonly tileBackground = computed(() => (this.logoHash() || this.logo() ? null : this.color() || null));
  protected readonly customActive = computed(
    () => !!this.color() && !ACCOUNT_COLOR_SWATCHES.some((s) => sameHex(s.hex, this.color())),
  );
  protected readonly canUpload = computed(() => this.accountId() !== null && !this.busy());

  /** The checked radio's value per group; undefined when none is checked (an unknown slug, an empty colour). */
  private readonly checkedLogo = computed(() => {
    const logo = this.logo();
    return logo === null || accountLogo(logo) ? logo : undefined;
  });
  private readonly checkedColor = computed(() => {
    const color = this.color();
    if (this.customActive()) return 'custom';
    return ACCOUNT_COLOR_SWATCHES.find((s) => sameHex(s.hex, color))?.hex;
  });

  constructor() {
    // The tab is derived once per account: an upload, a removal or a picked logo must not move it under the user.
    effect(() => {
      this.accountId();
      untracked(() => this.source.set(lookSourceOf(this.logoHash(), this.logo())));
    });
  }

  protected same(a: string, b: string): boolean {
    return sameHex(a, b);
  }

  /** Roving tabindex: the checked radio is the group's one tab stop, else its first radio. */
  protected rovingTab(group: RadioGroup, value: string | null): 0 | -1 {
    const checked = group === 'logo' ? this.checkedLogo() : this.checkedColor();
    const first = group === 'logo' ? null : ACCOUNT_COLOR_SWATCHES[0].hex;
    return value === (checked === undefined ? first : checked) ? 0 : -1;
  }

  /** Arrow keys move to the next / previous radio (wrapping) and select it — the radio's own click handler emits
   *  the value (the custom swatch opens the colour input) — then focus it, as `app-seg-toggle` does. */
  protected onRadioKeydown(event: KeyboardEvent, group: RadioGroup): void {
    const offset = ARROW_OFFSET[event.key];
    if (!offset) return;
    event.preventDefault();
    const radios = Array.from((event.currentTarget as HTMLElement).querySelectorAll<HTMLElement>('[role="radio"]'));
    const current = radios.indexOf((event.target as HTMLElement).closest<HTMLElement>('[role="radio"]')!);
    if (current < 0) return;
    const next = radios[(current + offset + radios.length) % radios.length];
    next.click();
    queueMicrotask(() => next.focus());
  }

  /** Always default-prevented: an un-prevented drop opens the file in the tab. `dropEffect` 'none' refuses the drop
   *  (blocked cursor, no `drop` event) while busy or on a new account. */
  protected onDragOver(event: DragEvent): void {
    event.preventDefault();
    const allowed = this.canUpload();
    if (event.dataTransfer) event.dataTransfer.dropEffect = allowed ? 'copy' : 'none';
    if (allowed) this.dragOver.set(true);
  }

  /** Moving onto the zone's own svg / text / button is not leaving it. */
  protected onDragLeave(event: DragEvent): void {
    const to = event.relatedTarget;
    if (to instanceof Node && (event.currentTarget as HTMLElement).contains(to)) return;
    this.dragOver.set(false);
  }

  /** 移除圖片 disables then removes itself; focus moves to the checked 圖示來源 radio (the group's tab stop) first,
   *  so it never drops to `<body>` — outside the phone sheet's focus trap. */
  protected onRemove(event: Event): void {
    (event.currentTarget as HTMLElement)
      .closest('.look')
      ?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')
      ?.focus();
    this.remove.emit();
  }

  protected onDrop(event: DragEvent): void {
    event.preventDefault();
    this.dragOver.set(false);
    const file = event.dataTransfer?.files[0];
    if (file && this.canUpload()) this.upload.emit(file);
  }

  protected onFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (file) this.upload.emit(file);
  }
}
