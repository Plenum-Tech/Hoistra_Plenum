"""
Anthropic tool definitions for the UDR agent.
9 tools covering full schema introspection + CRUD + custom SELECT.
"""

TOOL_DEFINITIONS = [
    {
        "name": "list_tables",
        "description": (
            "List every table in the plenum_cafm schema along with an estimated row count. "
            "Always call this first when you don't know which tables exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "describe_table",
        "description": (
            "Get the full schema of a specific table: column names, data types, "
            "nullability, defaults, primary keys, and foreign key relationships. "
            "Call this before reading or writing to a table you haven't inspected yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table name exactly as it appears in list_tables (e.g. 'work_orders')",
                },
            },
            "required": ["table"],
        },
    },
    {
        "name": "read_records",
        "description": (
            "Fetch rows from a table with optional equality filters, column selection, "
            "sorting, and pagination. Filters match exact values. "
            "For complex conditions (date ranges, JOINs, LIKE), use execute_select instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Table name"},
                "filters": {
                    "type": "object",
                    "description": (
                        "Equality filters as {column: value} pairs. "
                        'Example: {"status": "open", "priority": "high"}'
                    ),
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific columns to return. Omit to return all columns.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum rows to return. Range 1-500, default 50.",
                },
                "offset": {
                    "type": "integer",
                    "description": "Rows to skip for pagination. Default 0.",
                },
                "order_by": {
                    "type": "string",
                    "description": "Column name to sort results by.",
                },
                "order_dir": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Sort direction. Default 'asc'.",
                },
            },
            "required": ["table"],
        },
    },
    {
        "name": "get_record",
        "description": (
            "Fetch a single record by its primary key value. "
            "Returns the complete row or an error if not found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "record_id": {
                    "type": "string",
                    "description": "The primary key value (UUID string or integer as string).",
                },
                "id_column": {
                    "type": "string",
                    "description": "Primary key column name. Default 'id'.",
                },
            },
            "required": ["table", "record_id"],
        },
    },
    {
        "name": "search_records",
        "description": (
            "Search for records using a case-insensitive partial text match across "
            "one or more columns. Useful for finding records by name, description, or code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "search_term": {
                    "type": "string",
                    "description": "Text to search for (partial match, case-insensitive).",
                },
                "search_columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names to search in.",
                },
                "limit": {"type": "integer", "description": "Max results. Default 50."},
                "offset": {"type": "integer", "description": "Pagination offset. Default 0."},
            },
            "required": ["table", "search_term", "search_columns"],
        },
    },
    {
        "name": "create_record",
        "description": (
            "Insert a new row into a table. "
            "Returns the full created record including any database-generated fields "
            "such as UUID id, created_at, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "data": {
                    "type": "object",
                    "description": (
                        "Column-value pairs for the new record. "
                        'Example: {"title": "HVAC Repair", "priority": "high"}'
                    ),
                },
            },
            "required": ["table", "data"],
        },
    },
    {
        "name": "update_record",
        "description": (
            "Update an existing record identified by its primary key. "
            "Only the fields included in 'data' are changed — all other fields are untouched. "
            "Returns the full updated record."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "record_id": {
                    "type": "string",
                    "description": "Primary key value of the record to update.",
                },
                "data": {
                    "type": "object",
                    "description": "Columns and their new values to apply.",
                },
                "id_column": {
                    "type": "string",
                    "description": "Primary key column name. Default 'id'.",
                },
            },
            "required": ["table", "record_id", "data"],
        },
    },
    {
        "name": "delete_record",
        "description": (
            "Permanently delete a record from a table by its primary key. "
            "Returns true if the record was deleted, false if it was not found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "record_id": {
                    "type": "string",
                    "description": "Primary key value of the record to delete.",
                },
                "id_column": {
                    "type": "string",
                    "description": "Primary key column name. Default 'id'.",
                },
            },
            "required": ["table", "record_id"],
        },
    },
    {
        "name": "execute_select",
        "description": (
            "Execute a custom SQL SELECT query against the plenum_cafm schema. "
            "Use this for complex queries: JOINs across tables, date range filters, "
            "aggregations (COUNT, SUM, AVG), GROUP BY, HAVING, subqueries. "
            "ONLY SELECT statements are permitted — INSERT/UPDATE/DELETE will be rejected. "
            "Always use named :param_name placeholders for values, never string interpolation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": (
                        "A SELECT statement. Must start with SELECT. "
                        "Use plenum_cafm schema prefix on tables: "
                        'plenum_cafm."table_name". '
                        "Use :param_name for query parameters."
                    ),
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Parameter values keyed by name matching :param_name placeholders in sql. "
                        'Example: {"status": "open", "min_date": "2024-01-01"}'
                    ),
                },
            },
            "required": ["sql"],
        },
    },
]
