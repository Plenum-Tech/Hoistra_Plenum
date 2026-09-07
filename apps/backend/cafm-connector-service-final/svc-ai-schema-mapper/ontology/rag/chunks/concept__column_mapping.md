# Concept: Column mapping (source column -> canonical UDR column)

After table prefixing (every source column carries its destination-table metadata). Identical 3-step process as table mapping (deterministic >=95%, RAG >=95%, semantic NLP). Column metadata = {destination table, PK/FK/Shared classification, cell value format, 5 sample values}. >=70% Suggested, <70% Requires Review, no match -> auto-create column (flagged Auto-created). 100% of source columns must have a destination assignment before the script finalises.
