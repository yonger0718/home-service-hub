// frontend/src/app/components/accounting/dirty-form.service.spec.ts
import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { DirtyAware, DirtyFormRegistry } from './dirty-form.service';

function form(dirty: boolean): DirtyAware & { showDiscardPrompt: ReturnType<typeof vi.fn<() => void>> } {
  return { isDirty: () => dirty, showDiscardPrompt: vi.fn() };
}

describe('DirtyFormRegistry', () => {
  const registry = () => TestBed.inject(DirtyFormRegistry);

  it('runs the close at once without a form or with a clean one', () => {
    const run = vi.fn();
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);

    const clean = form(false);
    registry().register(clean);
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(2);
    expect(clean.showDiscardPrompt).not.toHaveBeenCalled();
    expect(registry().promptOpen()).toBe(false);
  });

  it('holds the close of a dirty form until 放棄, and drops it on 留下', () => {
    const dirty = form(true);
    const run = vi.fn();
    registry().register(dirty);

    registry().requestClose(run);
    expect(run).not.toHaveBeenCalled();
    expect(dirty.showDiscardPrompt).toHaveBeenCalledTimes(1);
    expect(registry().promptOpen()).toBe(true);
    registry().cancelDiscard();
    expect(registry().promptOpen()).toBe(false);
    registry().confirmDiscard();
    expect(run).not.toHaveBeenCalled();

    registry().requestClose(run);
    registry().confirmDiscard();
    expect(run).toHaveBeenCalledTimes(1);
    expect(registry().promptOpen()).toBe(false);
  });

  it('clears a pending close on clearPending and on unregister, ignoring another form unregistering', () => {
    const dirty = form(true);
    const other = form(false);
    const run = vi.fn();
    registry().register(dirty);
    registry().requestClose(run);
    registry().clearPending();
    expect(registry().promptOpen()).toBe(false);
    registry().confirmDiscard();
    expect(run).not.toHaveBeenCalled();

    registry().requestClose(run);
    registry().unregister(other);
    expect(registry().promptOpen()).toBe(true);
    registry().unregister(dirty);
    expect(registry().promptOpen()).toBe(false);
    registry().requestClose(run);
    expect(run).toHaveBeenCalledTimes(1);
  });
});
