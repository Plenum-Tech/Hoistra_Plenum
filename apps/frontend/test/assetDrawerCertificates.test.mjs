// The asset drawer's two compliance lines: the asset's own certificates and its vendor's
// accreditation. Until 28 Sep 2026 nothing on the Assets page tied Lift Asset-4471 to its LOLER
// examination, or to the blocked contractor who also services it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { assetCertificatesLine, vendorAccreditationLine } from '../src/logic/assetsCondition.js';

const NOW = new Date(2026, 8, 28, 12, 0, 0);

test("an asset's own certificates: type, lapsed or expires, date, number", () => {
  const line = assetCertificatesLine([
    { cert_scope: 'Asset', certificate_type_name: 'LOLER Thorough Examination Report', expiry_date: '2026-10-17',
      days_to_expiry: 19, certificate_number: 'LOLER-B-301-4471' }
  ], NOW);
  assert.equal(line, 'LOLER Thorough Examination Report · expires 17 Oct 2026 · LOLER-B-301-4471');
  assert.equal(assetCertificatesLine([], NOW), 'None on record for this asset');
});

test("a vendor's accreditation leads with the worst state and names what lapsed", () => {
  const safelift = [
    { cert_scope: 'Vendor', certificate_type_name: "Contractors' Public Liability Insurance", expiry_date: '2026-08-13',
      days_to_expiry: -46, vendor_block_state: 'Blocked' },
    { cert_scope: 'Vendor', certificate_type_name: 'LEIA Membership', expiry_date: '2027-03-10', days_to_expiry: 163 }
  ];
  assert.equal(vendorAccreditationLine(safelift, NOW),
    "Blocked · 2 certificates on file · Contractors' Public Liability Insurance lapsed 13 Aug 2026");
  const northgate = [
    { cert_scope: 'Vendor', certificate_type_name: 'NICEIC Approved Contractor', expiry_date: '2026-10-20', days_to_expiry: 22 },
    { cert_scope: 'Vendor', certificate_type_name: 'ISO 9001', expiry_date: '2027-02-28', days_to_expiry: 153 }
  ];
  assert.equal(vendorAccreditationLine(northgate, NOW),
    'Expiring · 2 certificates on file · NICEIC Approved Contractor expires 20 Oct 2026');
  assert.equal(vendorAccreditationLine([], NOW), 'No vendor certificate on record');
});
