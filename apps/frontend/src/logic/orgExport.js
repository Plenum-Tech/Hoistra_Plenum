// orgExport — downloading everything this company holds, from the task bar.
//
// One control, admin and superadmin only, and only while the account is actually in Admin
// view — the same three conditions the account menu's own "Admin view" toggle sets, so the
// export appears exactly where an administrator is already acting as one.
//
// It opens a panel rather than downloading on click, because the honest answer to "is
// everything in there?" is a list, not a claim. The panel is a preview: a row count per
// table and, beside it, every table deliberately left out with the reason. Credentials are
// never exported; the platform's own reference data is not this company's to take; and a
// table that cannot be filtered by company is excluded rather than exported unfiltered,
// which would put other customers' rows in this file.
//
// The preview costs a count. The download is a second, explicit click.
import { adminApi, downloadOrgExport } from '../api/admin.js';

export const OX_DEFAULTS = { oxOpen: false, oxPlan: null, oxLoading: false, oxError: '', oxBusy: false };

const NUM = (n) => (typeof n === 'number' ? n.toLocaleString('en-GB') : '—');

// Bytes as something a person reads before deciding whether to wait for it.
const SIZE = (b) => {
  if (!b && b !== 0) return '';
  if (b < 1024) return b + ' B';
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' KB';
  if (b < 1024 * 1024 * 1024) return (b / (1024 * 1024)).toFixed(1) + ' MB';
  return (b / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
};

export const orgExportMethods = {

  async oxOpen_() {
    if (this.state.oxLoading) return;
    this.setState({ oxOpen: true, oxLoading: true, oxError: '', oxPlan: null });
    try {
      const plan = await adminApi.exportPreview();
      this.setState({ oxPlan: plan, oxLoading: false });
    } catch (e) {
      this.setState({ oxLoading: false, oxError: (e && e.message) || String(e) });
    }
  },

  oxClose() { this.setState({ oxOpen: false, oxError: '' }); },

  async oxDownload() {
    if (this.state.oxBusy) return;
    this.setState({ oxBusy: true, oxError: '' });
    try {
      const res = await downloadOrgExport();
      this.setState({ oxBusy: false, oxOpen: false });
      this.flash('Exported ' + res.tables + ' tables · ' + NUM(res.rows) + ' rows · '
        + SIZE(res.bytes) + ' — saved as ' + res.name);
    } catch (e) {
      this.setState({ oxBusy: false, oxError: (e && e.message) || String(e) });
    }
  },

  oxVals() {
    const s = this.state;
    const a = s.account || {};
    // Admin or superadmin, signed in, AND currently in Admin view. The role toggle is what
    // "in admin view" means here (auth.js: s.role flips between 'admin' and 'user'), so a
    // superadmin reading the product as a user does not carry an export button around.
    const isAdminRole = a.role === 'admin' || a.role === 'superadmin';
    const show = !!s.signedIn && isAdminRole && s.role === 'admin';

    const plan = s.oxPlan || null;
    const tables = (plan && plan.tables) || {};
    const excluded = (plan && plan.excluded) || {};
    const names = Object.keys(tables).sort((x, y) => (tables[y].rows || 0) - (tables[x].rows || 0));
    const withRows = names.filter((n) => (tables[n].rows || 0) > 0);

    return {
      oxShow: show ? 'flex' : 'none',
      oxPanelShow: s.oxOpen ? 'flex' : 'none',
      oxOpen: () => this.oxOpen_(),
      oxClose: () => this.oxClose(),
      oxLoading: !!s.oxLoading,
      oxLoadingShow: s.oxLoading ? 'block' : 'none',
      oxErrorShow: s.oxError ? 'block' : 'none',
      oxError: s.oxError || '',
      oxReadyShow: plan && !s.oxLoading ? 'block' : 'none',
      // The headline: what is actually in the file.
      oxSummary: plan
        ? NUM(plan.row_total) + ' rows across ' + withRows.length + ' tables with data'
          + ' (' + names.length + ' exported in total).'
        : '',
      oxSubtitle: 'One CSV per table, plus a manifest recording exactly this list.',
      // Biggest first — the tables someone scans for to check the export is real.
      oxTables: names.slice(0, 40).map((n) => ({
        name: n,
        rows: NUM(tables[n].rows),
        by: tables[n].scoped_by || '',
        dim: (tables[n].rows || 0) === 0 ? '0.45' : '1'
      })),
      oxMoreShow: names.length > 40 ? 'block' : 'none',
      oxMore: names.length > 40 ? (names.length - 40) + ' more tables in the file' : '',
      // Said in the panel, not buried in the manifest: a reader deciding whether this is
      // "everything" needs the exclusions in front of them, with reasons.
      oxExcluded: Object.keys(excluded).sort().map((n) => ({ name: n, why: excluded[n] })),
      oxExcludedShow: Object.keys(excluded).length ? 'block' : 'none',
      oxExcludedHead: Object.keys(excluded).length + ' tables are deliberately not in the file',
      oxNotes: (plan && plan.notes) || [],
      oxBusy: !!s.oxBusy,
      oxDownloadLabel: s.oxBusy ? 'Building the file…' : 'Download the zip',
      oxDownload: () => this.oxDownload()
    };
  }
};
