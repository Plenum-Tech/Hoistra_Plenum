# Concept: Cross-system data normalization

CMMS/CAFM-native terms map to canonical UDR tables/columns via synonyms.json (Maximo WONUM->work_orders.workorder_ref, ASSETNUM->assets.asset_code; Fiix asset_id->asset_code; SAP PM EQUI->assets, EQUNR->asset_code; Archibus eq->assets; Planon Order->work_orders; MRI/Yardi Unit->spaces, Lease->leases). Value normalisation unifies case/spacing/format variants and promotes recurring shared values to reference-table primary keys.
