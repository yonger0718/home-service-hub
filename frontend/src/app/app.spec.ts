import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { App } from './app';
import { provideRouter } from '@angular/router';

describe('App', () => {
  // Spec files share `window`: restore the original matchMedia afterwards.
  const originalMatchMedia = Object.getOwnPropertyDescriptor(window, 'matchMedia');

  afterEach(() => {
    if (originalMatchMedia) Object.defineProperty(window, 'matchMedia', originalMatchMedia);
    else delete (window as { matchMedia?: unknown }).matchMedia;
  });

  beforeEach(async () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      writable: true,
      value: () => ({
        matches: false,
        media: '',
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });

    await TestBed.configureTestingModule({
      imports: [App],
      // The shell starts the nav badge refresh: its reads go to the testing backend.
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    }).compileComponents();
  });

  it('should create the app', () => {
    const fixture = TestBed.createComponent(App);
    const app = fixture.componentInstance;
    expect(app).toBeTruthy();
  });

  it('renders the shell: sidebar and page outlet, no header', async () => {
    const fixture = TestBed.createComponent(App);
    fixture.detectChanges();
    await fixture.whenStable();
    const compiled = fixture.nativeElement as HTMLElement;
    expect(compiled.querySelector('app-sidebar nav[aria-label="主要導覽"]')).not.toBeNull();
    expect(compiled.querySelector('main.hub-page router-outlet')).not.toBeNull();
    expect(compiled.querySelector('.hub-header')).toBeNull();
  });
});
