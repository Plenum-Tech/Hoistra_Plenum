// vendorsWrite — the two writes the Contract terms panel can make: correcting a term, and
// confirming the set.
//
// A contract ingest produces a DRAFT parameter set. Scoring refuses to run against a draft,
// so until someone confirms it the vendor has no score — which is the correct default,
// because an extraction is a reading of a document, not an agreement.
//
// Confirming turns those readings into the numbers every later judgement is made against:
// SLA breaches, service credits, and whether an invoice line is overcharged. That is why
// this is not a fire-and-forget button.
//
// The case it was built around. A 16-page university FM contract ingested on 17 Sep 2026
// produced 17 terms, 5 of them platform defaults — including a labour rate of £350/day. The
// contract states no day rate at all; it is priced per hour, per trade, twelve of them. The
// panel said "default", which is true and reads like a shrug. Confirming would have made
// £350/day the agreed figure, and every WKU invoice afterwards would have been checked
// against a number nobody ever wrote down. So the confirmation names the count out loud.
//
// Two rules hold here:
//
//   ONE FIELD PER WRITE. The PATCH route writes what it is given. Sending the whole row
//   would restate every value the reader never looked at, and quietly re-stamp a default as
//   though a person had checked it.
//
//   NO OPTIMISTIC UPDATE. Both writes re-read the page from the server on success and change
//   nothing locally on failure. A value on screen should be one the database actually holds;
//   showing an edit that was refused is worse than showing the old number.
import { opsApi } from '../api/opsIntelligence.js';

// contract_sla_parameters columns that hold a number. A text box that reaches one of these
// has to be a number before it is sent — Postgres would refuse it anyway, and a 422 reads to
// the person as a broken control rather than as a typo.
const NUMERIC_TERMS = new Set([
  'sla_response_p1_hours', 'sla_response_p2_hours', 'sla_response_p3_hours', 'sla_response_p4_hours',
  'sla_completion_p1_hours', 'sla_completion_p2_hours', 'sla_completion_p3_hours', 'sla_completion_p4_hours',
  'labour_day_rate', 'labour_hour_rate', 'overtime_rate', 'call_out_rate'
]);

// Terms that are objects, not values: parts pricing by part, KPI clauses, PPM obligations,
// the criticality ladder. A single-line box cannot express any of them, and flattening one
// to a string would destroy the structure the engine reads. They stay read-only until they
// have an editor that understands their shape.
const STRUCTURED_TERMS = new Set([
  'parts_pricing_json', 'kpi_clauses_json', 'ppm_obligations_json', 'task_criticality_json'
]);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// Who to record as the confirmer. `confirmed_by` is a uuid column, and this session's account
// id has not always been one — the auth tables were integer-keyed for a while. An integer
// sent to a uuid column is a 422, and a confirm that fails for that reason looks to the
// reader exactly like a button that does not work. So a non-uuid is left out: the row records
// the act without naming the person, which is worse than naming them and better than failing.
export const actorUuid = (account) => {
  const id = account && account.id;
  return typeof id === 'string' && UUID_RE.test(id.trim()) ? id.trim() : null;
};

const why = (e) => (e && e.message) || String(e || 'unknown error');

export const vendorsWriteMethods = {
  // Turn a draft parameter set into the agreed one. `contractId` comes from the panel, which
  // knows which vendor is open; passing it in keeps this method independent of the selection.
  async vpConfirmContract(contractId) {
    const id = String(contractId || '').trim();
    if (!id) return this.flash('No contract on record for this vendor — there is nothing to confirm.');
    if (this._vpWriting) return this.flash('One change at a time — the last one is still going.');
    // The server refuses a confirmation with no named person — a decision nobody is
    // attributed for is not one. Caught here so the reader gets a sentence rather than
    // "confirmed_by_required", and so a refusal is never mistaken for a broken button.
    const actor = actorUuid(this.state.account);
    if (!actor) {
      return this.flash(
        "We cannot identify you well enough to record who confirmed this. Sign in again, "
        + "then confirm — the decision has to carry a name."
      );
    }
    this._vpWriting = true;
    try {
      const r = await opsApi.confirmContract(id, { confirmed_by: actor });
      // A 200 is not a success on this router: the engine reports its own refusals in the
      // body, and `ok: false` with a 200 is how "not_found" and "already confirmed" arrive.
      // The engine names its refusals in codes. A code is not an explanation, and the two
      // it can return are both things the reader can act on.
      if (r && r.ok === false) {
        if (r.error === 'no_contract_terms') {
          throw new Error(
            'nothing in this contract was read from the document — all '
            + (r.fields ? r.fields + ' ' : '') + 'values are platform defaults. '
            + 'Confirming would make them binding on the vendor. Check the file, or re-ingest it.'
          );
        }
        if (r.error === 'confirmed_by_required') {
          throw new Error('a confirmation has to name who made it, and no user was attached.');
        }
        throw new Error(r.error || 'not confirmed');
      }
      this.flash('Contract terms confirmed — scoring will use them from now on.');
      await this.vpLoad({ force: true });
    } catch (e) {
      this.flash('Could not confirm the contract: ' + why(e));
    } finally {
      this._vpWriting = false;
    }
  },

  // Correct one extracted term. Deliberately narrow: one field, one write, one re-read.
  async vpEditTerm(contractId, field, value) {
    const id = String(contractId || '').trim();
    const key = String(field || '').trim();
    if (!id) return this.flash('No contract on record for this vendor — there is nothing to edit.');
    if (!key) return this.flash('No term was named, so nothing was changed.');
    if (STRUCTURED_TERMS.has(key)) {
      return this.flash('This term is a structure, not a single value — edit it on the contract document instead.');
    }
    if (this._vpWriting) return this.flash('One change at a time — the last one is still going.');

    let out = typeof value === 'string' ? value.trim() : value;
    if (NUMERIC_TERMS.has(key)) {
      // Blank is a deliberate clear; anything else has to parse, or the column refuses it.
      if (out === '') {
        out = null;
      } else {
        const n = Number(out);
        if (!Number.isFinite(n)) return this.flash('"' + value + '" is not a number, so the term was left as it was.');
        out = n;
      }
    } else if (out === '') {
      out = null;
    }

    this._vpWriting = true;
    try {
      const r = await opsApi.updateContract(id, { [key]: out }, actorUuid(this.state.account));
      if (r && r.ok === false) throw new Error(r.error || 'not updated');
      this.flash('Term updated.');
      await this.vpLoad({ force: true });
    } catch (e) {
      this.flash('Could not update the term: ' + why(e));
    } finally {
      this._vpWriting = false;
    }
  }
};
