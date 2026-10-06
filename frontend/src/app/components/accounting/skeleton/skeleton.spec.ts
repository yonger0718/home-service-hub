import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import { SkeletonComponent } from './skeleton';

@Component({ standalone: true, imports: [SkeletonComponent], template: '<app-skeleton rows="3" />' })
class HostComponent {}

describe('SkeletonComponent', () => {
  it('renders three busy placeholder rows with an accessible name', () => {
    TestBed.configureTestingModule({ imports: [HostComponent] });
    const fixture = TestBed.createComponent(HostComponent);
    fixture.detectChanges();
    const skeleton = (fixture.nativeElement as HTMLElement).querySelector('app-skeleton')!;

    expect(skeleton.classList).toContain('skeleton');
    expect(skeleton.getAttribute('role')).toBe('status');
    expect(skeleton.getAttribute('aria-busy')).toBe('true');
    expect(skeleton.getAttribute('aria-label')).toBe('載入中');
    expect(skeleton.querySelectorAll('.skel-row').length).toBe(3);
  });

  it('renders the number of rows it is given', () => {
    TestBed.configureTestingModule({ imports: [SkeletonComponent] });
    const fixture = TestBed.createComponent(SkeletonComponent);
    fixture.componentRef.setInput('rows', 5);
    fixture.detectChanges();
    expect((fixture.nativeElement as HTMLElement).querySelectorAll('.skel-row').length).toBe(5);
  });
});
