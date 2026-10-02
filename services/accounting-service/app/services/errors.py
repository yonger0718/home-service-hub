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
