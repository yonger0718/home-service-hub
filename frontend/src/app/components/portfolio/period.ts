function slashDate(date: string): string {
  return date.slice(0, 10).replaceAll('-', '/');
}

/** Header period: 'YYYY/MM/DD – YYYY/MM/DD', 'YYYY/MM/DD 起', '至 YYYY/MM/DD', or '全部期間'. */
export function periodLabel(q: { date_from?: string | null; date_to?: string | null }): string {
  const from = q.date_from ? slashDate(q.date_from) : null;
  const to = q.date_to ? slashDate(q.date_to) : null;
  if (from && to) return `${from} – ${to}`;
  if (from) return `${from} 起`;
  if (to) return `至 ${to}`;
  return '全部期間';
}
