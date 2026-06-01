import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

@Component({
  selector: 'app-file-chip',
  template: `
    <div class="file-chip">
      <span class="fi" aria-hidden="true"><i class="pi pi-file"></i></span>
      <div class="file-meta">
        <div class="fn">{{ filename() }}</div>
        <div class="fm">已解析 {{ parsedCount() }} 筆</div>
      </div>
      <button type="button" aria-label="移除檔案" (click)="remove.emit()">
        <i class="pi pi-times" aria-hidden="true"></i>
      </button>
    </div>
  `,
  styleUrl: './file-chip.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FileChipComponent {
  readonly filename = input('');
  readonly parsedCount = input(0);
  readonly remove = output<void>();
}
