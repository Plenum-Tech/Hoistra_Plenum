// documentsCrud — removing a document from a building, and everything read out of it.
//
// The drawer lists what is filed against a building. Deleting one of those rows is not a
// row delete: a document became a certificate on Compliance, contract terms and invoice
// lines on Vendors, and the chunks the assistant answers from. None of that is bound to the
// document by a foreign key — no constraint in the schema points at plenum_cafm.documents —
// so removing only what is on screen leaves every one of those pointing at an id that no
// longer resolves, on screens that have no way to know.
//
// So the delete cascades, and the dialog's job is to say so in advance. It opens on a
// DELETE with no `confirm`, which changes nothing and returns the counts; those counts are
// the dialog. "3 certificates, 16 contract terms and 42 chunks will be permanently deleted"
// is a decision a person can take. "Are you sure?" is not — and this one cannot be undone.
//
// The original file stays in blob storage. Every trace goes from the platform and no screen
// can reach it, but the PDF survives, so a delete made in error can be re-ingested. The
// rows cannot.
import { energyApi } from '../api/energy.js';
import { HOIST_ROLES } from './buildingsCrud.js';

// The role that actually governs, which is the acting account's where one is being viewed —
// the same rule the hoist and remove controls on this page already follow.
const realRole = (s) => (s.account && s.account.role) || s.role;

export const DC_DEFAULTS = { dcDel: null, dcDelError: '' };

// What each table is called in a sentence, singular and plural. The backend sends a label
// per table too; this is the fallback for one added there and not here, so a new table
// reads as "3 ppm visits" rather than as a table name nobody outside the schema knows.
const NAMES = {
  compliance_certificates: ['certificate', 'certificates'],
  contract_sla_parameters: ['contract term', 'contract terms'],
  contract_documents: ['contract link', 'contract links'],
  invoice_verifications: ['invoice verification', 'invoice verifications'],
  document_chunks: ['extracted chunk', 'extracted chunks'],
  compliance_vector_membership_audit: ['vector membership row', 'vector membership rows'],
  ppm_visits: ['PPM visit', 'PPM visits'],
  documents: ['document record', 'document records'],
  ingestion_documents: ['ingested file', 'ingested files'],
  corrections_log: ['correction', 'corrections'],
  ingestion_audit_log: ['ingestion audit row', 'ingestion audit rows'],
  review_queue: ['review queue entry', 'review queue entries'],
  claude_api_usage: ['API usage row', 'API usage rows'],
  inspections: ['inspection', 'inspections']
};

const phrase = (table, n) => {
  const pair = NAMES[table];
  const word = pair ? (n === 1 ? pair[0] : pair[1]) : table.replace(/_/g, ' ');
  return n + ' ' + word;
};

// "3 certificates, 16 contract terms and 42 extracted chunks". An Oxford-less list, because
// it is read aloud in the head before a destructive click and commas everywhere read as one
// long number. The document's own two rows are dropped: "the document" is the subject of the
// sentence the dialog already asked, and counting it again reads as a second document.
const listOf = (counts) => {
  const parts = Object.entries(counts || {})
    .filter(([t, n]) => n > 0 && t !== 'documents' && t !== 'ingestion_documents')
    .map(([t, n]) => phrase(t, n));
  if (!parts.length) return '';
  if (parts.length === 1) return parts[0];
  return parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1];
};

export const documentsCrudMethods = {

  // Step one is a DELETE with no `confirm`. It changes nothing and reports what the
  // document became, which is the only thing that makes this decision answerable.
  async dcAskDelete(doc, building) {
    const id = (doc && (doc.documentId || doc.id)) || '';
    if (!id) return;
    const name = (doc && doc.file) || id;
    const buildingId = (building && (building.buildingId || building.id)) || null;
    this.setState({
      dcDel: { id: id, name: name, buildingId: buildingId, loading: true },
      dcDelError: ''
    });
    try {
      const plan = await energyApi.deleteDocument(id, { confirm: false });
      this.setState({
        dcDel: {
          id: id,
          name: plan.file_name || name,
          docType: plan.doc_type || '',
          buildingId: buildingId || plan.building_id || null,
          removes: plan.removes || {},
          total: plan.removes_total || 0,
          cascades: plan.database_cascades || { deleted: {}, unlinked: {} },
          loading: false
        }
      });
    } catch (e) {
      this.setState({ dcDel: null, dcDelError: (e && e.message) || String(e) });
      this.flash('Could not check what that document became — ' + ((e && e.message) || e));
    }
  },

  dcCancelDelete() { this.setState({ dcDel: null, dcDelError: '' }); },

  async dcConfirmDelete() {
    const d = this.state.dcDel;
    if (!d || d.loading || d.working) return;
    this.setState({ dcDel: Object.assign({}, d, { working: true }) });
    try {
      const res = await energyApi.deleteDocument(d.id, { confirm: true });
      this.setState({ dcDel: null });
      this.flash(res.message || ('Deleted ' + d.name));
      (res.warnings || []).forEach((w) => this.flash(w));
      // The drawer caches each building's rows and bgLoad returns early on a hit, so the
      // deleted row would stay on screen until the page was reloaded. Drop this building's
      // entry, then re-read it and the rollup the chips above it are counted from.
      if (d.buildingId) {
        this.setState((p) => {
          const next = Object.assign({}, p.bgTree);
          delete next[d.buildingId];
          return { bgTree: next };
        });
        await this.bgLoad(d.buildingId);
      }
      await this.bldLoad();
    } catch (e) {
      this.setState({ dcDel: Object.assign({}, d, { working: false }) });
      this.flash('Delete failed — ' + ((e && e.message) || e));
    }
  },

  dcVals() {
    const s = this.state;
    const d = s.dcDel;
    const removed = listOf(d && d.removes);
    const cascades = (d && d.cascades) || {};
    const alsoGone = listOf(cascades.deleted);
    const unlinked = listOf(cascades.unlinked);

    return {
      // Same gate as removing a building. A user allocated to a building may ingest into
      // it; destroying a document and every record read out of it is not the same right.
      dcCanRemove: !!s.signedIn && HOIST_ROLES.has(realRole(s)),
      dcDelShow: d ? 'flex' : 'none',
      dcDelName: (d && d.name) || '',
      dcDelType: d && d.docType ? 'filed as ' + d.docType : '',
      dcDelLoading: !!(d && d.loading),
      dcDelWorking: !!(d && d.working),
      dcDelBusyLabel: d && d.working ? 'Deleting…' : 'Delete everywhere',
      // The sentence the decision is actually made on. It names the screens, because
      // "3 certificates" means nothing to someone who has not seen where they show up.
      dcDelBodyShow: d && !d.loading ? 'block' : 'none',
      dcDelBody: removed
        ? 'This will permanently delete ' + removed
          + ' — they disappear from Compliance and Vendors with it.'
        : 'Nothing was read out of this document, so only the document itself goes.',
      dcDelCascadeShow: alsoGone || unlinked ? 'block' : 'none',
      dcDelCascade: [
        alsoGone ? 'The database removes ' + alsoGone + ' along with the file record.' : '',
        unlinked ? (alsoGone ? ' ' : '') + (unlinked.charAt(0).toUpperCase() + unlinked.slice(1))
          + ' stay, no longer linked to it.' : ''
      ].join(''),
      // Said plainly rather than left to be discovered: this is the one thing that survives
      // and the only reason a mistake here is recoverable at all.
      dcDelKept: 'The original file stays in storage, so it can be ingested again. '
        + 'The records above cannot be brought back.',
      dcDelError: s.dcDelError || '',
      dcDelErrorShow: s.dcDelError ? 'block' : 'none',
      dcDelCancel: () => this.dcCancelDelete(),
      dcDelConfirm: () => this.dcConfirmDelete()
    };
  }
};
