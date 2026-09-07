"""OpenAI function-calling tool definitions for the WO orchestrator agent."""

TOOL_DEFINITIONS = [
    # ── Data lookup tools ──────────────────────────────────────────────────────

    {
        "type": "function",
        "function": {
            "name": "search_assets",
            "description": (
                "Search for assets by name, code, or keyword. "
                "Always call this first when the user mentions a piece of equipment or system."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Asset name, code, or descriptive keyword (e.g. 'HVAC floor 3', 'chiller', 'AHU-301')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_details",
            "description": (
                "Get full details for a specific asset: manufacturer, model, status, PPM schedules, and known issues. "
                "Call after confirming the asset with the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "description": "Asset UUID or asset_code from search_assets result",
                    }
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_locations",
            "description": "Search for locations by name, building, or floor to resolve a location string to a known record.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Location name, building, or floor (e.g. 'floor 3', 'Building A', 'roof')",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_ppm_schedules",
            "description": "Find active PPM (Planned Preventive Maintenance) schedules linked to an asset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "description": "Asset UUID",
                    }
                },
                "required": ["asset_id"],
            },
        },
    },

    # ── Intelligence tools (wrap existing modules) ─────────────────────────────

    {
        "type": "function",
        "function": {
            "name": "assess_criticality",
            "description": (
                "AI-powered criticality assessment — scores the work order on safety, operational, "
                "financial, and compliance dimensions. Returns criticality_level and response_time_hours. "
                "Call this before suggesting a priority or schedule."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset name or code"},
                    "location": {"type": "string", "description": "Location description"},
                    "issue_description": {
                        "type": "string",
                        "description": "Full description of the issue or maintenance task",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent", "critical"],
                        "description": "Initial priority from request (can be updated after assessment)",
                    },
                    "source": {
                        "type": "string",
                        "enum": ["email", "ppm", "manual", "chat", "tenant", "internal", "remediation"],
                        "description": "Source of the work order request",
                    },
                },
                "required": ["asset", "location", "issue_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "identify_safety_conditions",
            "description": (
                "Identify safety hazards, required permits, and PPE for the work. "
                "Call this whenever the work involves electrical, chemical, height, or confined-space risks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset name or code"},
                    "location": {"type": "string", "description": "Location description"},
                    "issue_description": {
                        "type": "string",
                        "description": "Description of the issue or task",
                    },
                },
                "required": ["asset", "location", "issue_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_compliance_requirements",
            "description": (
                "Detect regulatory compliance requirements for the work order (EPA, DEWA, UAE civil defence, etc.). "
                "Call this for HVAC, electrical, boiler, or any permit-sensitive work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset": {"type": "string", "description": "Asset name or code"},
                    "location": {"type": "string", "description": "Location description"},
                    "issue_description": {
                        "type": "string",
                        "description": "Description of the issue or task",
                    },
                },
                "required": ["asset", "issue_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_asset_intelligence",
            "description": (
                "Look up asset intelligence: known failure history, average repair cost, MTBF, and warranty status. "
                "Use this to give the user context about the asset's health."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_id": {
                        "type": "string",
                        "description": "Asset name, code, or UUID",
                    }
                },
                "required": ["asset_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_scheduling_recommendation",
            "description": (
                "Get a smart scheduling recommendation (date, time window, constraints) based on "
                "criticality, location occupancy, and work type."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "criticality_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                        "description": "Criticality level from assess_criticality",
                    },
                    "estimated_duration_hours": {
                        "type": "number",
                        "description": "Estimated duration of the work in hours",
                    },
                    "location": {
                        "type": "string",
                        "description": "Location description (used to determine occupancy patterns)",
                    },
                },
                "required": ["criticality_level", "estimated_duration_hours", "location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "allocate_resources",
            "description": (
                "Smart technician allocation — scores available technicians by skill match, "
                "workload, and performance to recommend the best fit."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_description": {
                        "type": "string",
                        "description": "Issue or task description used to infer required skills",
                    }
                },
                "required": ["issue_description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "score_vendors",
            "description": (
                "Score and rank available vendors for the work order using a composite model "
                "(rating 35%, availability 30%, expertise 25%, response time 5%, budget fit 5%)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "asset_type": {
                        "type": "string",
                        "description": "Type of asset (HVAC, Electrical, Chiller, etc.) to match vendor expertise",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent", "critical"],
                        "description": "Work order priority",
                    },
                    "estimated_budget": {
                        "type": "number",
                        "description": "Estimated budget in AED (0 if unknown)",
                    },
                    "required_skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of required skill tags",
                    },
                },
                "required": ["asset_type", "priority"],
            },
        },
    },

    # ── Workflow tools ─────────────────────────────────────────────────────────

    {
        "type": "function",
        "function": {
            "name": "create_work_order",
            "description": (
                "Create and persist a work order in the system. "
                "Only call this AFTER the user has explicitly confirmed they want to create the WO. "
                "Returns approval_suggestion and auto_suggestion (recommended approvers + similar past "
                "approval processes) — present those to the user before calling request_approval."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "enum": ["email", "ppm", "manual", "chat", "tenant", "internal", "remediation"],
                        "description": "Source of the work order",
                    },
                    "asset": {"type": "string", "description": "Asset name or code"},
                    "location": {"type": "string", "description": "Location name or description"},
                    "issue_description": {
                        "type": "string",
                        "description": "Full description of the issue or maintenance task",
                    },
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "urgent", "critical"],
                    },
                    "request_type": {
                        "type": "string",
                        "enum": ["repair", "maintenance", "inspection", "installation"],
                    },
                    "requester_name": {"type": "string", "description": "Name of the requester"},
                    "requester_email": {"type": "string", "description": "Email of the requester"},
                    "requester_phone": {"type": "string", "description": "Phone number (optional)"},
                    "vendor": {
                        "type": "string",
                        "description": "Ignored at create — vendor is assigned after final approval",
                    },
                    "scheduled_date": {
                        "type": "string",
                        "description": "Ignored at create — set after final approval (YYYY-MM-DD)",
                    },
                    "scheduled_time": {
                        "type": "string",
                        "description": "Ignored at create — set after final approval",
                    },
                    "estimated_duration": {
                        "type": "number",
                        "description": "Ignored at create — set after final approval (hours)",
                    },
                    "special_requirements": {
                        "type": "string",
                        "description": "Safety permits, PPE, or compliance notes to attach",
                    },
                    "submit_to_cmms": {
                        "type": "boolean",
                        "description": "Whether to submit immediately to the CMMS system (default true)",
                    },
                },
                "required": [
                    "source", "asset", "location", "issue_description",
                    "priority", "request_type",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_approval_chain",
            "description": (
                "Preview the approval chain for an existing or hypothetical work order. "
                "After create_work_order, use the auto_suggestion in that result instead — do not call "
                "this before create. Use suggest_approval_chain only when the user asks who would approve "
                "without creating a WO, or to refresh the chain for an existing work_order_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "work_type": {"type": "string"},
                    "priority": {"type": "string"},
                    "location_id": {"type": "integer"},
                    "location": {"type": "string", "description": "Building or site name"},
                    "estimated_cost": {"type": "number"},
                    "asset_category": {"type": "string"},
                    "work_order_id": {"type": "string"},
                },
                "required": ["work_type", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "request_approval",
            "description": (
                "Create an approval request for a work order that requires human sign-off "
                "before preparation or execution."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "work_order_id": {"type": "string", "description": "Work order ID to approve"},
                    "approval_type": {
                        "type": "string",
                        "enum": ["preparation", "final", "simple"],
                        "description": "Type of approval required",
                    },
                    "approver": {
                        "type": "string",
                        "description": "Name or email of the designated approver",
                    },
                },
                "required": ["work_order_id", "approval_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_work_order_status_track",
            "description": (
                "Full work order status: lifecycle state, each approval step (names and pending/approved), "
                "technician assignment, scheduling, holds (parts/assets), journey progress, and status timeline. "
                "Use when the user asks for status, progress, track, or where a work order stands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "work_order_id": {"type": "string", "description": "Work order ID"},
                },
                "required": ["work_order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_approval_request_email",
            "description": (
                "Send the Outlook approval-request email to the step 1 approver (or another step). "
                "Call after request_approval when the user asks to email the approver, or to re-send."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "work_order_id": {"type": "string", "description": "Work order ID"},
                    "step_order": {
                        "type": "integer",
                        "description": "Approval chain step (default 1)",
                        "default": 1,
                    },
                },
                "required": ["work_order_id"],
            },
        },
    },
]
