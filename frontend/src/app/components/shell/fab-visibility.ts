/** Idle time after which a hidden 記一筆 button comes back. */
export const FAB_IDLE_MS = 1200;
/** Elements whose scrolling counts as page scrolling: the shell's main column or a pane that opts in. */
export const PAGE_SCROLLER_SELECTOR = '.hub-main, [data-page-scroller]';

export interface ScrollSample {
  /** New scrollTop minus the previous one. */
  delta: number;
  /** The scroller is at the bottom. */
  atEnd: boolean;
  /** The button itself has focus. */
  focused: boolean;
}

/** Whether the 記一筆 button should be visible after a scroll event. */
export function nextFabVisibility(visible: boolean, sample: ScrollSample): boolean {
  if (sample.focused || sample.atEnd || sample.delta < 0) {
    return true;
  }
  if (sample.delta > 0) {
    return false;
  }
  return visible;
}
