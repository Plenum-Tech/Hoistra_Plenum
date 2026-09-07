# Canonical entity: Compliance Certificate (table `compliance_certificates`)

- Hierarchy level: compliance
- Primary key: `id`
- Description: Statutory / compliance certificate linked to an asset and a duty holder. Drives expiry tracking and statutory alerts.
- Table aliases / synonyms: certificate, cert, compliance doc, statutory certificate, inspection certificate

## Columns
- `id` · type=uuid · class=primary_key
- `certificate_ref` · type=string · class=standard · samples=CERT-LOLER-4471
- `cert_type` · type=string · class=standard · samples=gas_safety, EICR, LOLER, legionella_L8, fire · aliases=certificate type, compliance type
- `asset_id` · type=uuid · class=foreign_key · -> assets
- `duty_holder_id` · type=uuid · class=foreign_key · -> duty_holders · aliases=responsible person
- `expiry_date` · type=date · class=standard · samples=2026-12-31 · aliases=expiry, valid until, expiration date

## Relationships
- assets.id —certified_by(1:N)→ compliance_certificates.asset_id
- duty_holders.id —responsible_for(1:N)→ compliance_certificates.duty_holder_id
