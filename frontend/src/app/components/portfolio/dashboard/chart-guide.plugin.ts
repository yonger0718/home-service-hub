import type { Plugin } from 'chart.js';

/**
 * Dashed vertical guide at the hovered x. Registered per chart through `p-chart [plugins]`;
 * the colour is read at draw time so a theme switch needs no rebuild. Drawn after the datasets
 * because per-chart plugins run after the global Tooltip, whose box `afterDraw` would cross.
 */
export const hoverGuidePlugin: Plugin<'line'> = {
  id: 'hoverGuide',
  afterDatasetsDraw(chart) {
    const active = chart.tooltip?.getActiveElements() ?? [];
    if (active.length === 0) return;
    const { x } = active[0].element;
    const { top, bottom } = chart.chartArea;
    const ctx = chart.ctx;
    ctx.save();
    ctx.beginPath();
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--app-border').trim();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};
