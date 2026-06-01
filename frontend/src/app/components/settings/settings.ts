import { ChangeDetectionStrategy, Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';

import { AppearanceService, GainLossConvention } from '../../services/appearance.service';
import { SegToggleComponent, SegToggleOption } from '../ui/seg-toggle/seg-toggle';

@Component({
  selector: 'app-settings',
  imports: [SegToggleComponent],
  template: `
    <section class="settings-page">
      <div class="page-head">
        <h2 class="section-title">設定</h2>
      </div>

      <div class="set-section">
        <div class="set-section-title">外觀</div>
        <div class="set-card">
          <div class="set-row">
            <div class="set-label">
              <i class="pi" [class]="dark() ? 'pi-moon' : 'pi-sun'" aria-hidden="true"></i>
              <div>
                <div class="t">外觀模式</div>
                <div class="d">選擇淺色或深色主題</div>
              </div>
            </div>
            <app-seg-toggle
              ariaLabel="外觀模式"
              [options]="darkOptions"
              [value]="dark() ? 'dark' : 'light'"
              (change)="setDark($event)"
            ></app-seg-toggle>
          </div>
        </div>
      </div>

      <div class="set-section">
        <div class="set-section-title">投資顯示</div>
        <div class="set-card">
          <div class="set-row">
            <div class="set-label">
              <i class="pi pi-chart-line" aria-hidden="true"></i>
              <div>
                <div class="t">漲跌顏色</div>
                <div class="d">選擇符合你習慣的市場慣例</div>
              </div>
            </div>
            <app-seg-toggle
              ariaLabel="漲跌顏色"
              [options]="gainLossOptions"
              [value]="gainLoss()"
              (change)="setGainLoss($event)"
            ></app-seg-toggle>
          </div>

          <div class="set-row preview">
            <span class="set-sublabel">即時預覽</span>
            <div class="prev-chips">
              <span class="pill-preview pos"><i class="pi pi-arrow-up" aria-hidden="true"></i>上漲 +2.35%</span>
              <span class="pill-preview neg"><i class="pi pi-arrow-down" aria-hidden="true"></i>下跌 -1.20%</span>
            </div>
          </div>
        </div>
        <p class="caption">台股以紅色代表上漲、綠色代表下跌；歐美市場則相反。預設為台股慣例。</p>
      </div>
    </section>
  `,
  styleUrl: './settings.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SettingsComponent {
  private readonly appearance = inject(AppearanceService);

  protected readonly dark = toSignal(this.appearance.dark$, { initialValue: this.appearance.dark$.value });
  protected readonly gainLoss = toSignal(this.appearance.gainLoss$, { initialValue: this.appearance.gainLoss$.value });

  protected readonly darkOptions: SegToggleOption[] = [
    { value: 'light', label: '淺色' },
    { value: 'dark', label: '深色' },
  ];

  protected readonly gainLossOptions: SegToggleOption[] = [
    { value: 'asian', label: '紅漲綠跌' },
    { value: 'western', label: '綠漲紅跌' },
  ];

  protected setDark(value: string): void {
    this.appearance.setDark(value === 'dark');
  }

  protected setGainLoss(value: string): void {
    this.appearance.setGainLoss(value as GainLossConvention);
  }
}
