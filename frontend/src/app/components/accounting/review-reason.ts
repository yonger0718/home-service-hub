/** The review reason of a purchase whose posted rewards no longer match its figures (reward engine spec §5.1, #306). */
export const REWARD_POSTED = 'reward_posted';

/** Title of a 待確認 pill: names the reason when the server gave one the owner can act on (#306). */
export function reviewTitle(entry: { review_reason?: string | null }): string {
  return entry.review_reason === REWARD_POSTED ? '回饋已入帳，消費金額已改：到明細重算回饋或保持原樣' : '請確認這筆記錄';
}
