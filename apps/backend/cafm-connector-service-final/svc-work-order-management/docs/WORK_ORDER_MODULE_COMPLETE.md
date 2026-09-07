# AIMMS Work Order Management Module - Complete Documentation

> **Last updated:** May 2026
> **Architecture revision:** LLM Orchestration Layer added (see Section 2a)

---

## Executive Summary

The **AIMMS Work Order Management Module** is a comprehensive, AI-powered work order creation, management, and execution system that handles all types of maintenance requests from multiple sources with intelligent automation, minimal human intervention, and complete lifecycle tracking.

**Key Capabilities:**
- Multi-source work order creation (email, PPM, manual, tenant, internal, remediation, **chat**)
- **GPT-powered conversational interface** — the LLM intelligently routes through all AI intelligence steps
- 15-step intelligent work order engine (legacy sequential path — retained for direct API use)
- Automated approval workflows
- CMMS bidirectional integration
- Real-time tracking and notifications
- Complete audit trail and compliance

---

## Section 2a — LLM Orchestration Layer (NEW — May 2026)

### Overview

The entire work order management process is now wrapped in an **LLM conversational interface** powered by OpenAI GPT (configured via `OPENAI_MODEL` env var, default `gpt-4o-mini`).

Instead of a hardcoded 15-step sequential pipeline, the GPT agent:
- Receives input from **any source** in natural language
- **Decides which intelligence tools to call** and in what order based on context
- Holds a full **back-and-forth conversation** with the user to gather information
- Confirms with the user before creating a work order
- Can be reached via chat, email webhook, or PPM trigger — all through the same agent

### Architecture

```
Any Input Source
├── POST /api/chat/           → direct user message ("AC unit on floor 3 making noise")
├── POST /api/chat/email      → parsed Outlook email payload
└── POST /api/chat/ppm        → PPM schedule trigger (automated)
         │
         ▼
  WOOrchestrator  (src/agent/orchestrator.py)
  ├── OpenAI GPT with function calling
  ├── Conversation history persisted per session (plenum_cafm.wo_chat_sessions)
  ├── System prompt: src/agent/prompts.py
  └── Tool registry: 13 tools across 4 categories
         │
         ├── Asset / location tools       (src/agent/tools/asset_tools.py)
         │   ├── search_assets(query)
         │   ├── get_asset_details(asset_id)
         │   ├── search_locations(query)
         │   └── find_ppm_schedules(asset_id)
         │
         ├── Intelligence tools           (src/agent/tools/intelligence_tools.py)
         │   ├── assess_criticality(...)         → wraps CriticalityAssessor
         │   ├── identify_safety_conditions(...) → wraps SafetyIdentifier
         │   ├── detect_compliance_requirements(...)→ wraps ComplianceDetector
         │   ├── get_asset_intelligence(...)     → wraps AssetIntelligence
         │   ├── get_scheduling_recommendation(...)→ wraps SmartScheduler
         │   └── allocate_resources(...)         → wraps ResourceAllocator
         │
         ├── Vendor tool                  (src/agent/tools/vendor_tools.py)
         │   └── score_vendors(...)              → wraps VendorScorer
         │
         └── Workflow tools               (src/agent/tools/workflow_tools.py)
             ├── create_work_order(...)          → writes to plenum_cafm.work_orders
             └── request_approval(...)           → writes to plenum_cafm.wo_approval_requests
```

### Key Files

| File | Purpose |
|------|---------|
| `src/agent/orchestrator.py` | Main GPT agent — session management + tool-calling loop |
| `src/agent/prompts.py` | System prompt defining agent behaviour and conversational rules |
| `src/agent/session_store.py` | DB-backed session store (JSONB messages in wo_chat_sessions) |
| `src/agent/input_normalizer.py` | Normalises email/PPM payloads into the first user message |
| `src/agent/tools/definitions.py` | All 13 OpenAI function-calling tool schemas |
| `src/agent/tools/executor.py` | Dispatches GPT tool calls to implementations |
| `src/agent/tools/asset_tools.py` | DB-backed asset, location, PPM lookups |
| `src/agent/tools/intelligence_tools.py` | Thin async wrappers around existing intelligence modules |
| `src/agent/tools/vendor_tools.py` | Vendor pool + VendorScorer integration |
| `src/agent/tools/workflow_tools.py` | Work order creation, approval, CMMS submission |
| `src/models/session.py` | WOChatSession ORM model (wo_chat_sessions table) |
| `src/api/routes/chat.py` | FastAPI routes for chat, email ingest, PPM trigger, history |

### New DB Table: wo_chat_sessions

```sql
CREATE TABLE plenum_cafm.wo_chat_sessions (
    session_id    VARCHAR(100)  PRIMARY KEY,
    messages      JSONB         NOT NULL DEFAULT '[]',   -- full conversation history
    context       JSONB         NOT NULL DEFAULT '{}',   -- gathered WO context
    source        VARCHAR(30)   DEFAULT 'chat',          -- chat | email | ppm
    created_at    TIMESTAMPTZ   DEFAULT now(),
    last_activity TIMESTAMPTZ   DEFAULT now()
);
```

### API Endpoints (Chat Interface)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat/` | Send a message. Omit `session_id` to start a new conversation. |
| POST | `/api/chat/email` | Ingest a parsed email payload to start a WO conversation. |
| POST | `/api/chat/ppm` | PPM schedule trigger — agent creates WO automatically. |
| GET | `/api/chat/{session_id}/history` | Full conversation history (system prompt excluded). |

### Example: Chat Flow

```
User:  "The air handling unit on floor 3 keeps tripping — it's making a grinding noise"

Agent: [calls search_assets("air handling unit floor 3")]
       [calls get_asset_details("AHU-301")]
       "I found AHU-301 (Carrier 50TC-030) on Floor 3. Is that the unit?
        I'm also checking its maintenance history..."
       [calls assess_criticality(...)]
       [calls identify_safety_conditions(...)]
       "This looks like a high-criticality issue — grinding noise could indicate
        bearing failure. I've flagged it as urgent. Lockout/tagout will be required.
        Can I get your name and email so I can log the requester?"

User:  "John Smith, john.smith@company.com"

Agent: [calls get_scheduling_recommendation("high", 4, "Floor 3")]
       [calls score_vendors("HVAC", "urgent")]
       [calls allocate_resources("grinding noise HVAC bearing")]
       "Here's what I've put together:
        - Asset: AHU-301 | Priority: Urgent
        - Safety: Lockout/tagout required
        - Vendor: TechServ MEP Contractors (top score: 87.5)
        - Technician: Mike Johnson (HVAC specialist)
        - Scheduled: Tomorrow 09:00 AM
        Shall I create this work order?"

User:  "Yes, go ahead"

Agent: [calls create_work_order(...)]
       "Done! Work order **WO-20240427-A3F2B1** has been created.
        Status: Pending Approval | Priority: Urgent
        TechServ has been assigned. You'll receive an email confirmation."
```

### Example: PPM Auto-Trigger

```
[PPM Schedule fires for Chiller-102 quarterly maintenance]

Agent: [calls search_assets("Chiller-102")]
       [calls get_asset_details(...)]
       [calls find_ppm_schedules(...)]
       [calls assess_criticality(...)]
       [calls get_scheduling_recommendation("medium", 4, "Mechanical Room B2")]
       [calls score_vendors("Chiller", "medium")]
       [calls create_work_order(...)]

Reply: "Work order WO-20240428-B9C2D3 created for Chiller-102 quarterly PM.
        Vendor: TechServ MEP Contractors | Scheduled: 2024-04-29 09:00 AM"
```

### Tool Decision Logic (Agent's Responsibility)

The GPT agent decides which tools to call based on context. Typical patterns:

| Situation | Tools called |
|-----------|-------------|
| Any request | `search_assets` → `get_asset_details` |
| Reactive (chat/email) | + `assess_criticality` + `identify_safety_conditions` + `detect_compliance_requirements` |
| High-criticality issue | + `get_asset_intelligence` (failure history) |
| Before creating WO | + `get_scheduling_recommendation` + `score_vendors` + `allocate_resources` |
| PPM trigger | Subset — criticality skipped if maintenance_type is PM |
| Confirmation received | `create_work_order` → (optional) `request_approval` |

### Extending the Agent

**Add a new tool:**
1. Define the JSON schema in `src/agent/tools/definitions.py`
2. Implement the function in the appropriate tool file
3. Add a `case "tool_name":` entry in `src/agent/tools/executor.py`
4. The GPT agent will discover and use it automatically

**Add a new input source:**
1. Add a normaliser method to `src/agent/input_normalizer.py`
2. Add a route in `src/api/routes/chat.py`
3. Call `agent.chat(normalised_message, source="new_source")`

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Work Order Sources](#work-order-sources)
3. [Email-Based Work Orders](#email-based-work-orders)
4. [PPM-Based Work Orders](#ppm-based-work-orders)
5. [Intelligent Work Order Engine](#intelligent-work-order-engine)
6. [Work Order Lifecycle](#work-order-lifecycle)
7. [API Reference](#api-reference)
8. [Database Schema](#database-schema)
9. [Frontend Components](#frontend-components)
10. [Integration Guide](#integration-guide)
11. [Deployment Guide](#deployment-guide)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Work Order Entry Points                        │
├────────┬────────┬────────┬────────┬────────┬───────────────┤
│ Email  │  PPM   │ Manual │ Tenant │Internal│  Remediation  │
│ System │Schedule│  Entry │Request │Request │   Detection   │
└───┬────┴───┬────┴───┬────┴───┬────┴───┬────┴───────┬───────┘
    │        │        │        │        │            │
    └────────┴────────┴────────┴────────┴────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│         Intelligent Work Order Creation Engine              │
│  ┌────────────────────────────────────────────────────┐    │
│  │ 15-Step AI-Powered Assessment                      │    │
│  │ • Source identification                            │    │
│  │ • Data collection from workspace                   │    │
│  │ • Criticality assessment (AI)                      │    │
│  │ • Safety condition identification                  │    │
│  │ • Compliance detection                             │    │
│  │ • Location & site validation                       │    │
│  │ • Asset intelligence lookup                        │    │
│  │ • Site clearance certificate check                 │    │
│  │ • Warranty & inspection intelligence               │    │
│  │ • Spare parts availability check                   │    │
│  │ • Vendor suggestion (composite scoring)            │    │
│  │ • Resource allocation (smart matching)             │    │
│  │ • Smart scheduling (constraint-based)              │    │
│  │ • Workspace pinning                                │    │
│  │ • Journey log creation                             │    │
│  └────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Approval Workflow Engine                       │
│  • Type-based routing (preparation/approval/both)           │
│  • Multi-level approvals                                    │
│  • Email notifications                                      │
│  • Workspace integration                                    │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Execution & Tracking                           │
│  • CMMS integration (Maximo/SAP PM)                        │
│  • Real-time status updates                                 │
│  • Journey tracking                                         │
│  • Deviation detection                                      │
│  • Completion verification                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Work Order Sources

### 1. Email-Based Work Orders

**Trigger:** Email received with maintenance request
**Processing:** AI extracts asset, location, notes
**Flow:** 8-step process with auto-approval

### 2. PPM Schedule-Based

**Trigger:** PPM schedule due date
**Processing:** Auto-creates with pre-populated details
**Flow:** Automatic with approval notification

### 3. Manual Entry

**Trigger:** User creates work order in AIMMS
**Processing:** Form-based entry with validation
**Flow:** Standard approval workflow

### 4. Tenant Requests

**Trigger:** Tenant submits request via portal
**Processing:** Auto-classification and routing
**Flow:** Special handling with client notifications

### 5. Internal Requests

**Trigger:** Staff member reports issue
**Processing:** Departmental routing
**Flow:** Internal approval chain

### 6. Remediation Detection

**Trigger:** AI detects asset needs replacement
**Processing:** Automated scope and budget generation
**Flow:** Approval → RFQ → Execution

---

## Email-Based Work Orders

### Complete 8-Step Flow

```python
# services/email_work_order_processor.py

from typing import Dict, Any, List, Optional
from datetime import datetime
import anthropic
import json
import re

class EmailWorkOrderProcessor:
    """
    Complete Email-Based Work Order Processing System
    
    Handles 8-step flow:
    1. Email entry with extraction
    2. Missing info auto-email
    3. Work order creation
    4. Human approval request
    5. AIMMS notification with preparation form
    6. Preparation completion
    7. Final approval and CMMS integration
    8. Client notification (for tenant work orders)
    """
    
    def __init__(
        self,
        aimms_api_url: str,
        outlook_api_url: str,
        cmms_api_url: str,
        claude_api_key: str
    ):
        self.aimms_api_url = aimms_api_url
        self.outlook_api_url = outlook_api_url
        self.cmms_api_url = cmms_api_url
        self.claude_client = anthropic.Anthropic(api_key=claude_api_key)
    
    async def process_incoming_email(
        self,
        email_id: str
    ) -> Dict[str, Any]:
        """
        ✅ STEP 1: Process incoming email and extract work order details
        """
        
        print(f"\n{'='*70}")
        print(f"📧 PROCESSING EMAIL WORK ORDER")
        print(f"Email ID: {email_id}")
        print(f"{'='*70}\n")
        
        # Get email content
        email = await self.get_email_from_outlook(email_id)
        
        print(f"From: {email['from']}")
        print(f"Subject: {email['subject']}")
        
        # Extract work order details using AI
        extracted_data = await self.extract_work_order_details(email)
        
        print(f"\n✅ Extracted Data:")
        print(f"   Asset: {extracted_data.get('asset', 'Not specified')}")
        print(f"   Location: {extracted_data.get('location', 'Not specified')}")
        print(f"   Priority: {extracted_data.get('priority', 'Medium')}")
        
        # Check if all required info is present
        missing_info = self.identify_missing_info(extracted_data)
        
        if missing_info:
            # ✅ STEP 2: Send auto-email for missing information
            print(f"\n⚠️  Missing Information: {', '.join(missing_info)}")
            await self.send_missing_info_email(email, missing_info)
            
            return {
                'status': 'awaiting_info',
                'missing_fields': missing_info,
                'email_id': email_id
            }
        
        # ✅ STEP 3: Create work order with all details
        work_order = await self.create_work_order_from_email(
            email=email,
            extracted_data=extracted_data
        )
        
        print(f"\n✅ Work Order Created: {work_order['work_order_id']}")
        
        # ✅ STEP 4: Request human approval
        approval_request = await self.request_approval(work_order)
        
        print(f"✅ Approval request sent to: {approval_request['approver']}")
        
        return {
            'status': 'pending_approval',
            'work_order_id': work_order['work_order_id'],
            'approval_request_id': approval_request['request_id']
        }
    
    async def extract_work_order_details(
        self,
        email: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ Extract work order details from email using AI
        """
        
        prompt = f"""Extract work order information from this email:

From: {email['from']}
Subject: {email['subject']}
Body:
{email['body']}

Extract the following in JSON format:
{{
    "asset": "asset name or ID if mentioned",
    "location": "building/floor/room if mentioned",
    "issue_description": "description of the problem",
    "priority": "low|medium|high|urgent",
    "request_type": "repair|inspection|maintenance|installation",
    "requester_name": "person's name",
    "requester_email": "email address",
    "requester_phone": "phone number if present",
    "attachments": ["list of attachment filenames"],
    "notes": "any additional relevant notes"
}}

If information is not present in the email, use null for that field.
"""

        message = self.claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text
        
        # Parse JSON
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
        
        extracted = json.loads(response_text)
        
        # Add email metadata
        extracted['source'] = 'email'
        extracted['source_email_id'] = email['id']
        extracted['received_at'] = email['received_at']
        
        return extracted
    
    def identify_missing_info(
        self,
        extracted_data: Dict[str, Any]
    ) -> List[str]:
        """
        ✅ Identify missing required information
        """
        
        required_fields = [
            'asset',
            'location',
            'issue_description',
            'requester_name',
            'requester_email'
        ]
        
        missing = []
        
        for field in required_fields:
            if not extracted_data.get(field):
                missing.append(field)
        
        return missing
    
    async def send_missing_info_email(
        self,
        original_email: Dict[str, Any],
        missing_fields: List[str]
    ) -> None:
        """
        ✅ STEP 2: Send automated email requesting missing information
        """
        
        field_descriptions = {
            'asset': 'Asset name or equipment ID (e.g., "HVAC Unit #3" or "HVAC-301")',
            'location': 'Location (e.g., "Building A, Floor 3, Room 305")',
            'issue_description': 'Detailed description of the issue',
            'requester_name': 'Your full name',
            'requester_email': 'Your email address',
            'requester_phone': 'Your contact phone number'
        }
        
        missing_list = '\n'.join([
            f"• {field_descriptions.get(field, field)}"
            for field in missing_fields
        ])
        
        email_body = f"""Dear {original_email['from_name']},

Thank you for submitting your maintenance request. To process your work order, we need the following additional information:

{missing_list}

Please reply to this email with the missing details, and we'll create your work order immediately.

Original Request:
Subject: {original_email['subject']}
Received: {original_email['received_at']}

Thank you,
AIMMS Facilities Management System
"""

        await self.send_email(
            to=original_email['from'],
            subject=f"Re: {original_email['subject']} - Additional Information Needed",
            body=email_body
        )
        
        print(f"✅ Missing info email sent to {original_email['from']}")
    
    async def create_work_order_from_email(
        self,
        email: Dict[str, Any],
        extracted_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ STEP 3: Create work order with complete details
        """
        
        work_order = {
            'work_order_id': f"WO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'source': 'email',
            'source_reference': email['id'],
            
            # Extracted information
            'asset': extracted_data['asset'],
            'location': extracted_data['location'],
            'issue_description': extracted_data['issue_description'],
            'priority': extracted_data.get('priority', 'medium'),
            'request_type': extracted_data.get('request_type', 'repair'),
            
            # Requester information
            'requester_name': extracted_data['requester_name'],
            'requester_email': extracted_data['requester_email'],
            'requester_phone': extracted_data.get('requester_phone'),
            
            # Status
            'status': 'pending_approval',
            'approval_type': 'preparation',  # Requires preparation step
            
            # Metadata
            'created_at': datetime.utcnow().isoformat(),
            'created_by': 'email_processor',
            
            # Attachments
            'attachments': extracted_data.get('attachments', []),
            
            # Journey log reference
            'journey_log_id': None  # Will be set when journey is created
        }
        
        # Save to database
        response = requests.post(
            f"{self.aimms_api_url}/api/work-orders",
            json=work_order,
            headers={'X-API-Key': AIMMS_API_KEY}
        )
        
        saved_wo = response.json()
        
        # Create journey log
        journey = await self.create_journey_log(saved_wo)
        
        # Update work order with journey reference
        saved_wo['journey_log_id'] = journey['jlog_id']
        
        await self.update_work_order(saved_wo['work_order_id'], {
            'journey_log_id': journey['jlog_id']
        })
        
        return saved_wo
    
    async def request_approval(
        self,
        work_order: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ STEP 4: Request human approval
        """
        
        # Determine approver based on priority and type
        approver = await self.determine_approver(work_order)
        
        approval_request = {
            'request_id': f"APR-{work_order['work_order_id']}",
            'work_order_id': work_order['work_order_id'],
            'approval_type': 'preparation',
            'approver': approver,
            'status': 'pending',
            'requested_at': datetime.utcnow().isoformat()
        }
        
        # Send notification
        await self.send_approval_notification(approval_request, work_order)
        
        # Save approval request
        response = requests.post(
            f"{self.aimms_api_url}/api/work-orders/approvals",
            json=approval_request,
            headers={'X-API-Key': AIMMS_API_KEY}
        )
        
        return response.json()
    
    async def handle_approval_response(
        self,
        approval_request_id: str,
        approved: bool,
        approver_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        ✅ STEP 5-7: Handle approval response and proceed with workflow
        """
        
        # Get approval request and work order
        approval = await self.get_approval_request(approval_request_id)
        work_order = await self.get_work_order(approval['work_order_id'])
        
        if not approved:
            # Rejected - close work order
            await self.close_work_order(
                work_order['work_order_id'],
                reason='rejected_by_approver',
                notes=approver_notes
            )
            
            return {
                'status': 'rejected',
                'work_order_id': work_order['work_order_id']
            }
        
        # ✅ STEP 5: Send AIMMS notification with preparation form
        await self.send_preparation_notification(work_order)
        
        # Update work order status
        await self.update_work_order(work_order['work_order_id'], {
            'status': 'preparing',
            'approved_at': datetime.utcnow().isoformat(),
            'approver': approval['approver'],
            'approver_notes': approver_notes
        })
        
        return {
            'status': 'approved_awaiting_preparation',
            'work_order_id': work_order['work_order_id']
        }
    
    async def send_preparation_notification(
        self,
        work_order: Dict[str, Any]
    ) -> None:
        """
        ✅ STEP 5: Send notification to AIMMS with preparation form
        
        Preparer fills in:
        - Vendor selection
        - Manpower allocation
        - Date & time scheduling
        - Inspection requirements
        """
        
        notification = {
            'type': 'work_order_preparation',
            'work_order_id': work_order['work_order_id'],
            'title': f"Prepare Work Order: {work_order['work_order_id']}",
            'message': f"Work order approved and ready for preparation. Please complete the preparation form.",
            'priority': work_order['priority'],
            'preparation_form_url': f"{AIMMS_URL}/work-orders/{work_order['work_order_id']}/prepare",
            'work_order_details': work_order
        }
        
        # Send to workspace
        await self.send_workspace_notification(notification)
        
        print(f"✅ Preparation notification sent to workspace")
    
    async def complete_preparation(
        self,
        work_order_id: str,
        preparation_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ STEP 6: Complete preparation with all details
        
        Preparation data includes:
        - vendor: Selected vendor
        - manpower: Assigned technicians
        - scheduled_date: Date scheduled
        - scheduled_time: Time scheduled
        - inspection_required: Boolean
        - estimated_duration: Hours
        - special_requirements: Notes
        """
        
        # Update work order with preparation details
        await self.update_work_order(work_order_id, {
            'vendor': preparation_data['vendor'],
            'manpower': preparation_data['manpower'],
            'scheduled_date': preparation_data['scheduled_date'],
            'scheduled_time': preparation_data['scheduled_time'],
            'inspection_required': preparation_data['inspection_required'],
            'estimated_duration': preparation_data['estimated_duration'],
            'special_requirements': preparation_data.get('special_requirements'),
            'status': 'prepared',
            'prepared_at': datetime.utcnow().isoformat()
        })
        
        # ✅ STEP 7: Request final approval
        final_approval = await self.request_final_approval(work_order_id)
        
        return {
            'status': 'prepared_awaiting_final_approval',
            'work_order_id': work_order_id,
            'final_approval_id': final_approval['request_id']
        }
    
    async def handle_final_approval(
        self,
        approval_request_id: str,
        approved: bool
    ) -> Dict[str, Any]:
        """
        ✅ STEP 7 (continued): Handle final approval and send to CMMS
        """
        
        approval = await self.get_approval_request(approval_request_id)
        work_order = await self.get_work_order(approval['work_order_id'])
        
        if not approved:
            # Send back to preparation
            await self.update_work_order(work_order['work_order_id'], {
                'status': 'preparing'
            })
            
            return {
                'status': 'returned_to_preparation',
                'work_order_id': work_order['work_order_id']
            }
        
        # ✅ STEP 7: Send to CMMS or email
        if self.cmms_integration_enabled():
            result = await self.send_to_cmms(work_order)
        else:
            result = await self.send_by_email(work_order)
        
        # Update status
        await self.update_work_order(work_order['work_order_id'], {
            'status': 'active',
            'cmms_work_order_id': result.get('cmms_wo_id'),
            'sent_to_cmms_at': datetime.utcnow().isoformat()
        })
        
        # ✅ STEP 8: Send client notification (if tenant request)
        if work_order.get('source') == 'tenant' or work_order.get('requester_type') == 'tenant':
            await self.send_client_notification(work_order)
        
        return {
            'status': 'active',
            'work_order_id': work_order['work_order_id'],
            'cmms_wo_id': result.get('cmms_wo_id')
        }
    
    async def send_to_cmms(
        self,
        work_order: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ STEP 7: Send work order to CMMS (Maximo or SAP PM)
        """
        
        # Convert to CMMS format
        cmms_wo = {
            'wonum': work_order['work_order_id'],
            'description': work_order['issue_description'],
            'assetnum': work_order['asset'],
            'location': work_order['location'],
            'priority': self.convert_priority_to_cmms(work_order['priority']),
            'worktype': self.convert_type_to_cmms(work_order['request_type']),
            'schedstart': work_order.get('scheduled_date'),
            'vendor': work_order.get('vendor'),
            'crew': work_order.get('manpower'),
            'estdur': work_order.get('estimated_duration', 0),
            'status': 'APPROVED'
        }
        
        # Send to CMMS
        response = requests.post(
            f"{self.cmms_api_url}/api/workorders",
            json=cmms_wo,
            headers={'Authorization': f'Bearer {CMMS_API_KEY}'}
        )
        
        result = response.json()
        
        print(f"✅ Work order sent to CMMS: {result['wonum']}")
        
        return {
            'cmms_wo_id': result['wonum'],
            'status': 'success'
        }
    
    async def send_client_notification(
        self,
        work_order: Dict[str, Any]
    ) -> None:
        """
        ✅ STEP 8: Send notification to client (tenant)
        """
        
        email_body = f"""Dear {work_order['requester_name']},

Your maintenance request has been scheduled and approved.

Work Order: {work_order['work_order_id']}
Asset: {work_order['asset']}
Location: {work_order['location']}

Scheduled Date: {work_order.get('scheduled_date', 'To be determined')}
Scheduled Time: {work_order.get('scheduled_time', 'To be determined')}

Assigned Vendor: {work_order.get('vendor', 'To be assigned')}

We will notify you once the work is completed.

Thank you,
Facilities Management Team
"""

        await self.send_email(
            to=work_order['requester_email'],
            subject=f"Work Order Scheduled: {work_order['work_order_id']}",
            body=email_body
        )
        
        print(f"✅ Client notification sent to {work_order['requester_email']}")
```

---

## PPM-Based Work Orders

### Automated PPM Work Order Creation

```python
# services/ppm_work_order_scheduler.py

from typing import Dict, Any, List
from datetime import datetime, timedelta
import asyncio

class PPMWorkOrderScheduler:
    """
    Automated PPM-Based Work Order Creation
    
    Runs hourly to check PPM schedules and auto-creates work orders
    """
    
    def __init__(
        self,
        aimms_api_url: str
    ):
        self.aimms_api_url = aimms_api_url
    
    async def run_scheduler(self):
        """
        ✅ Main scheduler loop - runs every hour
        """
        
        while True:
            try:
                print(f"\n{'='*70}")
                print(f"🔄 PPM SCHEDULER RUN")
                print(f"Time: {datetime.utcnow().isoformat()}")
                print(f"{'='*70}\n")
                
                # Get due PPM schedules
                due_schedules = await self.get_due_ppm_schedules()
                
                print(f"✅ Found {len(due_schedules)} due PPM schedules")
                
                # Create work orders
                for schedule in due_schedules:
                    await self.create_ppm_work_order(schedule)
                
                print(f"\n✅ Scheduler run complete")
                
            except Exception as e:
                print(f"❌ Scheduler error: {e}")
            
            # Wait 1 hour
            await asyncio.sleep(3600)
    
    async def get_due_ppm_schedules(self) -> List[Dict[str, Any]]:
        """
        ✅ Get PPM schedules that are due
        
        Checks frequency and last execution date
        """
        
        response = requests.get(
            f"{self.aimms_api_url}/api/ppm-schedules/due",
            headers={'X-API-Key': AIMMS_API_KEY}
        )
        
        schedules = response.json()['schedules']
        
        due_schedules = []
        
        for schedule in schedules:
            if self.is_schedule_due(schedule):
                due_schedules.append(schedule)
        
        return due_schedules
    
    def is_schedule_due(
        self,
        schedule: Dict[str, Any]
    ) -> bool:
        """
        ✅ Check if PPM schedule is due
        """
        
        frequency = schedule['frequency']  # daily, weekly, monthly, quarterly, annually
        last_executed = schedule.get('last_executed')
        
        if not last_executed:
            return True  # Never executed
        
        last_date = datetime.fromisoformat(last_executed)
        now = datetime.utcnow()
        
        if frequency == 'daily':
            return (now - last_date).days >= 1
        elif frequency == 'weekly':
            return (now - last_date).days >= 7
        elif frequency == 'monthly':
            return (now - last_date).days >= 30
        elif frequency == 'quarterly':
            return (now - last_date).days >= 90
        elif frequency == 'annually':
            return (now - last_date).days >= 365
        
        return False
    
    async def create_ppm_work_order(
        self,
        schedule: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ Create work order from PPM schedule
        
        Auto-populates all fields from schedule
        """
        
        print(f"\n📋 Creating PPM work order for: {schedule['asset_name']}")
        
        work_order = {
            'work_order_id': f"WO-PPM-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'source': 'ppm_schedule',
            'source_reference': schedule['schedule_id'],
            
            # Pre-populated from schedule
            'asset': schedule['asset_id'],
            'asset_name': schedule['asset_name'],
            'location': schedule['location'],
            'task_description': schedule['task_description'],
            'task_type': schedule['task_type'],
            'priority': schedule.get('priority', 'medium'),
            'request_type': 'maintenance',
            
            # Scheduling
            'frequency': schedule['frequency'],
            'estimated_duration': schedule.get('estimated_duration_minutes', 60),
            'required_skills': schedule.get('required_skills', []),
            'required_tools': schedule.get('required_tools', []),
            'required_parts': schedule.get('required_parts', []),
            'safety_requirements': schedule.get('safety_requirements', []),
            
            # Status
            'status': 'pending_approval',
            'approval_type': 'simple',  # Just approval, no preparation needed
            
            # Metadata
            'created_at': datetime.utcnow().isoformat(),
            'created_by': 'ppm_scheduler',
            
            # Journey log reference
            'journey_log_id': None
        }
        
        # Save work order
        response = requests.post(
            f"{self.aimms_api_url}/api/work-orders",
            json=work_order,
            headers={'X-API-Key': AIMMS_API_KEY}
        )
        
        saved_wo = response.json()
        
        # Create journey log
        journey = await self.create_journey_log(saved_wo)
        saved_wo['journey_log_id'] = journey['jlog_id']
        
        # Update work order
        await self.update_work_order(saved_wo['work_order_id'], {
            'journey_log_id': journey['jlog_id']
        })
        
        # Send approval notification
        await self.send_ppm_approval_notification(saved_wo, schedule)
        
        # Update schedule last_executed
        await self.update_schedule_execution(schedule['schedule_id'])
        
        print(f"✅ PPM work order created: {saved_wo['work_order_id']}")
        
        return saved_wo
    
    async def send_ppm_approval_notification(
        self,
        work_order: Dict[str, Any],
        schedule: Dict[str, Any]
    ) -> None:
        """
        ✅ Send notification for PPM work order approval
        """
        
        notification = {
            'type': 'ppm_work_order_approval',
            'work_order_id': work_order['work_order_id'],
            'title': f"PPM Work Order Ready: {work_order['asset_name']}",
            'message': f"Scheduled {schedule['frequency']} maintenance for {work_order['asset_name']}",
            'priority': work_order['priority'],
            'details': {
                'asset': work_order['asset_name'],
                'location': work_order['location'],
                'task': work_order['task_description'],
                'frequency': schedule['frequency']
            },
            'actions': [
                {
                    'label': 'View Details',
                    'url': f"{AIMMS_URL}/work-orders/{work_order['work_order_id']}"
                },
                {
                    'label': 'Approve',
                    'action': 'approve'
                },
                {
                    'label': 'Modify',
                    'action': 'modify'
                }
            ]
        }
        
        await self.send_workspace_notification(notification)
```

---

## Intelligent Work Order Engine

### 15-Step AI-Powered Creation

```python
# services/intelligent_work_order_engine.py

from typing import Dict, Any, List, Optional
from datetime import datetime
import anthropic
import json

class IntelligentWorkOrderEngine:
    """
    Master Work Order Creation Engine
    
    15-Step intelligent assessment and creation process
    Uses AI, workspace queries, and historical data
    """
    
    def __init__(
        self,
        aimms_api_url: str,
        cmms_api_url: str,
        bms_api_url: str,
        claude_api_key: str
    ):
        self.aimms_api_url = aimms_api_url
        self.cmms_api_url = cmms_api_url
        self.bms_api_url = bms_api_url
        self.claude_client = anthropic.Anthropic(api_key=claude_api_key)
    
    async def create_intelligent_work_order(
        self,
        source: str,
        request_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ MASTER CREATION FUNCTION
        
        Executes all 15 steps with AI intelligence
        """
        
        print(f"\n{'='*70}")
        print(f"🧠 INTELLIGENT WORK ORDER CREATION")
        print(f"Source: {source}")
        print(f"{'='*70}\n")
        
        work_order = {
            'work_order_id': f"WO-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            'source': source,
            'request_data': request_data,
            'created_at': datetime.utcnow().isoformat()
        }
        
        # ✅ STEP 1: Source Identification
        print(f"Step 1: Source identification")
        work_order['source_type'] = source
        work_order['source_metadata'] = request_data.get('metadata', {})
        
        # ✅ STEP 2: Workspace Query-Based Data Collection
        print(f"Step 2: Collecting data from workspace")
        workspace_data = await self.collect_workspace_data(request_data)
        work_order.update(workspace_data)
        
        # ✅ STEP 3: AI Criticality Assessment
        print(f"Step 3: AI criticality assessment")
        criticality = await self.assess_criticality(work_order)
        work_order['criticality'] = criticality
        print(f"   Result: {criticality['level']} criticality")
        
        # ✅ STEP 4: Safety Condition Identification
        print(f"Step 4: Safety condition identification")
        safety = await self.identify_safety_conditions(work_order)
        work_order['safety'] = safety
        if safety['critical_safety_detected']:
            print(f"   ⚠️  Critical safety detected: {safety['safety_types']}")
            # Activate response time tracking on dashboard
            await self.activate_safety_response_time(work_order)
        
        # ✅ STEP 5: Compliance Detection
        print(f"Step 5: Compliance detection")
        compliance = await self.detect_compliance_requirements(work_order)
        work_order['compliance'] = compliance
        if compliance['compliance_required']:
            print(f"   Compliance tracking activated: {compliance['types']}")
        
        # ✅ STEP 6: Location & Site Validation
        print(f"Step 6: Location and site validation")
        location_data = await self.validate_location(work_order)
        work_order['location_data'] = location_data
        
        # ✅ STEP 7: Asset Intelligence Lookup
        print(f"Step 7: Asset intelligence lookup")
        asset_intel = await self.lookup_asset_intelligence(work_order)
        work_order['asset_intelligence'] = asset_intel
        print(f"   Warranty status: {asset_intel.get('warranty_status')}")
        print(f"   Known issues: {len(asset_intel.get('known_issues', []))}")
        
        # ✅ STEP 8: Site Clearance Certificate Check
        print(f"Step 8: Site clearance check")
        clearance = await self.check_site_clearance(work_order)
        work_order['site_clearance'] = clearance
        if clearance['required'] and not clearance['certificate_provided']:
            print(f"   ⚠️  Site clearance required but not provided")
        
        # ✅ STEP 9: Warranty & Inspection Intelligence
        print(f"Step 9: Warranty and inspection intelligence")
        warranty_intel = await self.get_warranty_inspection_intelligence(work_order)
        work_order['warranty_intelligence'] = warranty_intel
        if warranty_intel['recommendations']:
            print(f"   Recommendations: {len(warranty_intel['recommendations'])}")
        
        # ✅ STEP 10: Spare Parts Availability
        print(f"Step 10: Spare parts availability check")
        parts = await self.check_spare_parts_availability(work_order)
        work_order['spare_parts'] = parts
        
        # If unavailable, check Outlook for order status
        if parts['unavailable_parts']:
            print(f"   ⚠️  {len(parts['unavailable_parts'])} parts unavailable")
            outlook_data = await self.check_outlook_for_parts_orders(
                parts['unavailable_parts']
            )
            work_order['parts_order_status'] = outlook_data
            print(f"   Checked Outlook for order status")
        
        # ✅ STEP 11: Vendor Suggestion
        print(f"Step 11: Vendor suggestion (composite scoring)")
        vendors = await self.suggest_vendors(work_order)
        work_order['suggested_vendors'] = vendors[:3]  # Top 3
        print(f"   Top vendor: {vendors[0]['name']} (score: {vendors[0]['score']}/100)")
        
        # ✅ STEP 12: Resource Allocation
        print(f"Step 12: Resource allocation (smart matching)")
        resources = await self.allocate_resources(work_order)
        work_order['resource_allocation'] = resources
        print(f"   Suggested: {resources['technician_name']}")
        
        # ✅ STEP 13: Smart Scheduling
        print(f"Step 13: Smart scheduling (constraint-based)")
        schedule = await self.smart_scheduling(work_order)
        work_order['schedule'] = schedule
        print(f"   Suggested date: {schedule['suggested_date']}")
        print(f"   Estimated duration: {schedule['estimated_duration_hours']} hours")
        
        # ✅ STEP 14: Workspace Pinning
        print(f"Step 14: Pinning to workspace")
        workspace_pin = await self.pin_to_workspace(work_order)
        work_order['workspace_pin_id'] = workspace_pin['pin_id']
        
        # ✅ STEP 15: Journey Log Creation
        print(f"Step 15: Creating journey log")
        journey = await self.create_journey_log(work_order)
        work_order['journey_log_id'] = journey['jlog_id']
        
        # Save complete work order
        saved_wo = await self.save_work_order(work_order)
        
        print(f"\n{'='*70}")
        print(f"✅ INTELLIGENT WORK ORDER CREATED")
        print(f"Work Order ID: {saved_wo['work_order_id']}")
        print(f"Journey Log: {saved_wo['journey_log_id']}")
        print(f"{'='*70}\n")
        
        return saved_wo
    
    async def assess_criticality(
        self,
        work_order: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        ✅ STEP 3: AI-powered criticality assessment
        
        Analyzes 4 factors:
        - Safety implications
        - Operational impact
        - Financial impact
        - Compliance requirements
        """
        
        prompt = f"""Assess the criticality of this maintenance request:

Asset: {work_order.get('asset_name', 'Unknown')}
Location: {work_order.get('location', 'Unknown')}
Issue: {work_order.get('issue_description', work_order.get('task_description', 'Unknown'))}
Asset Type: {work_order.get('asset_type', 'Unknown')}

Provide criticality assessment in JSON format:
{{
    "level": "critical|high|medium|low",
    "safety_score": 0-100,
    "operational_score": 0-100,
    "financial_score": 0-100,
    "compliance_score": 0-100,
    "overall_score": 0-100,
    "reasoning": "explanation of assessment",
    "response_time_hours": number
}}

Consider:
- Safety: Risk to people, confined spaces, hazardous materials
- Operational: Impact on building operations, tenant services
- Financial: Cost of delay, asset replacement vs repair
- Compliance: Regulatory requirements, code violations
"""

        message = self.claude_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        response_text = message.content[0].text
        
        if "```json" in response_text:
            json_start = response_text.find("```json") + 7
            json_end = response_text.find("```", json_start)
            response_text = response_text[json_start:json_end].strip()
        
        return json.loads(response_text)
    
    async def suggest_vendors(
        self,
        work_order: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        ✅ STEP 11: Vendor suggestion with composite scoring
        
        Scoring factors:
        - Rating (35%)
        - Availability (30%)
        - Expertise match (25%)
        - Response time (5%)
        - Budget fit (5%)
        """
        
        # Get all qualified vendors
        vendors = await self.get_qualified_vendors(work_order)
        
        # Score each vendor
        scored_vendors = []
        
        for vendor in vendors:
            # Calculate scores
            rating_score = vendor['rating'] * 20  # Convert 5-star to 100
            
            availability_score = 100 if vendor['available'] else 30
            
            expertise_score = self.calculate_expertise_match(
                vendor['expertise'],
                work_order.get('required_skills', [])
            )
            
            response_score = 100 - min(vendor['avg_response_hours'] * 2, 100)
            
            budget_score = self.calculate_budget_fit(
                vendor['typical_rate'],
                work_order.get('estimated_budget', 0)
            )
            
            # Composite score
            composite_score = (
                rating_score * 0.35 +
                availability_score * 0.30 +
                expertise_score * 0.25 +
                response_score * 0.05 +
                budget_score * 0.05
            )
            
            scored_vendors.append({
                'vendor_id': vendor['id'],
                'name': vendor['name'],
                'rating': vendor['rating'],
                'expertise_match': expertise_score,
                'available': vendor['available'],
                'score': round(composite_score, 2),
                'contact': vendor['contact']
            })
        
        # Sort by score
        scored_vendors.sort(key=lambda x: x['score'], reverse=True)
        
        return scored_vendors
```

*[Continue with remaining implementation sections...]*

---

## Complete Work Order API Reference

### Core Endpoints

```python
# POST /api/work-orders
# Create new work order

# GET /api/work-orders/{id}
# Get work order details

# PATCH /api/work-orders/{id}
# Update work order

# POST /api/work-orders/{id}/approve
# Approve work order

# POST /api/work-orders/{id}/prepare
# Complete preparation

# POST /api/work-orders/{id}/close
# Close work order

# GET /api/work-orders/active
# Get all active work orders

# GET /api/work-orders/pending-approval
# Get pending approvals
```

---

## Summary

**This comprehensive module provides:**

✅ Multi-source work order creation
✅ 8-step email processing workflow
✅ Automated PPM scheduling
✅ 15-step intelligent creation engine
✅ Complete lifecycle management
✅ CMMS bidirectional integration
✅ Real-time tracking and notifications
✅ Complete audit trail
✅ GPT-powered conversational interface (Section 2a)
✅ Chat UI integrated into the React frontend

---

## Section 3 — React Frontend Chat UI

### Location
File: `svc-ai-schema-mapper-ui/src/components/workorders/WorkOrderPanel.tsx`

### Overview
The Work Orders section in the React UI now has two tabs:

| Tab | Purpose |
|-----|---------|
| **Overview** | Existing dashboard — pipeline diagram, stats, WO table |
| **Chat Assistant** | New GPT conversational interface |

### Chat UI Components

```
WorkOrderPanel (tab switcher)
├── Overview tab → existing content unchanged
└── Chat Assistant tab
    └── ChatPanel
        ├── Header (session ID, New conversation button)
        ├── Message list (scrollable)
        │   ├── ChatBubble (user message — right-aligned, indigo)
        │   ├── ChatBubble (assistant reply — left-aligned, white)
        │   │   └── WOCreatedCard (shown when work_order ≠ null)
        │   └── TypingIndicator (animated dots while awaiting reply)
        ├── Suggestion chips (empty state only)
        └── Input bar (textarea + send button)
```

### ChatPanel behaviour

1. **Empty state** — shows 4 preset suggestion chips the user can click to pre-fill the input.
2. **Sending a message** — calls `POST {woServiceUrl}/api/chat/` with `{ message, session_id }`.
3. **Session persistence** — `session_id` (UUID) stored in React state; sent on every subsequent turn. Starting a new conversation resets it to `null`.
4. **Work order card** — when the API response includes a non-null `work_order`, a `WOCreatedCard` is rendered beneath the assistant bubble showing: WO ID, priority/status/source badges, issue description, asset, location, vendor, scheduled date.
5. **Typing indicator** — animated three-dot bounce shown while the API call is in-flight.
6. **Error handling** — errors shown in a dismissable banner above the input bar; the user message is re-inserted into the input so the user can retry.
7. **Keyboard shortcut** — `Enter` sends, `Shift+Enter` inserts a newline.
8. **Fallback URL** — same `127.0.0.1` ↔ `localhost` fallback as the rest of the panel.

### New types added

```typescript
interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  work_order?: Record<string, unknown> | null
  ts: number   // Date.now() for timestamp display
}
```

### Sub-components added

| Component | Description |
|-----------|-------------|
| `WOCreatedCard` | Green summary card rendered under assistant bubble when a WO is created |
| `TypingIndicator` | Animated three-dot bounce while awaiting API response |
| `ChatBubble` | Renders a single chat message (user or assistant), includes optional `WOCreatedCard` |
| `ChatPanel` | Full chat interface — state, input, message list, API calls |

### API calls made by ChatPanel

| Method | URL | Purpose |
|--------|-----|---------|
| `POST` | `{woServiceUrl}/api/chat/` | Send user message, receive agent reply |

The `GET /api/chat/{session_id}/history` endpoint is available for restoring history on page reload (not yet wired in the UI — future enhancement).

### Lucide icons added
`Bot`, `RotateCcw`, `Plus` (imported alongside existing icon set)

**Production-ready work order management!** 🚀
