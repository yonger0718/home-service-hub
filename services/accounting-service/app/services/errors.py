"""Service-layer errors shared by every write service; routers map them to HTTP (app.routers.errors)."""


class ValidationError(Exception):
    """A request is well-formed but violates a ledger rule; HTTP 422 naming `field`."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class ConflictError(Exception):
    """The write conflicts with existing rows (in use, reward entry, ...); HTTP 409."""


class NotFoundError(Exception):
    """The target row does not exist; HTTP 404."""


# Codes of the split rework (§1.6). A 409 message is "<code>" or "<code>: <detail>"; the SPA matches the prefix.
MEMBER_NOT_FOUND = "member_not_found"  # the 404 of an id that is not a member of the group
MEMBER_LOCKED = "member_locked"
ALREADY_GROUPED = "already_grouped"
ENTRY_LOCKED = "entry_locked"
KIND_NOT_SPLITTABLE = "kind_not_splittable"
GROUP_SCHEDULED = "group_scheduled"
RETRY = "retry"


class CodedConflictError(ConflictError):
    """A 409 whose message starts with a stable code: `<code>` or `<code>: <detail>`."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code if detail is None else f"{code}: {detail}")
        self.code = code
