/** Account colour swatches: the seven light `--app-series-N` values of `styles.scss`, stored as lowercase hex.
 *  Matching a stored colour is case-insensitive; a colour outside the set is shown as the custom swatch. */
export const ACCOUNT_COLOR_SWATCHES: readonly { hex: string; label: string }[] = [
  { hex: '#533afd', label: '靛紫' },
  { hex: '#4f8f8a', label: '青綠' },
  { hex: '#b7843f', label: '金棕' },
  { hex: '#b9687e', label: '玫瑰' },
  { hex: '#7e78a8', label: '薰衣草' },
  { hex: '#5f7f98', label: '灰藍' },
  { hex: '#8a93a3', label: '石墨' },
];
