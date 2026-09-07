class UDRError(Exception):
    """Base exception for all UDR errors."""


class UnsafeIdentifierError(UDRError):
    """Raised when a table or column name fails the safety regex."""


class TableNotFoundError(UDRError):
    """Raised when a requested table does not exist in the schema."""


class ColumnNotFoundError(UDRError):
    """Raised when a requested column does not exist in the table."""


class RecordNotFoundError(UDRError):
    """Raised when a record lookup by PK returns no rows."""


class UnsafeQueryError(UDRError):
    """Raised when execute_select receives a non-SELECT statement."""
