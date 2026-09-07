# Concept: Entity resolution & vector-chunk anchoring (Test 1)

Every unstructured chunk (<=512 tokens) must be anchored to a structured entity via that entity's PRIMARY KEY only — never a foreign key or shared attribute (0% tolerance). If only a shared attribute is available (e.g. asset_make 'Siemens 012'), auto-create a reference table (manufacturers) with that value as PK, then re-tag. Test 1 verifies every chunk's source_entity_id exists as a PK in some table; >1% fail blocks the UDR. Normalisation: 'Siemens 012' / 'siemens 012' / 'Siemens012' resolve to one manufacturer PK.
