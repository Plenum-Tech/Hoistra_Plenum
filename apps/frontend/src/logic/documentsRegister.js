// Manage documents — a building's documents in one dialog (api/documents.js).
//
// Every document on the building however it reached it (filed here, linked here, cited by a
// record on it), and what an admin does to one: set its type, mark it personal, link it to
// another building or take that link off, make its text searchable ("Index now"), and upload
// a new one with its type chosen. An identity paper (visa, Emirates ID, passport, labour card)
// is personal: filed and listed for admins, never indexed for search, numbers masked.
import { documentsApi } from '../api/documents.js';
import { isStaleScope } from '../api/client.js';

const errText = (e) => (e && e.message) || String(e);

export const documentsRegisterMethods = {
  dmOpen(buildingId, buildingName) {
    this.setState({ dmBuildingId: buildingId, dmBuildingName: buildingName || '', dmRows: null, dmErr: '', dmMsg: '',
      dmBusy: null, dmUploadFile: null, dmUploadType: 'other' });
    this.dmLoad(buildingId);
  },
  dmClose() { this.setState({ dmBuildingId: null, dmRows: null }); },

  async dmLoad(buildingId) {
    const id = buildingId || this.state.dmBuildingId;
    if (!id) return;
    try {
      const out = await documentsApi.list(id);
      if (this.state.dmBuildingId !== id) return;
      this.setState({ dmRows: (out && out.documents) || [], dmTypes: (out && out.types) || this.state.dmTypes || [],
        dmCanManage: !!(out && out.can_manage), dmHidden: (out && out.personal_hidden) || 0, dmErr: '' });
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ dmErr: 'The documents could not be read: ' + errText(e), dmRows: [] });
    }
  },

  async dmAct(docId, label, fn) {
    if (this.state.dmBusy) return;
    this.setState({ dmBusy: docId, dmMsg: '' });
    try {
      const out = await fn();
      this.setState({ dmBusy: null, dmMsg: label(out) });
      this.dmLoad();
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ dmBusy: null, dmMsg: 'Not done: ' + errText(e) });
    }
  },
  dmSetType(docId, docType) {
    return this.dmAct(docId, (o) => 'Type set to ' + String((o && o.type) || docType).replace(/_/g, ' ') +
      ((o && o.sensitivity) === 'personal' ? ' — kept personal.' : '.'), () => documentsApi.classify(docId, { doc_type: docType }));
  },
  dmSetPersonal(docId, personal) {
    return this.dmAct(docId, (o) => ((o && o.sensitivity) === 'personal' ? 'Marked personal: out of search, admins only.' : 'Marked standard.'),
      () => documentsApi.classify(docId, { personal: !!personal }));
  },
  dmLink(docId, targetBuildingId) {
    if (!targetBuildingId) return this.setState({ dmMsg: 'Choose the building to link it to.' });
    return this.dmAct(docId, (o) => (o && o.created === false ? 'Already linked there.' : 'Linked to the other building.'),
      () => documentsApi.link(docId, targetBuildingId));
  },
  dmUnlink(docId) {
    return this.dmAct(docId, (o) => ((o && o.removed) ? 'Link removed.' : 'It was not linked here.'),
      () => documentsApi.unlink(docId, this.state.dmBuildingId));
  },
  dmIndex(docId) {
    return this.dmAct(docId, (o) => (o && o.already_indexed ? 'Already searchable.' : 'Indexing started — searchable in a minute or two.'),
      () => documentsApi.indexNow(docId));
  },
  dmSetLinkTarget(docId, value) { this.setState((p) => ({ dmLinkTo: Object.assign({}, p.dmLinkTo || {}, { [docId]: value }) })); },
  dmPickFile(e) { this.setState({ dmUploadFile: (e && e.target && e.target.files && e.target.files[0]) || null, dmMsg: '' }); },
  dmSetUploadType(e) { this.setState({ dmUploadType: e.target.value }); },
  async dmUpload() {
    const s = this.state;
    if (!s.dmUploadFile) return this.setState({ dmMsg: 'Choose a file first.' });
    if (s.dmBusy) return;
    this.setState({ dmBusy: 'upload', dmMsg: '' });
    try {
      const r = await documentsApi.upload(s.dmUploadFile, s.dmBuildingId, s.dmUploadType || 'other');
      this.setState({ dmBusy: null, dmUploadFile: null,
        dmMsg: (r && r.success === false) ? 'Not filed: ' + (r.error || 'no reason given') : (r && r.answer) || 'Filed.' });
      this.dmLoad();
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ dmBusy: null, dmMsg: 'Not filed: ' + errText(e) });
    }
  },

  dmVals() { return dmVals(this); }
};

export function dmVals(c) {
  const s = c.state;
  const types = s.dmTypes || [];
  const label = (k) => (types.find((t) => t.key === k) || {}).label || (k ? String(k).replace(/_/g, ' ') : 'Unclassified');
  const others = (s.bldLive || []).filter((b) => b && b.buildingId && b.buildingId !== s.dmBuildingId)
    .map((b) => ({ id: b.buildingId, name: b.name + (b.code ? ' · ' + b.code : '') }));
  const rows = (s.dmRows || []).map((d) => ({
    id: d.document_id, title: d.title || d.file || 'Untitled', type: d.type || '', typeLabel: d.type_label || label(d.type),
    personal: !!d.personal, indexed: !!d.indexed, how: d.how, added: d.added ? String(d.added).slice(0, 10) : '',
    linkedHere: d.how === 'linked here', busy: s.dmBusy === d.document_id,
    setType: (e) => c.dmSetType(d.document_id, e.target.value),
    togglePersonal: () => c.dmSetPersonal(d.document_id, !d.personal),
    linkTo: (s.dmLinkTo || {})[d.document_id] || '',
    setLinkTo: (e) => c.dmSetLinkTarget(d.document_id, e.target.value),
    link: () => c.dmLink(d.document_id, (s.dmLinkTo || {})[d.document_id]),
    unlink: () => c.dmUnlink(d.document_id),
    index: () => c.dmIndex(d.document_id)
  }));
  return {
    dmShow: !!s.dmBuildingId,
    dmBuildingName: s.dmBuildingName || '',
    dmLoading: !!s.dmBuildingId && s.dmRows === null,
    dmErr: s.dmErr || '', dmMsg: s.dmMsg || '',
    dmCanManage: !!s.dmCanManage,
    dmHidden: s.dmHidden || 0,
    dmRows: rows,
    dmTypes: types,
    dmOthers: others,
    dmClose: () => c.dmClose(),
    dmUploadType: s.dmUploadType || 'other',
    dmUploadPersonal: !!(types.find((t) => t.key === (s.dmUploadType || 'other')) || {}).personal,
    dmUploadName: (s.dmUploadFile && s.dmUploadFile.name) || '',
    dmPickFile: (e) => c.dmPickFile(e),
    dmSetUploadType: (e) => c.dmSetUploadType(e),
    dmUpload: () => c.dmUpload(),
    dmUploading: s.dmBusy === 'upload'
  };
}
