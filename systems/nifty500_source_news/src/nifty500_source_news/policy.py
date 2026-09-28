"""Binding constants for the NIFTY 500 Source News track."""

TRACK_ID = "nifty500-source-news"
APPROVER_ID = "aditya-lakhotia"
AUTHORIZED_APPROVER_IDS = frozenset({APPROVER_ID})


def is_authorized_approver(actor_id: str) -> bool:
    return actor_id in AUTHORIZED_APPROVER_IDS

CLEAN_ROOM_ONLY = True
ADITYA_APPROVAL_REQUIRED = True
PAPER_ONLY = True
POINT_IN_TIME_ONLY = True
IMMUTABLE_LEDGER = True
APPROVED_SOURCES_ONLY = True
FAIL_CLOSED = True
TRACK_ISOLATION = True
VERSIONED_CHANGE_CONTROL = True

# Trading mechanics intentionally do not exist yet. They require a separate approved
# design proposal and must not be inferred from the FnO Momentum track.
