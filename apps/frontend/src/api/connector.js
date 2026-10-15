// api/connector — cafm-connector-service's plenum_cafm CRUD routes, behind the gateway at
// /backend/connector/ (deploy/allinone/nginx.conf). Every route checks the caller's token with
// the identity service and stamps or checks the caller's company on what it writes
// (api/security.py guard); a superadmin is not narrowed, so the company is always sent.
import { BASES, apiFetch } from './client.js';

const B = BASES.connector;

export const connectorApi = {
  // The company's vendor list, a page at a time: {total, limit, offset, data: [VendorResponse]}.
  listVendors: (query) => apiFetch(B, '/api/v1/plenum/vendors', { query: query }),
  // A vendor in the vendor master: {organization_id, vendor_name, address?} → VendorResponse.
  createVendor: (body) => apiFetch(B, '/api/v1/plenum/vendors', { method: 'POST', body: body }),
  // A contact on a vendor: {vendor_id, name, email?, phone?, designation?}.
  createVendorContact: (body) => apiFetch(B, '/api/v1/plenum/vendor-contacts', { method: 'POST', body: body })
};
