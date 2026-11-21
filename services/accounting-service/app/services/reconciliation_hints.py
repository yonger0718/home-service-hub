"""Reconciliation case hints (`ReconciliationCase.context["hint"]`) shared across services.

A leaf module (no app imports), so matching, reconciliation, dividend, policy and inbox code can all name the same
strings without the circular imports that used to force literal copies (#274).
"""

PEER_HINT = "needs_peer"  # R6b: a transfer/payment line that needs its peer leg, not a single-leg entry
STAND_IN_DELETED_HINT = "stand_in_deleted"  # R6b.0: the owner deleted the stand-in booked for this line
