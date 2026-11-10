import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ACCOUNT_LOGOS } from '../../../../theme/account-logos';
import { AccountLookSectionComponent, lookSourceOf, lookSummary } from './look-section';

type Inputs = Partial<{
  accountId: number | null;
  logoHash: string | null;
  logo: string | null;
  icon: string;
  color: string;
  busy: boolean;
}>;

const png = () => new Blob([new Uint8Array(1)], { type: 'image/png' });

describe('lookSummary / lookSourceOf', () => {
  it('follows upload > logo > icon precedence', () => {
    expect(lookSummary('h1', 'cathay-united', '💳')).toBe('上傳的圖片');
    expect(lookSummary(null, 'cathay-united', '💳')).toBe('內建 logo · 國泰世華');
    expect(lookSummary(null, 'china-airlines', '💳')).toBe('內建 logo · 未知 logo');
    expect(lookSummary(null, null, 'Pi')).toBe('文字 Pi');
    expect(lookSummary(null, null, '')).toBe('預設圖示');
  });

  it('derives the source tab', () => {
    expect(lookSourceOf('h1', 'cathay-united')).toBe('upload');
    expect(lookSourceOf(null, 'cathay-united')).toBe('logo');
    expect(lookSourceOf(null, null)).toBe('text');
  });
});

describe('AccountLookSectionComponent', () => {
  let http: HttpTestingController;
  let fixture: ComponentFixture<AccountLookSectionComponent>;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideHttpClient(), provideHttpClientTesting()] });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  function render(inputs: Inputs): ComponentFixture<AccountLookSectionComponent> {
    fixture = TestBed.createComponent(AccountLookSectionComponent);
    set(inputs);
    return fixture;
  }

  function set(inputs: Inputs): void {
    for (const [k, v] of Object.entries(inputs)) {
      fixture.componentRef.setInput(k, v);
    }
    fixture.detectChanges();
  }

  /** Flushes the icon's uploaded-image request for `id` / `hash` with a 1-byte PNG. */
  function flushLogo(id: number, hash: string): void {
    http.expectOne(`/api/accounting/accounts/${id}/logo.png?v=${hash}`).flush(png());
    fixture.detectChanges();
  }

  const root = () => fixture.nativeElement as HTMLElement;
  const q = <T extends Element = HTMLElement>(sel: string) => root().querySelector<T>(sel);
  const qa = <T extends Element = HTMLElement>(sel: string) => Array.from(root().querySelectorAll<T>(sel));
  const checkedTab = () => q('.seg-toggle [aria-checked="true"]')!.textContent!.trim();
  const segButton = (label: string) => qa<HTMLButtonElement>('.seg-toggle button').find((b) => b.textContent!.trim() === label)!;
  function openTab(label: string): void {
    segButton(label).click();
    fixture.detectChanges();
  }
  function collect<T>(name: 'logoChange' | 'iconChange' | 'colorChange' | 'upload' | 'remove'): T[] {
    const out: T[] = [];
    (fixture.componentInstance[name] as unknown as { subscribe(fn: (v: T) => void): unknown }).subscribe((v) => out.push(v));
    return out;
  }
  function key(el: HTMLElement, k: string): KeyboardEvent {
    const ev = new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    fixture.detectChanges();
    return ev;
  }
  function dragEvent(type: 'dragover' | 'drop', files: File[] = []): Event {
    const ev = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'dataTransfer', { value: { files } });
    return ev;
  }

  it('opens on 上傳圖片 when the account has an uploaded image, 內建 logo for a slug, 文字＋顏色 otherwise', () => {
    render({ accountId: 5, logoHash: 'h1', logo: 'cathay-united' });
    flushLogo(5, 'h1');
    expect(checkedTab()).toBe('上傳圖片');

    render({ accountId: 5, logo: 'cathay-united' });
    expect(checkedTab()).toBe('內建 logo');

    render({ accountId: 5, icon: 'Pi' });
    expect(checkedTab()).toBe('文字＋顏色');
  });

  it('the open tab follows accountId, not later data changes', () => {
    render({ accountId: 5 });
    expect(checkedTab()).toBe('文字＋顏色');
    set({ logo: 'cathay-united' });
    expect(checkedTab()).toBe('文字＋顏色');
    set({ accountId: 6 });
    expect(checkedTab()).toBe('內建 logo');

    render({ accountId: 5, logoHash: 'h1' });
    flushLogo(5, 'h1');
    expect(checkedTab()).toBe('上傳圖片');
    set({ logoHash: null }); // 移除圖片 finished
    expect(checkedTab()).toBe('上傳圖片');

    render({ accountId: 5, logoHash: null });
    openTab('上傳圖片');
    set({ logoHash: 'h2' }); // an upload finished
    flushLogo(5, 'h2');
    expect(checkedTab()).toBe('上傳圖片');
  });

  it('status line and preview follow precedence', () => {
    render({ accountId: 5, logoHash: 'h1', logo: 'cathay-united', icon: '💳' });
    flushLogo(5, 'h1');
    const expectUpload = () => {
      expect(q('.look-current')!.textContent!.trim()).toBe('目前顯示：上傳的圖片');
      expect(q('.look-tile img')!.getAttribute('src')!.startsWith('blob:')).toBe(true);
    };
    expectUpload();

    qa<HTMLButtonElement>('.seg-toggle button')[2].click();
    fixture.detectChanges();
    expect(checkedTab()).toBe('文字＋顏色');
    expectUpload();

    const icons = collect<string>('iconChange');
    const logos = collect<string | null>('logoChange');
    const colors = collect<string>('colorChange');
    const input = q<HTMLInputElement>('.icon-input')!;
    input.value = '🏦';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    expect(icons).toEqual(['🏦']);
    expect(logos).toEqual([]);
    expect(colors).toEqual([]);
  });

  it('upload tab: 選擇檔案 picks a file and emits upload', () => {
    render({ accountId: 5 });
    openTab('上傳圖片');
    const uploads = collect<File>('upload');
    const input = q<HTMLInputElement>('input[type=file]')!;
    const file = new File([png()], 'mark.png', { type: 'image/png' });
    Object.defineProperty(input, 'files', { configurable: true, value: [file] });
    Object.defineProperty(input, 'value', { configurable: true, writable: true, value: 'C:\\fakepath\\mark.png' });
    input.dispatchEvent(new Event('change'));
    expect(uploads).toHaveLength(1);
    expect(uploads[0]).toBe(file);
    expect(input.value).toBe('');
  });

  it('upload tab: dragover is default-prevented only when a drop can be taken', () => {
    render({ accountId: 5 });
    openTab('上傳圖片');
    let ev = dragEvent('dragover');
    q('.drop')!.dispatchEvent(ev);
    fixture.detectChanges();
    expect(ev.defaultPrevented).toBe(true);
    expect(q('.drop.over')).not.toBeNull();

    q('.drop')!.dispatchEvent(dragEvent('drop'));
    set({ busy: true });
    expect(q('.drop.over')).toBeNull();
    ev = dragEvent('dragover');
    q('.drop')!.dispatchEvent(ev);
    fixture.detectChanges();
    expect(ev.defaultPrevented).toBe(false);
    expect(q('.drop.over')).toBeNull();

    set({ busy: false, accountId: null });
    openTab('上傳圖片');
    ev = dragEvent('dragover');
    q('.drop')!.dispatchEvent(ev);
    fixture.detectChanges();
    expect(ev.defaultPrevented).toBe(false);
    expect(q('.drop.over')).toBeNull();
  });

  it('upload tab: a dropped file emits upload once, an ignored drag does nothing while busy', () => {
    render({ accountId: 5 });
    openTab('上傳圖片');
    const uploads = collect<File>('upload');
    const file = new File([png()], 'mark.png', { type: 'image/png' });
    const drop = dragEvent('drop', [file]);
    q('.drop')!.dispatchEvent(drop);
    fixture.detectChanges();
    expect(drop.defaultPrevented).toBe(true);
    expect(uploads).toEqual([file]);

    set({ busy: true });
    expect(q('.drop.disabled')).not.toBeNull();
    expect(q<HTMLButtonElement>('.logo-upload')!.disabled).toBe(true);
    expect(q('.logo-upload')!.getAttribute('aria-busy')).toBe('true');
    q('.drop')!.dispatchEvent(dragEvent('drop', [file]));
    fixture.detectChanges();
    expect(uploads).toEqual([file]);
  });

  it('new account: default tab 文字＋顏色; 上傳圖片 shows 選擇檔案 disabled, the hint, ignores a drop, no 移除圖片', () => {
    render({ accountId: null });
    expect(checkedTab()).toBe('文字＋顏色');
    openTab('上傳圖片');
    const uploads = collect<File>('upload');
    expect(q('.logo-upload[disabled]')).not.toBeNull();
    expect(q('.logo-upload')!.textContent!.trim()).toBe('選擇檔案');
    expect(q('.logo-hint')!.textContent!.trim()).toBe('先建立帳戶，再上傳 logo');
    expect(q('.drop-text')!.textContent!.trim()).toBe('拖曳圖片到這裡，或');
    expect(q('.look-limits')!.textContent!.trim()).toBe('PNG / JPG / WebP · 5 MB 以內');
    q('.drop')!.dispatchEvent(dragEvent('drop', [new File([png()], 'mark.png', { type: 'image/png' })]));
    fixture.detectChanges();
    expect(uploads).toEqual([]);
    expect(q('.logo-remove')).toBeNull();
  });

  it('the hidden file input is the last input of the upload arm and out of the tab order', () => {
    render({ accountId: 5 });
    openTab('上傳圖片');
    const inputs = qa<HTMLInputElement>('.look-body input');
    const file = inputs[inputs.length - 1];
    expect(file.type).toBe('file');
    expect(file.getAttribute('tabindex')).toBe('-1');
    expect(file.getAttribute('aria-hidden')).toBe('true');
    expect(file.getAttribute('accept')).toBe('image/png,image/jpeg,image/webp');
    expect(file.compareDocumentPosition(q('.logo-upload')!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('移除圖片 only with an uploaded image and emits remove', () => {
    render({ accountId: 5, logoHash: null });
    openTab('上傳圖片');
    expect(q('.logo-remove')).toBeNull();

    set({ logoHash: 'h1' });
    flushLogo(5, 'h1');
    const removes = collect<void>('remove');
    const remove = q<HTMLButtonElement>('.logo-remove')!;
    expect(remove.textContent!.trim()).toBe('移除圖片');
    remove.click();
    expect(removes).toHaveLength(1);
  });

  it('內建 logo grid: 無 first, aria-checked follows logo, clicking emits the slug / null', () => {
    render({ accountId: 5, logo: 'cathay-united' });
    const grid = q('.logo-grid')!;
    expect(grid.getAttribute('role')).toBe('radiogroup');
    expect(grid.getAttribute('aria-label')).toBe('內建 logo');
    const cells = qa<HTMLButtonElement>('.logo-opt');
    expect(cells).toHaveLength(ACCOUNT_LOGOS.length + 1);
    expect(cells[0].textContent!.trim()).toBe('無');
    expect(cells[0].getAttribute('aria-checked')).toBe('false');
    expect(q('.logo-opt[aria-label="國泰世華"][aria-checked="true"]')).not.toBeNull();
    expect(qa('.logo-opt[aria-checked="true"]')).toHaveLength(1);

    const logos = collect<string | null>('logoChange');
    q<HTMLButtonElement>('.logo-opt[aria-label="匯豐"]')!.click();
    cells[0].click();
    expect(logos).toEqual(['hsbc', null]);
  });

  it('內建 logo grid is one tab stop with arrow keys', () => {
    render({ accountId: 5, logo: 'cathay-united' });
    const tabStops = qa<HTMLButtonElement>('.logo-opt[tabindex="0"]');
    expect(tabStops).toHaveLength(1);
    expect(tabStops[0].getAttribute('aria-label')).toBe('國泰世華');
    expect(qa('.logo-opt[tabindex="-1"]')).toHaveLength(ACCOUNT_LOGOS.length);

    const logos = collect<string | null>('logoChange');
    const i = ACCOUNT_LOGOS.findIndex((l) => l.slug === 'cathay-united');
    expect(key(tabStops[0], 'ArrowRight').defaultPrevented).toBe(true);
    expect(logos).toEqual([ACCOUNT_LOGOS[i + 1].slug]);
    key(qa<HTMLButtonElement>('.logo-opt')[0], 'ArrowLeft');
    expect(logos).toEqual([ACCOUNT_LOGOS[i + 1].slug, ACCOUNT_LOGOS[ACCOUNT_LOGOS.length - 1].slug]);
    expect(key(tabStops[0], 'Enter').defaultPrevented).toBe(false);
    expect(logos).toHaveLength(2);

    render({ accountId: 5, logo: 'china-airlines' });
    expect(qa('.logo-opt[aria-checked="true"]')).toHaveLength(0);
    const stops = qa<HTMLButtonElement>('.logo-opt[tabindex="0"]');
    expect(stops).toHaveLength(1);
    expect(stops[0].textContent!.trim()).toBe('無');
  });

  it('arrow keys move focus to the newly selected radio', async () => {
    render({ accountId: 5, logo: ACCOUNT_LOGOS[0].slug });
    const cells = qa<HTMLButtonElement>('.logo-opt');
    cells[1].focus();
    key(cells[1], 'ArrowDown');
    await Promise.resolve();
    expect(document.activeElement).toBe(cells[2]);
  });

  it('文字＋顏色: swatches, case-insensitive match, custom colour', () => {
    render({ accountId: 5, color: '#4F8F8A' });
    const group = q('.swatches')!;
    expect(group.getAttribute('role')).toBe('radiogroup');
    expect(group.getAttribute('aria-label')).toBe('顏色');
    expect(q('.text-label')!.textContent!.trim()).toBe('文字');
    expect(q('.icon-input')!.getAttribute('aria-label')).toBe('圖示');
    expect(qa('.swatch:not(.custom)').map((s) => s.getAttribute('aria-label'))).toEqual(['靛紫', '青綠', '金棕', '玫瑰', '薰衣草', '灰藍', '石墨']);
    expect(q('.swatch[aria-label="青綠"][aria-checked="true"]')).not.toBeNull();
    expect(q('.swatch.custom')!.getAttribute('aria-checked')).toBe('false');
    expect(q('.swatch.custom')!.getAttribute('aria-label')).toBe('自訂顏色');

    set({ color: '#123456' });
    expect(qa('.swatch:not(.custom)[aria-checked="true"]')).toHaveLength(0);
    expect(q('.swatch.custom[aria-checked="true"]')!.getAttribute('tabindex')).toBe('0');
    const colorInput = q<HTMLInputElement>('.color-input')!;
    expect(colorInput.value).toBe('#123456');
    expect(colorInput.getAttribute('tabindex')).toBe('-1');
    expect(colorInput.getAttribute('aria-hidden')).toBe('true');
    expect(qa<HTMLInputElement>('.look-body input')[0]).toBe(q('.icon-input'));

    const colors = collect<string>('colorChange');
    q<HTMLButtonElement>('.swatch[aria-label="靛紫"]')!.click();
    colorInput.value = '#abcdef';
    colorInput.dispatchEvent(new Event('input'));
    expect(colors).toEqual(['#533afd', '#abcdef']);

    const click = vi.spyOn(colorInput, 'click');
    q<HTMLButtonElement>('.swatch.custom')!.click();
    expect(click).toHaveBeenCalledTimes(1);
    expect(colors).toHaveLength(2);
  });

  it('顏色 is one tab stop with arrow keys', () => {
    render({ accountId: 5, color: '#533afd' });
    const stops = qa<HTMLButtonElement>('.swatch[tabindex="0"]');
    expect(stops).toHaveLength(1);
    expect(stops[0].getAttribute('aria-label')).toBe('靛紫');

    const colors = collect<string>('colorChange');
    key(stops[0], 'ArrowRight');
    expect(colors).toEqual(['#4f8f8a']);

    const click = vi.spyOn(q<HTMLInputElement>('.color-input')!, 'click');
    key(stops[0], 'ArrowLeft');
    expect(click).toHaveBeenCalledTimes(1);
    expect(colors).toEqual(['#4f8f8a']);

    set({ color: '' });
    const empty = qa<HTMLButtonElement>('.swatch[tabindex="0"]');
    expect(empty).toHaveLength(1);
    expect(empty[0].getAttribute('aria-label')).toBe('靛紫');
  });

  it('icon input keeps maxlength 16 and emits a 16-character value', () => {
    render({ accountId: 5 });
    const input = q<HTMLInputElement>('.icon-input')!;
    expect(input.getAttribute('maxlength')).toBe('16');
    const icons = collect<string>('iconChange');
    const value = 'abcdefghijklmnop';
    input.value = value;
    input.dispatchEvent(new Event('input'));
    expect(icons).toEqual([value]);
  });

  it('preview tile is tinted with the account colour only when no logo wins', () => {
    render({ accountId: 5, color: '#533afd' });
    const tile = q('.look-tile')!;
    expect(tile.classList).toContain('tinted');
    expect(tile.style.background).toMatch(/533afd|rgb\(83, 58, 253\)/);
    expect(q('.look-preview small')!.textContent!.trim()).toBe('預覽');

    set({ logo: 'cathay-united' });
    expect(q('.look-tile.tinted')).toBeNull();
    expect(q<HTMLElement>('.look-tile')!.style.background).toBe('');
  });
});
