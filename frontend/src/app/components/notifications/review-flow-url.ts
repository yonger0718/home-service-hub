/**
 * The review flow's URL, `/notifications/recon/<statementId>`, known in one place: the route matcher and the shell
 * (which hides the floating 記一筆 over the full-screen flow) both test ids with it. Dependency-free: the shell is in
 * the initial bundle.
 */

/** A statement id as the flow's URL writes it: a positive integer, no leading zero, at most 15 digits (a safe integer). */
export const REVIEW_FLOW_ID = /^[1-9]\d{0,14}$/;

/** The e-invoice flow's segment: `/notifications/einvoices` (D10; F3 adds `?state=unmatched` on the same URL). */
export const EINVOICE_FLOW_SEGMENT = 'einvoices';

/** `url` (query and fragment ignored) is a review-flow or e-invoice-flow URL the route matches. */
export function isReviewFlowUrl(url: string): boolean {
  const path = url.split(/[?#]/)[0];
  if (path === `/notifications/${EINVOICE_FLOW_SEGMENT}`) {
    return true;
  }
  const match = /^\/notifications\/recon\/([^/]+)$/.exec(path);
  return match !== null && REVIEW_FLOW_ID.test(match[1]);
}
