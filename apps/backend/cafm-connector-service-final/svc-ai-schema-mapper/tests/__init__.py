"""
Phase 8 Integration Tests for svc-ai-schema-mapper.

Test suite covers:
- REST API endpoints (8 endpoints)
- WebSocket streaming
- Full E2E pipeline (9-node LangGraph)
- All evaluation layers (EL-M.1 through EL-M.9, EL-3.0)
- All 3 HITL gates (GATE 1, GATE 2, GATE 3)

Run tests with:
    pytest tests/
    pytest tests/ -v
    pytest tests/ --cov=src
    pytest tests/ -n auto  (parallel execution)
"""
