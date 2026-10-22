import type { Chart } from 'chart.js';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { hoverGuidePlugin } from './chart-guide.plugin';

describe('hoverGuidePlugin', () => {
  afterEach(() => {
    document.documentElement.style.removeProperty('--app-border');
  });

  function fakeChart(active: { element: { x: number } }[]) {
    const ctx = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      setLineDash: vi.fn(),
      lineWidth: 0,
      strokeStyle: '',
    };
    const chart = {
      tooltip: { getActiveElements: () => active },
      chartArea: { top: 0, bottom: 200 },
      ctx,
    } as unknown as Chart<'line'>;
    return { chart, ctx };
  }

  function draw(chart: Chart<'line'>): void {
    (hoverGuidePlugin.afterDraw as (c: Chart<'line'>, args: unknown, options: unknown) => void)(chart, {}, {});
  }

  it('draws one dashed vertical line at the active x from the chart top to bottom', () => {
    const { chart, ctx } = fakeChart([{ element: { x: 120 } }]);

    draw(chart);

    expect(ctx.setLineDash).toHaveBeenCalledTimes(1);
    expect((ctx.setLineDash.mock.calls[0][0] as number[]).length).toBeGreaterThan(0);
    expect(ctx.moveTo).toHaveBeenCalledTimes(1);
    expect(ctx.moveTo).toHaveBeenCalledWith(120, 0);
    expect(ctx.lineTo).toHaveBeenCalledTimes(1);
    expect(ctx.lineTo).toHaveBeenCalledWith(120, 200);
    expect(ctx.stroke).toHaveBeenCalledTimes(1);
    expect(ctx.save).toHaveBeenCalled();
    expect(ctx.restore).toHaveBeenCalled();
  });

  it('draws nothing without an active element', () => {
    const { chart, ctx } = fakeChart([]);

    draw(chart);

    expect(ctx.moveTo).not.toHaveBeenCalled();
    expect(ctx.lineTo).not.toHaveBeenCalled();
    expect(ctx.stroke).not.toHaveBeenCalled();
  });

  it('reads --app-border from the root at draw time, trimmed', () => {
    const { chart, ctx } = fakeChart([{ element: { x: 40 } }]);

    document.documentElement.style.setProperty('--app-border', ' #abcdef');
    draw(chart);
    expect(ctx.strokeStyle).toBe('#abcdef');

    document.documentElement.style.setProperty('--app-border', '#123456');
    draw(chart);
    expect(ctx.strokeStyle).toBe('#123456');
  });
});
