# Intelligent Work Order Engine - Complete Implementation Guide with Sample Data

## Overview

This document provides **complete implementation details** for all 15 steps of the Intelligent Work Order Engine, including:
- Actual code implementations
- Sample data at each step
- Database queries
- API calls
- Logic explanations
- Real-world examples

---

## Table of Contents

1. [Sample Input Data](#sample-input-data)
2. [Step-by-Step Implementation](#step-by-step-implementation)
3. [Database Schema](#database-schema)
4. [Complete Working Example](#complete-working-example)

---

## Sample Input Data

### Example 1: Email-Based Work Order

```python
# Incoming request from email processor
request_data = {
    'source': 'email',
    'email_id': 'email-20240427-001',
    'asset': 'HVAC-301',
    'asset_name': 'Rooftop Unit #3',
    'location': 'Building A, Roof Level',
    'issue_description': 'HVAC unit making loud grinding noise and reduced cooling capacity',
    'priority': 'high',
    'requester_name': 'John Smith',
    'requester_email': 'john.smith@company.com',
    'metadata': {
        'received_at': '2024-04-27T09:30:00Z',
        'subject': 'Urgent: HVAC Unit Issue - Building A'
    }
}
```

### Example 2: PPM Schedule-Based

```python
request_data = {
    'source': 'ppm_schedule',
    'schedule_id': 'PPM-SCH-2024-045',
    'asset': 'CHILLER-102',
    'asset_name': 'Central Chiller Unit #2',
    'location': 'Mechanical Room B2',
    'task_description': 'Quarterly chiller maintenance and inspection',
    'task_type': 'preventive_maintenance',
    'priority': 'medium',
    'frequency': 'quarterly',
    'metadata': {
        'last_executed': '2024-01-27',
        'schedule_created': '2023-01-15'
    }
}
```

---

## Step-by-Step Implementation

### STEP 1: Source Identification

**Purpose:** Identify and classify the work order source

**Implementation:**

```python
async def step_1_source_identification(
    self,
    source: str,
    request_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 1: Source Identification
    
    Classifies the source and extracts metadata
    """
    
    source_mapping = {
        'email': {
            'type': 'reactive',
            'requires_preparation': True,
            'approval_type': 'preparation',
            'auto_journey': True
        },
        'ppm_schedule': {
            'type': 'preventive',
            'requires_preparation': False,
            'approval_type': 'simple',
            'auto_journey': True
        },
        'manual': {
            'type': 'reactive',
            'requires_preparation': True,
            'approval_type': 'full',
            'auto_journey': True
        },
        'tenant_request': {
            'type': 'reactive',
            'requires_preparation': True,
            'approval_type': 'preparation',
            'auto_journey': True,
            'client_notification': True
        },
        'remediation': {
            'type': 'proactive',
            'requires_preparation': False,
            'approval_type': 'full',
            'auto_journey': True,
            'special_handling': 'remediation_workflow'
        }
    }
    
    source_config = source_mapping.get(source, source_mapping['manual'])
    
    return {
        'source_type': source,
        'source_classification': source_config['type'],
        'requires_preparation': source_config['requires_preparation'],
        'approval_type': source_config['approval_type'],
        'auto_journey': source_config['auto_journey'],
        'client_notification': source_config.get('client_notification', False),
        'special_handling': source_config.get('special_handling'),
        'source_metadata': request_data.get('metadata', {})
    }
```

**Sample Output:**

```python
{
    'source_type': 'email',
    'source_classification': 'reactive',
    'requires_preparation': True,
    'approval_type': 'preparation',
    'auto_journey': True,
    'client_notification': False,
    'special_handling': None,
    'source_metadata': {
        'received_at': '2024-04-27T09:30:00Z',
        'subject': 'Urgent: HVAC Unit Issue - Building A'
    }
}
```

---

### STEP 2: Workspace Query-Based Data Collection

**Purpose:** Collect additional data using closed-answer workspace queries

**Implementation:**

```python
async def step_2_collect_workspace_data(
    self,
    request_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 2: Workspace Query-Based Data Collection
    
    Uses workspace queries to gather additional context
    Queries are closed-answer format for structured data
    """
    
    collected_data = {}
    
    # Query 1: Get asset details
    asset_query = {
        'query': f"Get complete details for asset {request_data['asset']}",
        'type': 'asset_lookup',
        'expected_format': 'structured'
    }
    
    asset_data = await self.execute_workspace_query(asset_query)
    collected_data['asset_details'] = asset_data
    
    # Query 2: Get location details
    location_query = {
        'query': f"Get location details for {request_data['location']}",
        'type': 'location_lookup',
        'expected_format': 'structured'
    }
    
    location_data = await self.execute_workspace_query(location_query)
    collected_data['location_details'] = location_data
    
    # Query 3: Get recent maintenance history
    history_query = {
        'query': f"Get last 5 maintenance records for {request_data['asset']}",
        'type': 'history_lookup',
        'expected_format': 'list'
    }
    
    history_data = await self.execute_workspace_query(history_query)
    collected_data['maintenance_history'] = history_data
    
    # Query 4: Check if asset has active work orders
    active_wo_query = {
        'query': f"Are there any active work orders for {request_data['asset']}?",
        'type': 'boolean_check',
        'expected_format': 'boolean'
    }
    
    active_wos = await self.execute_workspace_query(active_wo_query)
    collected_data['has_active_work_orders'] = active_wos
    
    return collected_data

async def execute_workspace_query(
    self,
    query: Dict[str, Any]
) -> Any:
    """
    Execute a workspace query and return structured data
    """
    
    # Call workspace API
    response = requests.post(
        f"{self.aimms_api_url}/api/workspace/query",
        json=query,
        headers={'X-API-Key': AIMMS_API_KEY}
    )
    
    return response.json()['data']
```

**Sample Workspace Query Response:**

```python
{
    'asset_details': {
        'asset_id': 'HVAC-301',
        'asset_name': 'Rooftop Unit #3',
        'asset_type': 'HVAC',
        'manufacturer': 'Carrier',
        'model': '50TC-030',
        'serial_number': 'SN-12345-ABC',
        'installation_date': '2018-03-15',
        'years_in_service': 6.1,
        'warranty_status': 'expired',
        'warranty_expiry_date': '2023-03-15',
        'location': 'Building A, Roof Level',
        'criticality': 'high',
        'replacement_cost': 85000
    },
    'location_details': {
        'building': 'Building A',
        'floor': 'Roof Level',
        'zone': 'North Zone',
        'occupancy_type': 'Office',
        'occupied': True,
        'access_restrictions': ['roof_access_permit_required'],
        'site_clearance_required': True
    },
    'maintenance_history': [
        {
            'date': '2024-01-15',
            'type': 'inspection',
            'findings': 'Belt tension low, filter 60% clogged',
            'technician': 'Mike Johnson'
        },
        {
            'date': '2023-10-20',
            'type': 'repair',
            'issue': 'Fan motor bearing replacement',
            'technician': 'Sarah Williams'
        },
        {
            'date': '2023-07-10',
            'type': 'inspection',
            'findings': 'All systems normal',
            'technician': 'Mike Johnson'
        },
        {
            'date': '2023-04-05',
            'type': 'repair',
            'issue': 'Refrigerant leak repair',
            'technician': 'John Davis'
        },
        {
            'date': '2023-01-12',
            'type': 'inspection',
            'findings': 'Minor vibration detected',
            'technician': 'Mike Johnson'
        }
    ],
    'has_active_work_orders': False
}
```

---

### STEP 3: AI Criticality Assessment

**Purpose:** Use AI to assess criticality based on 4 factors

**Implementation:**

```python
async def step_3_assess_criticality(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 3: AI Criticality Assessment
    
    Uses Claude AI to assess:
    1. Safety implications (0-100)
    2. Operational impact (0-100)
    3. Financial impact (0-100)
    4. Compliance requirements (0-100)
    """
    
    # Prepare context for AI
    context = f"""Assess maintenance request criticality:

ASSET INFORMATION:
- Asset: {work_order.get('asset_name')} ({work_order.get('asset')})
- Type: {work_order.get('asset_details', {}).get('asset_type')}
- Age: {work_order.get('asset_details', {}).get('years_in_service')} years
- Location: {work_order.get('location')}
- Criticality Rating: {work_order.get('asset_details', {}).get('criticality')}

ISSUE:
{work_order.get('issue_description', work_order.get('task_description'))}

RECENT HISTORY:
{json.dumps(work_order.get('maintenance_history', [])[:3], indent=2)}

LOCATION CONTEXT:
- Occupied: {work_order.get('location_details', {}).get('occupied')}
- Access Restrictions: {work_order.get('location_details', {}).get('access_restrictions')}

Provide assessment in JSON format:
{{
    "safety_score": 0-100 (risk to people, confined spaces, hazardous conditions),
    "safety_factors": ["list of specific safety concerns"],
    
    "operational_score": 0-100 (impact on building operations, tenant services),
    "operational_impact": "description of operational consequences",
    
    "financial_score": 0-100 (cost of delay, asset damage risk, liability),
    "financial_impact": "description of financial consequences",
    
    "compliance_score": 0-100 (regulatory requirements, code violations),
    "compliance_factors": ["list of compliance issues"],
    
    "overall_score": 0-100 (weighted average),
    "criticality_level": "critical|high|medium|low",
    "response_time_hours": number (recommended response time),
    "reasoning": "detailed explanation of assessment"
}}

SCORING GUIDE:
- Safety: 90-100 = immediate danger, 70-89 = high risk, 50-69 = moderate, <50 = low
- Operational: 90-100 = building shutdown, 70-89 = major disruption, 50-69 = minor impact
- Financial: 90-100 = >$50K risk, 70-89 = $20K-50K, 50-69 = $5K-20K
- Compliance: 90-100 = code violation, 70-89 = regulatory risk, 50-69 = best practice
"""

    # Call Claude AI
    message = self.claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": context}]
    )
    
    response_text = message.content[0].text
    
    # Parse JSON response
    if "```json" in response_text:
        json_start = response_text.find("```json") + 7
        json_end = response_text.find("```", json_start)
        response_text = response_text[json_start:json_end].strip()
    
    assessment = json.loads(response_text)
    
    # Add timestamp
    assessment['assessed_at'] = datetime.utcnow().isoformat()
    assessment['assessed_by'] = 'claude_ai'
    
    return assessment
```

**Sample AI Response:**

```python
{
    "safety_score": 65,
    "safety_factors": [
        "Roof access required - fall protection needed",
        "Grinding noise suggests bearing failure - potential flywheel separation",
        "Reduced cooling in occupied space - heat stress risk if summer"
    ],
    
    "operational_score": 78,
    "operational_impact": "HVAC serves north zone office space with ~50 occupants. Reduced cooling capacity will cause discomfort and potential productivity loss. Grinding noise is disruptive to normal operations.",
    
    "financial_score": 72,
    "financial_impact": "Bearing failure if left unaddressed could lead to complete motor failure ($8,000 repair vs current $1,500). Reduced efficiency increasing energy costs ~$200/day. Asset is 6 years old, nearing replacement consideration at $85,000.",
    
    "compliance_score": 45,
    "compliance_factors": [
        "No immediate code violations",
        "ASHRAE 62.1 ventilation standards require functioning HVAC",
        "Occupant comfort within acceptable ASHRAE 55 ranges currently"
    ],
    
    "overall_score": 65,
    "criticality_level": "high",
    "response_time_hours": 24,
    "reasoning": "High-priority issue requiring 24-hour response. While not an immediate safety emergency, the grinding noise suggests bearing failure that could escalate to complete motor failure. The unit serves occupied space, creating operational impact. Financial risk is moderate but increasing. No critical compliance issues currently.",
    
    "assessed_at": "2024-04-27T10:15:00Z",
    "assessed_by": "claude_ai"
}
```

---

### STEP 4: Safety Condition Identification

**Purpose:** Identify specific safety conditions and activate tracking

**Implementation:**

```python
async def step_4_identify_safety_conditions(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 4: Safety Condition Identification
    
    Identifies specific safety conditions:
    - Confined spaces
    - Hot work
    - Electrical hazards
    - Lockout/Tagout requirements
    - Fall protection
    - Hazardous materials
    """
    
    safety_keywords = {
        'confined_space': [
            'tank', 'vessel', 'pit', 'vault', 'trench', 'tunnel',
            'confined', 'enclosed space', 'limited access'
        ],
        'hot_work': [
            'welding', 'cutting', 'grinding', 'torch', 'flame',
            'hot work', 'spark', 'ignition'
        ],
        'electrical': [
            'electrical', 'power', 'voltage', 'circuit', 'breaker',
            'transformer', 'panel', 'live', 'energized'
        ],
        'lockout_tagout': [
            'lockout', 'tagout', 'loto', 'de-energize', 'isolation',
            'disconnect', 'shut off'
        ],
        'fall_protection': [
            'roof', 'height', 'ladder', 'scaffold', 'elevated',
            'fall protection', 'above ground'
        ],
        'hazardous_materials': [
            'refrigerant', 'chemical', 'asbestos', 'lead', 'hazmat',
            'toxic', 'flammable', 'corrosive'
        ]
    }
    
    # Analyze work order text
    text_to_analyze = ' '.join([
        work_order.get('issue_description', ''),
        work_order.get('task_description', ''),
        work_order.get('location', ''),
        work_order.get('asset_name', '')
    ]).lower()
    
    detected_conditions = []
    
    for condition_type, keywords in safety_keywords.items():
        if any(keyword in text_to_analyze for keyword in keywords):
            detected_conditions.append(condition_type)
    
    # Check asset type for automatic conditions
    asset_type = work_order.get('asset_details', {}).get('asset_type', '').lower()
    
    if 'hvac' in asset_type or 'chiller' in asset_type:
        if 'refrigerant' not in text_to_analyze:
            detected_conditions.append('hazardous_materials')  # Refrigerant
    
    # Check location for automatic conditions
    location = work_order.get('location', '').lower()
    if 'roof' in location:
        if 'fall_protection' not in detected_conditions:
            detected_conditions.append('fall_protection')
    
    # Build safety requirements
    safety_requirements = []
    permits_required = []
    
    for condition in detected_conditions:
        if condition == 'confined_space':
            safety_requirements.append('Confined Space Entry Permit')
            permits_required.append('confined_space_permit')
        elif condition == 'hot_work':
            safety_requirements.append('Hot Work Permit')
            permits_required.append('hot_work_permit')
        elif condition == 'electrical':
            safety_requirements.append('Electrical Safety Lockout/Tagout')
            permits_required.append('loto_procedure')
        elif condition == 'fall_protection':
            safety_requirements.append('Fall Protection Equipment & Training')
            permits_required.append('fall_protection_plan')
        elif condition == 'hazardous_materials':
            safety_requirements.append('Hazmat Handling Certification')
            permits_required.append('hazmat_certification')
    
    critical_safety_detected = len(detected_conditions) > 0
    
    result = {
        'critical_safety_detected': critical_safety_detected,
        'safety_conditions': detected_conditions,
        'safety_types': [cond.replace('_', ' ').title() for cond in detected_conditions],
        'safety_requirements': safety_requirements,
        'permits_required': permits_required,
        'response_time_tracking': critical_safety_detected  # Activate if critical
    }
    
    return result
```

**Sample Output:**

```python
{
    'critical_safety_detected': True,
    'safety_conditions': [
        'fall_protection',
        'hazardous_materials',
        'lockout_tagout'
    ],
    'safety_types': [
        'Fall Protection',
        'Hazardous Materials',
        'Lockout Tagout'
    ],
    'safety_requirements': [
        'Fall Protection Equipment & Training',
        'Hazmat Handling Certification',
        'Electrical Safety Lockout/Tagout'
    ],
    'permits_required': [
        'fall_protection_plan',
        'hazmat_certification',
        'loto_procedure'
    ],
    'response_time_tracking': True
}
```

**Dashboard Activation Logic:**

```python
async def activate_safety_response_time(
    self,
    work_order: Dict[str, Any]
) -> None:
    """
    Activate safety response time tracking on dashboard
    """
    
    if not work_order.get('safety', {}).get('critical_safety_detected'):
        return
    
    dashboard_widget = {
        'type': 'safety_response_tracker',
        'work_order_id': work_order['work_order_id'],
        'asset': work_order['asset_name'],
        'safety_conditions': work_order['safety']['safety_types'],
        'response_time_hours': work_order.get('criticality', {}).get('response_time_hours', 24),
        'started_at': datetime.utcnow().isoformat(),
        'status': 'active',
        'alert_threshold_hours': work_order.get('criticality', {}).get('response_time_hours', 24)
    }
    
    # Add to dashboard
    requests.post(
        f"{self.aimms_api_url}/api/dashboard/widgets/safety-tracker",
        json=dashboard_widget,
        headers={'X-API-Key': AIMMS_API_KEY}
    )
```

---

### STEP 5: Compliance Detection

**Purpose:** Detect regulatory compliance requirements

**Implementation:**

```python
async def step_5_detect_compliance_requirements(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 5: Compliance Detection
    
    Detects compliance requirements:
    - Energy regulations
    - Water regulations
    - Gas regulations
    - Heat regulations
    - Environmental regulations
    - Building codes
    """
    
    compliance_keywords = {
        'energy': [
            'energy', 'power consumption', 'efficiency', 'ashrae 90.1',
            'energy star', 'electricity', 'kw', 'kwh'
        ],
        'water': [
            'water', 'plumbing', 'leak', 'flow rate', 'gpm',
            'water conservation', 'backflow'
        ],
        'gas': [
            'gas', 'natural gas', 'propane', 'gas line', 'gas leak',
            'combustion', 'boiler', 'furnace'
        ],
        'heat': [
            'heating', 'boiler', 'heat exchanger', 'steam', 'hot water',
            'thermal', 'temperature'
        ],
        'environmental': [
            'refrigerant', 'emissions', 'discharge', 'epa', 'environmental',
            'hazardous waste', 'pollution'
        ],
        'building_code': [
            'fire safety', 'egress', 'life safety', 'occupancy',
            'accessibility', 'ada', 'fire alarm', 'sprinkler'
        ]
    }
    
    # Analyze work order
    text_to_analyze = ' '.join([
        work_order.get('issue_description', ''),
        work_order.get('task_description', ''),
        work_order.get('asset_details', {}).get('asset_type', '')
    ]).lower()
    
    detected_compliance = []
    compliance_details = {}
    
    for compliance_type, keywords in compliance_keywords.items():
        if any(keyword in text_to_analyze for keyword in keywords):
            detected_compliance.append(compliance_type)
            
            # Add specific requirements
            if compliance_type == 'energy':
                compliance_details[compliance_type] = {
                    'regulations': ['ASHRAE 90.1', 'Energy Policy Act'],
                    'tracking_required': True,
                    'reporting': 'Energy consumption monitoring'
                }
            elif compliance_type == 'water':
                compliance_details[compliance_type] = {
                    'regulations': ['EPA Water Efficiency Standards'],
                    'tracking_required': True,
                    'reporting': 'Water usage tracking'
                }
            elif compliance_type == 'gas':
                compliance_details[compliance_type] = {
                    'regulations': ['Gas Safety Regulations'],
                    'tracking_required': True,
                    'reporting': 'Gas usage and safety inspections'
                }
            elif compliance_type == 'environmental':
                compliance_details[compliance_type] = {
                    'regulations': ['EPA Clean Air Act', 'Refrigerant Management'],
                    'tracking_required': True,
                    'reporting': 'Environmental impact documentation'
                }
    
    compliance_required = len(detected_compliance) > 0
    
    result = {
        'compliance_required': compliance_required,
        'types': detected_compliance,
        'details': compliance_details,
        'tracking_activated': compliance_required
    }
    
    if compliance_required:
        # Activate compliance tracking
        await self.activate_compliance_tracking(work_order, result)
    
    return result
```

**Sample Output:**

```python
{
    'compliance_required': True,
    'types': ['energy', 'environmental'],
    'details': {
        'energy': {
            'regulations': ['ASHRAE 90.1', 'Energy Policy Act'],
            'tracking_required': True,
            'reporting': 'Energy consumption monitoring'
        },
        'environmental': {
            'regulations': ['EPA Clean Air Act', 'Refrigerant Management'],
            'tracking_required': True,
            'reporting': 'Environmental impact documentation'
        }
    },
    'tracking_activated': True
}
```

---

### STEP 6: Location & Site Validation

**Purpose:** Validate location and check access requirements

**Implementation:**

```python
async def step_6_validate_location(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 6: Location & Site Validation
    
    Validates:
    - Location exists and is accessible
    - Building and zone identification
    - Access restrictions
    - Occupancy status
    - Parking availability for service vehicles
    """
    
    location = work_order.get('location')
    location_details = work_order.get('location_details', {})
    
    # Parse location string
    location_parts = location.split(',')
    building = location_parts[0].strip() if location_parts else None
    floor = location_parts[1].strip() if len(location_parts) > 1 else None
    room = location_parts[2].strip() if len(location_parts) > 2 else None
    
    # Validate location exists
    location_valid = await self.check_location_exists(building, floor, room)
    
    if not location_valid:
        return {
            'valid': False,
            'error': 'Location not found in system',
            'action_required': 'Verify location details'
        }
    
    # Get additional location data
    location_data = {
        'valid': True,
        'building': building,
        'floor': floor,
        'room': room,
        'zone': location_details.get('zone'),
        'occupied': location_details.get('occupied', True),
        'occupancy_type': location_details.get('occupancy_type'),
        'access_restrictions': location_details.get('access_restrictions', []),
        'access_hours': await self.get_access_hours(building),
        'parking_available': await self.check_parking_availability(building),
        'elevator_access': await self.check_elevator_access(building, floor),
        'special_instructions': await self.get_location_instructions(building, floor)
    }
    
    return location_data

async def check_location_exists(
    self,
    building: str,
    floor: str,
    room: str
) -> bool:
    """
    Check if location exists in database
    """
    
    query = """
    SELECT COUNT(*) as count
    FROM locations
    WHERE building = %s
    AND (floor = %s OR %s IS NULL)
    AND (room = %s OR %s IS NULL)
    """
    
    result = await self.db.execute(query, (building, floor, floor, room, room))
    
    return result[0]['count'] > 0
```

**Sample Output:**

```python
{
    'valid': True,
    'building': 'Building A',
    'floor': 'Roof Level',
    'room': None,
    'zone': 'North Zone',
    'occupied': True,
    'occupancy_type': 'Office',
    'access_restrictions': ['roof_access_permit_required'],
    'access_hours': {
        'weekday': '6:00 AM - 8:00 PM',
        'weekend': 'By appointment only'
    },
    'parking_available': True,
    'elevator_access': {
        'available': True,
        'max_load': '2000 lbs',
        'service_elevator': True
    },
    'special_instructions': [
        'Roof access requires escort by building security',
        'Use service elevator for equipment transport',
        'Notify tenants 24 hours before work on roof'
    ]
}
```

---

### STEP 7: Asset Intelligence Lookup

**Purpose:** Gather comprehensive asset intelligence

**Implementation:**

```python
async def step_7_lookup_asset_intelligence(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 7: Asset Intelligence Lookup
    
    Gathers:
    - Warranty status
    - Maintenance history analysis
    - Known issues from similar assets
    - Manufacturer bulletins
    - Parts availability history
    """
    
    asset_id = work_order['asset']
    asset_details = work_order.get('asset_details', {})
    
    # Get warranty information
    warranty_info = await self.get_warranty_info(asset_details)
    
    # Analyze maintenance history
    history_analysis = await self.analyze_maintenance_history(
        work_order.get('maintenance_history', [])
    )
    
    # Get known issues for this model
    known_issues = await self.get_known_issues(
        asset_details.get('manufacturer'),
        asset_details.get('model')
    )
    
    # Check manufacturer bulletins
    bulletins = await self.check_manufacturer_bulletins(
        asset_details.get('manufacturer'),
        asset_details.get('model')
    )
    
    # Get parts availability history
    parts_history = await self.get_parts_availability_history(asset_id)
    
    intelligence = {
        'warranty_status': warranty_info['status'],
        'warranty_expiry': warranty_info['expiry_date'],
        'warranty_coverage': warranty_info['coverage'],
        'under_warranty': warranty_info['under_warranty'],
        
        'maintenance_patterns': history_analysis['patterns'],
        'common_failures': history_analysis['common_failures'],
        'average_repair_cost': history_analysis['avg_cost'],
        'mtbf_days': history_analysis['mtbf_days'],
        
        'known_issues': known_issues,
        'manufacturer_bulletins': bulletins,
        
        'parts_commonly_needed': parts_history['common_parts'],
        'parts_lead_times': parts_history['lead_times'],
        
        'recommendations': self.generate_recommendations(
            warranty_info,
            history_analysis,
            known_issues
        )
    }
    
    return intelligence

async def analyze_maintenance_history(
    self,
    history: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Analyze maintenance history using AI
    """
    
    if not history:
        return {
            'patterns': [],
            'common_failures': [],
            'avg_cost': 0,
            'mtbf_days': None
        }
    
    prompt = f"""Analyze this maintenance history and identify patterns:

{json.dumps(history, indent=2)}

Provide analysis in JSON format:
{{
    "patterns": ["list of recurring patterns"],
    "common_failures": ["list of common failure modes"],
    "avg_cost": estimated average cost per repair,
    "mtbf_days": mean time between failures in days,
    "trend": "improving|stable|deteriorating",
    "recommendations": ["list of recommendations"]
}}
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

async def get_known_issues(
    self,
    manufacturer: str,
    model: str
) -> List[Dict[str, Any]]:
    """
    Get known issues for this asset model across company
    """
    
    query = """
    SELECT 
        issue_description,
        COUNT(*) as occurrence_count,
        AVG(repair_cost) as avg_cost,
        STRING_AGG(DISTINCT solution, '; ') as solutions
    FROM work_orders wo
    JOIN assets a ON wo.asset_id = a.asset_id
    WHERE a.manufacturer = %s
    AND a.model = %s
    AND wo.status = 'closed'
    GROUP BY issue_description
    HAVING COUNT(*) >= 3
    ORDER BY COUNT(*) DESC
    LIMIT 10
    """
    
    results = await self.db.execute(query, (manufacturer, model))
    
    return [
        {
            'issue': row['issue_description'],
            'occurrences': row['occurrence_count'],
            'avg_cost': row['avg_cost'],
            'known_solutions': row['solutions'].split('; ') if row['solutions'] else []
        }
        for row in results
    ]
```

**Sample Output:**

```python
{
    'warranty_status': 'expired',
    'warranty_expiry': '2023-03-15',
    'warranty_coverage': 'parts_and_labor',
    'under_warranty': False,
    
    'maintenance_patterns': [
        'Belt tension issues every 6 months',
        'Filter replacement needed quarterly',
        'Bearing replacements every 18 months'
    ],
    'common_failures': [
        'Fan motor bearing failure',
        'Belt wear',
        'Refrigerant leaks'
    ],
    'average_repair_cost': 2500,
    'mtbf_days': 180,
    
    'known_issues': [
        {
            'issue': 'Fan motor bearing failure',
            'occurrences': 8,
            'avg_cost': 3200,
            'known_solutions': [
                'Replace bearing assembly',
                'Upgrade to sealed bearings',
                'Implement vibration monitoring'
            ]
        },
        {
            'issue': 'Belt slippage and wear',
            'occurrences': 12,
            'avg_cost': 850,
            'known_solutions': [
                'Replace with high-performance belt',
                'Adjust tension to spec',
                'Check pulley alignment'
            ]
        }
    ],
    
    'manufacturer_bulletins': [
        {
            'bulletin_id': 'TB-50TC-2023-04',
            'title': 'Belt Tension Adjustment Procedure Update',
            'date': '2023-04-15',
            'summary': 'Updated tension specifications for improved reliability'
        }
    ],
    
    'parts_commonly_needed': [
        {
            'part': 'Fan motor bearing assembly',
            'part_number': 'P-50TC-BRG-001',
            'frequency': 'Every 18 months',
            'typical_lead_time': '5-7 days'
        },
        {
            'part': 'Drive belt',
            'part_number': 'P-50TC-BLT-002',
            'frequency': 'Every 6 months',
            'typical_lead_time': '2-3 days'
        }
    ],
    
    'parts_lead_times': {
        'P-50TC-BRG-001': '5-7 days',
        'P-50TC-BLT-002': '2-3 days',
        'P-50TC-FLT-010': '1 day'
    },
    
    'recommendations': [
        'Based on history, this likely requires bearing replacement',
        'Order bearing assembly now to avoid delay',
        'Consider vibration monitoring for early detection',
        'Schedule during off-hours due to noise disruption'
    ]
}
```

---

### STEP 8: Site Clearance Certificate Check

**Purpose:** Check if site clearance is required and available

**Implementation:**

```python
async def step_8_check_site_clearance(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 8: Site Clearance Certificate Check
    
    Checks location-based clearance requirements:
    - Roof access permits
    - Confined space entry
    - Hot work permits
    - Tenant notifications
    """
    
    location_data = work_order.get('location_data', {})
    safety_data = work_order.get('safety', {})
    
    clearance_required = False
    required_certificates = []
    
    # Check access restrictions
    if 'roof_access_permit_required' in location_data.get('access_restrictions', []):
        clearance_required = True
        required_certificates.append({
            'type': 'roof_access_permit',
            'name': 'Roof Access Permit',
            'validity_days': 30
        })
    
    # Check safety requirements
    for permit in safety_data.get('permits_required', []):
        clearance_required = True
        
        if permit == 'confined_space_permit':
            required_certificates.append({
                'type': 'confined_space_permit',
                'name': 'Confined Space Entry Permit',
                'validity_days': 1
            })
        elif permit == 'hot_work_permit':
            required_certificates.append({
                'type': 'hot_work_permit',
                'name': 'Hot Work Permit',
                'validity_days': 1
            })
    
    # Check if certificates are already on file
    certificates_status = []
    
    for cert in required_certificates:
        status = await self.check_certificate_exists(
            work_order['asset'],
            cert['type']
        )
        
        certificates_status.append({
            **cert,
            'on_file': status['exists'],
            'expiry_date': status.get('expiry_date'),
            'expired': status.get('expired', False),
            'upload_required': not status['exists'] or status.get('expired', False)
        })
    
    any_upload_required = any(cert['upload_required'] for cert in certificates_status)
    
    result = {
        'required': clearance_required,
        'certificates': certificates_status,
        'certificate_provided': not any_upload_required,
        'upload_url': f"{AIMMS_URL}/work-orders/{work_order['work_order_id']}/upload-certificates" if any_upload_required else None
    }
    
    return result

async def check_certificate_exists(
    self,
    asset_id: str,
    certificate_type: str
) -> Dict[str, Any]:
    """
    Check if valid certificate exists
    """
    
    query = """
    SELECT 
        certificate_id,
        expiry_date,
        CASE 
            WHEN expiry_date < CURRENT_DATE THEN true
            ELSE false
        END as expired
    FROM certificates
    WHERE asset_id = %s
    AND certificate_type = %s
    AND expiry_date >= CURRENT_DATE
    ORDER BY expiry_date DESC
    LIMIT 1
    """
    
    result = await self.db.execute(query, (asset_id, certificate_type))
    
    if result:
        return {
            'exists': True,
            'certificate_id': result[0]['certificate_id'],
            'expiry_date': result[0]['expiry_date'].isoformat(),
            'expired': result[0]['expired']
        }
    else:
        return {
            'exists': False
        }
```

**Sample Output:**

```python
{
    'required': True,
    'certificates': [
        {
            'type': 'roof_access_permit',
            'name': 'Roof Access Permit',
            'validity_days': 30,
            'on_file': True,
            'expiry_date': '2024-05-15',
            'expired': False,
            'upload_required': False
        },
        {
            'type': 'fall_protection_plan',
            'name': 'Fall Protection Plan',
            'validity_days': 90,
            'on_file': False,
            'expiry_date': None,
            'expired': False,
            'upload_required': True
        }
    ],
    'certificate_provided': False,
    'upload_url': 'https://aimms.com/work-orders/WO-20240427093000/upload-certificates'
}
```

---

### STEP 9: Warranty & Inspection Intelligence

**Purpose:** Provide warranty-based recommendations and inspection insights

**Implementation:**

```python
async def step_9_get_warranty_inspection_intelligence(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 9: Warranty & Inspection Intelligence
    
    Provides:
    - Warranty-specific recommendations
    - Required spare parts based on warranty
    - Recommended tools
    - Inspection schedule recommendations
    """
    
    asset_intel = work_order.get('asset_intelligence', {})
    warranty_status = asset_intel.get('warranty_status')
    
    # Get warranty manual data
    warranty_manual = await self.get_warranty_manual(
        work_order.get('asset_details', {}).get('manufacturer'),
        work_order.get('asset_details', {}).get('model')
    )
    
    intelligence = {
        'warranty_recommendations': [],
        'required_parts': [],
        'recommended_tools': [],
        'inspection_schedule_recommendations': [],
        'special_notes': []
    }
    
    if warranty_status == 'active':
        intelligence['warranty_recommendations'].append(
            'Asset under warranty - contact manufacturer before repair'
        )
        intelligence['warranty_recommendations'].append(
            f"Warranty expires: {asset_intel.get('warranty_expiry')}"
        )
        intelligence['special_notes'].append(
            'Use only manufacturer-approved parts to maintain warranty'
        )
    
    # Get parts recommendations based on issue
    issue_text = work_order.get('issue_description', '').lower()
    
    if 'noise' in issue_text or 'grinding' in issue_text:
        intelligence['required_parts'].extend([
            {
                'part': 'Fan motor bearing assembly',
                'part_number': 'P-50TC-BRG-001',
                'reason': 'Grinding noise typically indicates bearing failure',
                'estimated_cost': 450
            },
            {
                'part': 'Drive belt',
                'part_number': 'P-50TC-BLT-002',
                'reason': 'Replace belt during bearing replacement',
                'estimated_cost': 85
            }
        ])
        
        intelligence['recommended_tools'].extend([
            'Bearing puller',
            'Torque wrench',
            'Belt tension gauge',
            'Vibration analyzer'
        ])
    
    # Inspection schedule recommendations
    intelligence['inspection_schedule_recommendations'] = [
        {
            'interval': 'Post-repair inspection',
            'timing': 'Immediately after repair completion',
            'checks': [
                'Verify no abnormal noise',
                'Check vibration levels',
                'Measure belt tension',
                'Verify cooling capacity'
            ]
        },
        {
            'interval': 'Follow-up inspection',
            'timing': '1 week after repair',
            'checks': [
                'Re-check belt tension',
                'Monitor for any recurring noise',
                'Verify temperature differentials'
            ]
        },
        {
            'interval': 'Regular maintenance',
            'timing': 'Monthly for next 3 months, then quarterly',
            'checks': [
                'Lubrication',
                'Belt condition and tension',
                'Filter replacement',
                'General visual inspection'
            ]
        }
    ]
    
    # Special notes from history
    if asset_intel.get('known_issues'):
        intelligence['special_notes'].extend([
            f"Known issue: {issue['issue']} - occurred {issue['occurrences']} times"
            for issue in asset_intel['known_issues'][:2]
        ])
    
    intelligence['recommendations'] = [
        'Order parts immediately to avoid delays',
        'Schedule during off-hours (6 PM - 6 AM) due to noise',
        'Estimated repair time: 3-4 hours',
        'Recommend vibration monitoring installation for early detection'
    ]
    
    return intelligence
```

**Sample Output:**

```python
{
    'warranty_recommendations': [
        'Asset out of warranty - proceed with repair',
        'Warranty expired: 2023-03-15'
    ],
    
    'required_parts': [
        {
            'part': 'Fan motor bearing assembly',
            'part_number': 'P-50TC-BRG-001',
            'reason': 'Grinding noise typically indicates bearing failure',
            'estimated_cost': 450
        },
        {
            'part': 'Drive belt',
            'part_number': 'P-50TC-BLT-002',
            'reason': 'Replace belt during bearing replacement',
            'estimated_cost': 85
        }
    ],
    
    'recommended_tools': [
        'Bearing puller',
        'Torque wrench',
        'Belt tension gauge',
        'Vibration analyzer'
    ],
    
    'inspection_schedule_recommendations': [
        {
            'interval': 'Post-repair inspection',
            'timing': 'Immediately after repair completion',
            'checks': [
                'Verify no abnormal noise',
                'Check vibration levels',
                'Measure belt tension',
                'Verify cooling capacity'
            ]
        },
        {
            'interval': 'Follow-up inspection',
            'timing': '1 week after repair',
            'checks': [
                'Re-check belt tension',
                'Monitor for any recurring noise',
                'Verify temperature differentials'
            ]
        },
        {
            'interval': 'Regular maintenance',
            'timing': 'Monthly for next 3 months, then quarterly',
            'checks': [
                'Lubrication',
                'Belt condition and tension',
                'Filter replacement',
                'General visual inspection'
            ]
        }
    ],
    
    'special_notes': [
        'Known issue: Fan motor bearing failure - occurred 8 times',
        'Known issue: Belt slippage and wear - occurred 12 times'
    ],
    
    'recommendations': [
        'Order parts immediately to avoid delays',
        'Schedule during off-hours (6 PM - 6 AM) due to noise',
        'Estimated repair time: 3-4 hours',
        'Recommend vibration monitoring installation for early detection'
    ]
}
```

---

### STEP 10: Spare Parts Availability Check

**Purpose:** Check parts availability and search Outlook for orders

**Implementation:**

```python
async def step_10_check_spare_parts_availability(
    self,
    work_order: Dict[str, Any]
) -> Dict[str, Any]:
    """
    ✅ STEP 10: Spare Parts Availability
    
    Checks inventory and searches Outlook for orders if unavailable
    """
    
    required_parts = work_order.get('warranty_intelligence', {}).get('required_parts', [])
    
    if not required_parts:
        return {
            'all_available': True,
            'available_parts': [],
            'unavailable_parts': []
        }
    
    parts_status = []
    
    for part in required_parts:
        # Check inventory
        inventory = await self.check_inventory(part['part_number'])
        
        part_status = {
            'part': part['part'],
            'part_number': part['part_number'],
            'required_quantity': 1,
            'available_quantity': inventory['quantity'],
            'available': inventory['quantity'] > 0,
            'location': inventory.get('location'),
            'estimated_cost': part.get('estimated_cost', 0)
        }
        
        if not part_status['available']:
            # Check if on order
            on_order = await self.check_if_on_order(part['part_number'])
            part_status['on_order'] = on_order['on_order']
            part_status['expected_eta'] = on_order.get('eta')
        
        parts_status.append(part_status)
    
    available_parts = [p for p in parts_status if p['available']]
    unavailable_parts = [p for p in parts_status if not p['available']]
    
    result = {
        'all_available': len(unavailable_parts) == 0,
        'available_parts': available_parts,
        'unavailable_parts': unavailable_parts
    }
    
    return result

async def check_outlook_for_parts_orders(
    self,
    unavailable_parts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    ✅ Search Outlook emails for parts order status
    
    Searches for:
    - Purchase orders
    - Order confirmations
    - Shipping notifications
    - Delivery ETAs
    """
    
    outlook_findings = {}
    
    for part in unavailable_parts:
        part_number = part['part_number']
        
        # Search Outlook emails
        search_query = f"subject:(Purchase Order OR Order Confirmation OR Shipping) AND body:{part_number}"
        
        emails = await self.search_outlook_emails(search_query)
        
        if emails:
            # Parse emails for order status
            order_info = await self.parse_order_emails(emails, part_number)
            
            outlook_findings[part_number] = {
                'order_found': True,
                'status': order_info.get('status'),
                'order_number': order_info.get('order_number'),
                'supplier': order_info.get('supplier'),
                'order_date': order_info.get('order_date'),
                'estimated_eta': order_info.get('eta'),
                'tracking_number': order_info.get('tracking_number'),
                'emails_found': len(emails)
            }
        else:
            outlook_findings[part_number] = {
                'order_found': False,
                'action_required': 'Order part immediately'
            }
    
    return outlook_findings

async def search_outlook_emails(
    self,
    search_query: str
) -> List[Dict[str, Any]]:
    """
    Search Outlook using Microsoft Graph API
    """
    
    response = requests.get(
        f"{self.outlook_api_url}/v1.0/me/messages",
        params={
            '$search': search_query,
            '$top': 10,
            '$orderby': 'receivedDateTime DESC'
        },
        headers={'Authorization': f'Bearer {OUTLOOK_API_TOKEN}'}
    )
    
    return response.json().get('value', [])

async def parse_order_emails(
    self,
    emails: List[Dict[str, Any]],
    part_number: str
) -> Dict[str, Any]:
    """
    Parse order emails to extract key information using AI
    """
    
    # Combine email content
    email_texts = []
    for email in emails:
        email_texts.append(f"""
From: {email['from']['emailAddress']['name']}
Subject: {email['subject']}
Date: {email['receivedDateTime']}
Body: {email['body']['content'][:500]}
""")
    
    prompt = f"""Extract purchase order information for part {part_number} from these emails:

{chr(10).join(email_texts)}

Provide in JSON format:
{{
    "status": "ordered|shipped|delivered",
    "order_number": "PO number if found",
    "supplier": "supplier name",
    "order_date": "YYYY-MM-DD",
    "eta": "expected delivery date",
    "tracking_number": "shipping tracking number if available"
}}
"""

    message = self.claude_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    response_text = message.content[0].text
    
    if "```json" in response_text:
        json_start = response_text.find("```json") + 7
        json_end = response_text.find("```", json_start)
        response_text = response_text[json_start:json_end].strip()
    
    return json.loads(response_text)
```

**Sample Output:**

```python
{
    'all_available': False,
    'available_parts': [
        {
            'part': 'Drive belt',
            'part_number': 'P-50TC-BLT-002',
            'required_quantity': 1,
            'available_quantity': 3,
            'available': True,
            'location': 'Parts Room A, Shelf 12',
            'estimated_cost': 85
        }
    ],
    'unavailable_parts': [
        {
            'part': 'Fan motor bearing assembly',
            'part_number': 'P-50TC-BRG-001',
            'required_quantity': 1,
            'available_quantity': 0,
            'available': False,
            'location': None,
            'estimated_cost': 450,
            'on_order': True,
            'expected_eta': '2024-05-02'
        }
    ]
}

# Outlook findings for unavailable parts:
parts_order_status = {
    'P-50TC-BRG-001': {
        'order_found': True,
        'status': 'shipped',
        'order_number': 'PO-2024-1234',
        'supplier': 'ABC Industrial Supply',
        'order_date': '2024-04-23',
        'estimated_eta': '2024-05-02',
        'tracking_number': 'UPS-123456789',
        'emails_found': 3
    }
}
```

---

### STEP 11: Vendor Suggestion (Composite Scoring)

**Purpose:** Suggest top 3 vendors using composite scoring algorithm

**Implementation:**

```python
async def step_11_suggest_vendors(
    self,
    work_order: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    ✅ STEP 11: Vendor Suggestion with Composite Scoring
    
    Scoring algorithm:
    - Rating: 35%
    - Availability: 30%
    - Expertise match: 25%
    - Response time: 5%
    - Budget fit: 5%
    """
    
    # Get qualified vendors
    asset_type = work_order.get('asset_details', {}).get('asset_type')
    required_skills = work_order.get('warranty_intelligence', {}).get('recommended_tools', [])
    estimated_budget = sum([
        part.get('estimated_cost', 0) 
        for part in work_order.get('warranty_intelligence', {}).get('required_parts', [])
    ]) + 500  # Add labor estimate
    
    vendors = await self.get_qualified_vendors(asset_type)
    
    scored_vendors = []
    
    for vendor in vendors:
        # Calculate individual scores
        
        # 1. Rating Score (35%) - Convert 5-star to 100-point scale
        rating_score = (vendor['rating'] / 5.0) * 100
        
        # 2. Availability Score (30%)
        if vendor['available']:
            if vendor.get('next_available_hours', 0) <= 24:
                availability_score = 100
            elif vendor.get('next_available_hours', 0) <= 48:
                availability_score = 80
            elif vendor.get('next_available_hours', 0) <= 72:
                availability_score = 60
            else:
                availability_score = 40
        else:
            availability_score = 20
        
        # 3. Expertise Match (25%)
        expertise_score = self.calculate_expertise_match(
            vendor['expertise'],
            asset_type,
            required_skills
        )
        
        # 4. Response Time Score (5%)
        avg_response_hours = vendor.get('avg_response_hours', 48)
        response_score = max(0, 100 - (avg_response_hours * 2))
        
        # 5. Budget Fit Score (5%)
        vendor_rate = vendor.get('typical_hourly_rate', 0)
        estimated_vendor_cost = vendor_rate * 4  # Assume 4 hours
        
        if estimated_vendor_cost <= estimated_budget:
            budget_score = 100
        elif estimated_vendor_cost <= estimated_budget * 1.2:
            budget_score = 75
        elif estimated_vendor_cost <= estimated_budget * 1.5:
            budget_score = 50
        else:
            budget_score = 25
        
        # Calculate composite score
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
            'rating_count': vendor.get('rating_count', 0),
            'available': vendor['available'],
            'next_available': vendor.get('next_available_date'),
            'expertise': vendor['expertise'],
            'expertise_match': expertise_score,
            'typical_rate': vendor.get('typical_hourly_rate'),
            'avg_response_hours': avg_response_hours,
            'completed_jobs': vendor.get('completed_jobs', 0),
            'score': round(composite_score, 2),
            'score_breakdown': {
                'rating': round(rating_score * 0.35, 2),
                'availability': round(availability_score * 0.30, 2),
                'expertise': round(expertise_score * 0.25, 2),
                'response': round(response_score * 0.05, 2),
                'budget': round(budget_score * 0.05, 2)
            },
            'contact': {
                'phone': vendor.get('phone'),
                'email': vendor.get('email')
            }
        })
    
    # Sort by score
    scored_vendors.sort(key=lambda x: x['score'], reverse=True)
    
    return scored_vendors

def calculate_expertise_match(
    self,
    vendor_expertise: List[str],
    asset_type: str,
    required_skills: List[str]
) -> float:
    """
    Calculate expertise match score
    """
    
    score = 0
    
    # Check asset type match
    if asset_type.lower() in [e.lower() for e in vendor_expertise]:
        score += 60  # Base score for asset type match
    
    # Check required skills match
    if required_skills:
        matches = sum(
            1 for skill in required_skills 
            if any(skill.lower() in e.lower() for e in vendor_expertise)
        )
        skill_score = (matches / len(required_skills)) * 40
        score += skill_score
    else:
        score += 20  # Partial credit if no skills specified
    
    return min(score, 100)
```

**Sample Output:**

```python
[
    {
        'vendor_id': 'VND-001',
        'name': 'TechCool HVAC Services',
        'rating': 4.8,
        'rating_count': 156,
        'available': True,
        'next_available': '2024-04-27T14:00:00Z',
        'expertise': ['HVAC', 'Carrier Equipment', 'Commercial Systems', 'Emergency Repair'],
        'expertise_match': 100.0,
        'typical_rate': 125,
        'avg_response_hours': 4,
        'completed_jobs': 342,
        'score': 94.25,
        'score_breakdown': {
            'rating': 33.60,  # (4.8/5.0 * 100) * 0.35
            'availability': 30.00,  # 100 * 0.30
            'expertise': 25.00,  # 100 * 0.25
            'response': 4.60,   # (100 - 4*2) * 0.05
            'budget': 5.00      # 100 * 0.05
        },
        'contact': {
            'phone': '+1-555-0123',
            'email': 'dispatch@techcool.com'
        }
    },
    {
        'vendor_id': 'VND-042',
        'name': 'Climate Solutions Inc',
        'rating': 4.6,
        'rating_count': 89,
        'available': True,
        'next_available': '2024-04-28T08:00:00Z',
        'expertise': ['HVAC', 'Preventive Maintenance', 'Rooftop Units'],
        'expertise_match': 85.0,
        'typical_rate': 110,
        'avg_response_hours': 12,
        'completed_jobs': 178,
        'score': 87.85,
        'score_breakdown': {
            'rating': 32.20,
            'availability': 24.00,  # 80 * 0.30 (available in 24-48 hours)
            'expertise': 21.25,
            'response': 3.80,
            'budget': 5.00
        },
        'contact': {
            'phone': '+1-555-0456',
            'email': 'service@climatesolutions.com'
        }
    },
    {
        'vendor_id': 'VND-089',
        'name': 'ABC Mechanical',
        'rating': 4.5,
        'rating_count': 234,
        'available': True,
        'next_available': '2024-04-29T10:00:00Z',
        'expertise': ['Commercial HVAC', 'Equipment Repair'],
        'expertise_match': 70.0,
        'typical_rate': 95,
        'avg_response_hours': 24,
        'completed_jobs': 456,
        'score': 83.50,
        'score_breakdown': {
            'rating': 31.50,
            'availability': 18.00,  # 60 * 0.30 (available in 48-72 hours)
            'expertise': 17.50,
            'response': 2.60,
            'budget': 5.00
        },
        'contact': {
            'phone': '+1-555-0789',
            'email': 'repairs@abcmech.com'
        }
    }
]
```

---

*[Document continues with Steps 12-15 implementation...]*

This is getting very long! Would you like me to:
1. Continue with the remaining steps (12-15)?
2. Create a separate document for database schemas and API integration?
3. Focus on a specific step you'd like more detail on?

Let me know how you'd like to proceed!
