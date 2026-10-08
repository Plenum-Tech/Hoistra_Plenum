// migrationGates — the migration gates as CAFM Web draws and answers them.
//
// Hoistra and CAFM Web answer the same gates on the same service (svc-ai-schema-mapper), so
// the screens show the same fields and send the same answers. Each gate here is two pure
// functions over the gate's payload and the reader's decisions (`dec`, {key: value}):
//   …View(payload, dec)  → the rows and labels for a gate screen
//   …Body(payload, dec)  → the POST the gate is answered with
// The CAFM files each one follows are named at the top of its section. Where CAFM does
// something the service does not honour, or that would write without a person asking, the
// section says what is different here and why.
//
// Everything reads `pending_gate_payload` only — the status poll sends it whole — never the
// UDR reports or node outputs, which the poll leaves out above 16 KB.

const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);
const arr = (v) => (Array.isArray(v) ? v : []);
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);
const str = (v) => (typeof v === 'string' ? v : null);
const two = (x) => (num(x) === null ? '—' : x.toFixed(2));
const count = (n) => Number(n).toLocaleString('en-GB');
const plural = (n, one, many) => (n === 1 ? one : (many || one + 's'));

// ── unique tables ───────────────────────────────────────────────────────────────────
//
// gates/gate-unique-table-approval.tsx + TableResolutionPanels phase="unique_only" in
// migration-metadata-view.tsx. Read-only: the key was decided at the gate before, and the
// service reads nothing from this answer (unique_table_review_node discards the resume value).
//
// Hoistra used to show final_decisions here as the routing each sheet "will land in". Nothing
// has been routed yet — that is the next gate — so those rows were guesses, or a sheet matched
// to its own name at a fake 100%. CAFM shows no routing at this gate, and neither does this.

export function uniqueTablesView(payload) {
  const tr = isObj(payload && payload.table_resolution) ? payload.table_resolution : {};
  const cards = arr(tr.metadata_cards).filter(isObj);
  const pkDet = arr(tr.pk_detection).filter(isObj);
  const dup = isObj(tr.duplicate_tables) ? tr.duplicate_tables : {};
  const groups = arr(dup.groups).filter((g) => isObj(g) && Array.isArray(g.tables));
  const groupOf = {};
  groups.forEach((g) => g.tables.forEach((t) => { groupOf[t] = g; }));
  const merged = arr(tr.merged_columns).filter(isObj);
  const counts = isObj(tr.counts) ? tr.counts : {};

  // B8.1, read-only — the key confirmed at the gate before, one row per duplicate group.
  const seenPk = new Set();
  const pkRowsOut = [];
  pkDet.forEach((p) => {
    const g = groupOf[p.table];
    if (g) { const k = g.tables.join('|'); if (seenPk.has(k)) return; seenPk.add(k); }
    pkRowsOut.push({
      label: g ? (g.label || p.table) : p.table, kind: str(p.kind) || '',
      pk: arr(p.primary_key).join(', '), uniqueness: two(p.uniqueness), nullRate: two(p.null_rate),
      tieBreak: str(p.tie_break) || ''
    });
  });

  // B7.1 — the first card of a duplicate group stands for the whole group.
  const seen7 = new Set();
  const uniq = [];
  cards.forEach((c) => {
    const g = c.table ? groupOf[c.table] : null;
    if (g) { const k = g.tables.join('|'); if (seen7.has(k)) return; seen7.add(k); }
    uniq.push({
      label: g ? (g.label || c.table) : c.table,
      group: g ? { count: g.count || g.tables.length, members: g.tables.slice() } : null,
      pk: arr(c.primary_key).map(String),
      cols: num(c.column_count) === null ? '—' : c.column_count,
      samples: arr(c.samples).filter(isObj).map((x) => ({ column: String(x.column || ''), values: arr(x.values).slice(0, 3).map(String).join(', ') }))
    });
  });

  const pw = isObj(tr.pairwise) ? tr.pairwise : {};
  const hp = isObj(pw.highest_pair) ? pw.highest_pair : null;
  const verdict = str(pw.verdict) || '';
  // The service's verdict already opens with the highest pair; CAFM printed it twice.
  const pairNote = !verdict ? null : {
    pair: hp && !/^highest pair/i.test(verdict) ? { a: hp.table_a, b: hp.table_b, meta: two(hp.metadata_similarity), name: two(hp.name_similarity) } : null,
    verdict: verdict
  };

  const tablesN = num(counts.tables) === null ? cards.length : counts.tables;
  return {
    show: cards.length > 0 || arr(tr.final_decisions).length > 0,
    pkRows: pkRowsOut,
    dupMeta: groups.length ? groups.length + ' duplicate ' + plural(groups.length, 'group') + ' · by shared columns' : 'no duplicates · by shared columns',
    dupGroups: groups.map((g) => {
      const shared = arr(g.shared_columns).map(String);
      return {
        key: g.tables.join('|'), count: g.count || g.tables.length, tables: g.tables.slice(),
        overlap: Math.round((num(g.similarity) || 0) * 100) + '% column overlap',
        shared: shared.slice(0, 12), more: shared.length > 12 ? shared.length - 12 : 0
      };
    }),
    dupEmpty: 'No duplicate tables — all ' + (num(dup.checked) === null ? cards.length : dup.checked) + ' tables have distinct columns.',
    hasCards: cards.length > 0,
    uniqueMeta: uniq.length + ' unique · ' + tablesN + ' source ' + plural(tablesN, 'table') + (groups.length ? ' · ' + groups.length + ' duplicate ' + plural(groups.length, 'group') : ''),
    unique: uniq,
    mergeNote: groups.length
      ? groups.map((g) => (g.label || g.tables[0]) + ' (×' + (g.count || g.tables.length) + ')').join(', ') + ' merged from similar columns — shown as ' + uniq.length + ' unique ' + plural(uniq.length, 'table') + ' instead of ' + cards.length + '.'
      : '',
    pairNote: pairNote,
    merges: merged.map((m) => {
      const dropped = arr(m.dropped).map(String);
      const rows = num(m.row_count) || 0;
      return {
        table: String(m.table || ''), members: arr(m.members).map(String).join(' = '), kept: String(m.kept || ''),
        match: (num(m.match_pct) === null ? 100 : m.match_pct) + '% identical',
        detail: 'Values matched row-for-row across ' + count(rows) + ' ' + plural(rows, 'row') + ' — kept ',
        dropped: dropped.join(', ')
      };
    }),
    mergeMeta: merged.length + ' ' + plural(merged.length, 'merge') + ' · ' + merged.reduce((a, m) => a + arr(m.dropped).length, 0) + ' column(s) removed'
  };
}

// ── classification (B20.1) ──────────────────────────────────────────────────────────
//
// gates/gate-classification-approval.tsx. Keys: dec[group_id] = 'fk' | 'shared' | 'exclude'.
// Foreign Key is offered only where the group has a primary-key member to enforce against:
// the service drops a Shared group's lookup table on an FK override and adds an FK only when
// the group has_pk (column_intelligence.apply_classification_decisions), so promoting a group
// without one left its columns neither normalised nor enforced.

const isFkVerdict = (v) => /foreign/i.test(v || '');
const isSharedVerdict = (v) => /shared/i.test(v || '');
const pkOfGroup = (c) => c.canonical_has_pk || c.has_pk || null;
const canBeFk = (c) => isFkVerdict(c.verdict) || !!c.has_pk || !!c.canonical_has_pk;

function classificationChoices(payload, dec) {
  const cls = arr(payload && payload.classification).filter(isObj);
  const reviewable = cls.filter((c) => isFkVerdict(c.verdict)).concat(cls.filter((c) => isSharedVerdict(c.verdict) && !isFkVerdict(c.verdict)));
  return reviewable.map((c) => {
    const detected = isFkVerdict(c.verdict) ? 'fk' : 'shared';
    let chosen = (dec || {})[c.group_id] || detected;
    if (chosen === 'fk' && !canBeFk(c)) chosen = detected;
    return { c: c, detected: detected, chosen: chosen };
  });
}

export function classificationBody(payload, dec) {
  const rejected = [];
  const overrides = {};
  classificationChoices(payload, dec).forEach(({ c, detected, chosen }) => {
    if (chosen === 'exclude') rejected.push(c.group_id);
    else if (chosen !== detected) overrides[c.group_id] = chosen;
  });
  return { rejected_groups: rejected, verdict_overrides: overrides };
}

export function classificationView(payload, dec) {
  const cls = arr(payload && payload.classification).filter(isObj);
  const lookupOf = {};
  arr(payload && payload.shared_attribute_tables).filter(isObj).forEach((t) => { if (t.from_group) lookupOf[t.from_group] = t; });
  // Every distinct key a group is anchored on, the ones an FK points at included.
  const pks = [];
  cls.forEach((c) => {
    const pk = c.canonical_has_pk || c.has_pk || (/primary/i.test(c.verdict || '') ? (c.canonical_name || null) : null);
    if (pk && pks.indexOf(pk) < 0) pks.push(pk);
  });
  const rows = classificationChoices(payload, dec).map(({ c, detected, chosen }) => {
    const pk = pkOfGroup(c);
    const members = arr(c.members).map(String);
    const fks = members.filter((m) => m !== pk && m !== c.has_pk);
    const lk = lookupOf[c.group_id];
    const changed = chosen !== 'exclude' && chosen !== detected;
    return {
      id: c.group_id, kind: detected, chosen: chosen, changed: changed, excluded: chosen === 'exclude',
      ri: num(c.ri) === null ? '' : 'RI ' + Math.round(c.ri * 100) + '%',
      fkSide: (fks.length ? fks : [c.canonical_name || '']).join(' · '), pkSide: pk || '',
      canonical: c.canonical_name || '',
      lookup: lk && chosen === 'shared' ? { table: lk.table_name, pk: lk.pk_column, values: num(lk.distinct_count) === null ? arr(lk.sample_values).length : lk.distinct_count } : null,
      members: members.join(' · '),
      note: chosen === 'exclude' ? 'Excluded — nothing from this group will be applied.'
        : changed ? (chosen === 'shared'
          ? 'Re-classified: FK will NOT be enforced — a lookup table is created instead.'
          : 'Re-classified: treated as a Foreign Key — its lookup table will NOT be created.')
        : '',
      fkAllowed: canBeFk(c)
    };
  });
  const fk = rows.filter((r) => r.kind === 'fk');
  const shared = rows.filter((r) => r.kind === 'shared');
  const excluded = rows.filter((r) => r.excluded).length;
  const changedN = rows.filter((r) => r.changed).length;
  return {
    pks: pks, fk: fk, shared: shared,
    counts: pks.length + ' PK · ' + fk.length + ' FK · ' + shared.length + ' Shared',
    submitLabel: excluded || changedN
      ? 'Apply ' + (excluded + changedN) + ' change' + (excluded + changedN === 1 ? '' : 's') + ' & continue'
      : 'Confirm links'
  };
}

// ── column mapping (B14.1) ──────────────────────────────────────────────────────────
//
// gates/gate-column-mapping-approval.tsx. Keys: dec['cm:' + source] = '__keep__' | '__new__' |
// a destination column. Two things Hoistra used to get wrong, both as CAFM sends them now:
//  - an override goes under EVERY sheet routed to the row (source_tables), not the first only —
//    write_node looks a column's destination up by its own sheet (table_routing[sheet]);
//  - "New column" on a row with no match is sent as __new__, which makes the service add the
//    column (approved_new_columns) instead of leaving it to the next pass.

const cmKey = (r) => 'cm:' + String(r.source || '');
const cmColumn = (r) => r.source_column || String(r.source || '').split('.').slice(1).join('.');
const cmTables = (r) => {
  const t = arr(r.source_tables).map(String).filter(Boolean);
  return t.length ? t : [String(r.source || '').split('.')[0]];
};

function cmDecision(r, dec) {
  const v = (dec || {})[cmKey(r)];
  if (v === '__new__') return { kind: 'new' };
  if (v && v !== '__keep__') return { kind: 'retarget', target: v };
  return { kind: 'keep' };
}

export function columnMappingBody(payload, dec) {
  const overrides = {};
  arr(payload && payload.dest_mapping).filter(isObj).forEach((r) => {
    const d = cmDecision(r, dec);
    const value = d.kind === 'new' ? '__new__' : d.kind === 'retarget' && d.target !== r.matched_column ? d.target : null;
    if (!value) return;
    cmTables(r).forEach((t) => {
      overrides[t] = overrides[t] || {};
      overrides[t][cmColumn(r)] = value;
    });
  });
  return { overrides: overrides };
}

export function columnMappingView(payload, dec) {
  const destCols = isObj(payload && payload.dest_columns_by_table) ? payload.dest_columns_by_table : {};
  const rows = arr(payload && payload.dest_mapping).filter(isObj);
  const order = [];
  const by = {};
  rows.forEach((r) => {
    const t = String(r.source || '').split('.')[0];
    if (!by[t]) { by[t] = []; order.push(t); }
    by[t].push(r);
  });
  let changes = 0;
  const tables = order.map((t) => {
    const list = by[t];
    const src = [];
    list.forEach((r) => arr(r.source_tables).forEach((x) => { if (src.indexOf(x) < 0) src.push(x); }));
    const dest = (list.find((r) => r.dest_table) || {}).dest_table || '';
    return {
      key: t, label: t, sources: src, dest: dest, colsLabel: list.length + ' ' + plural(list.length, 'column'),
      rows: list.map((r) => {
        const d = cmDecision(r, dec);
        const dt = String(r.dest_table || '');
        const options = arr(destCols[dt.toLowerCase()] || destCols[dt]).map(String);
        if (d.kind === 'new' || (d.kind === 'retarget' && d.target !== r.matched_column)) changes += 1;
        const target = d.kind === 'new' ? null : d.kind === 'retarget' ? d.target : (r.matched_column || null);
        return {
          key: cmKey(r), column: String(r.source || '').split('.').slice(1).join('.') || cmColumn(r),
          target: target, destTable: dt,
          outcome: r.outcome ? r.outcome + (!r.matched_column ? ' · no existing match' : '') : '',
          pk: !!r.is_primary_key, cls: str(r.classification) || '', format: str(r.format) || '',
          samples: arr(r.samples).slice(0, 5).map(String).join(', '),
          kind: d.kind, retarget: d.kind === 'retarget' ? d.target : '', options: options
        };
      })
    };
  });
  return {
    tables: tables,
    total: rows.length + ' ' + plural(rows.length, 'column'),
    submitLabel: changes ? 'Apply ' + changes + ' change' + (changes === 1 ? '' : 's') + ' & continue' : 'Confirm columns'
  };
}

// ── hierarchy (Gate 2) ──────────────────────────────────────────────────────────────
//
// gates/gate-hierarchy.tsx. Keys: dec[relKey(r)] = 'confirm' | 'modify' | 'reject', and for a
// modified row dec[k + '#table'] / dec[k + '#column'] (blank keeps the detected target).
//
// One thing the service does that neither app used to say: if the confirmed relationships
// still contain a cycle — a table pointing at itself counts — verify_hierarchy_node's EL-M.7
// check returns WITHOUT applying the answer, and the run carries on with the relationships as
// detected (migration_graph adds no edge back to the gate). Every rejection and change on the
// gate is dropped without a word. The screen names the loop before it is sent.

export const relKey = (r) => [r.source_table, r.source_column, r.target_table, r.target_column].join('>');

export function hierarchyRows(payload) {
  const p = payload || {};
  const has = (x) => typeof x === 'string' && x.length > 0;
  const shape = (it, type, readOnly, suggested) => ({
    source_table: it.source_table, source_column: it.source_column,
    target_table: str(it.target_table), target_column: str(it.target_column),
    relationship_type: str(it.relationship_type), confidence: num(it.confidence),
    data_match_rate: num(it.data_match_rate), reasoning: str(it.reasoning),
    type: type, reference: readOnly, suggested: suggested
  });
  if (Array.isArray(p.review_items)) {
    return p.review_items.filter((it) => isObj(it) && has(it.source_table) && has(it.source_column)).map((it) => {
      const type = str(it.type) || 'fk';
      const ro = it.read_only === true || type === 'system_default' || it.mapping_note === true || it.system_default === true;
      return shape(it, type, ro, 'confirm');
    });
  }
  return arr(p.hierarchies_to_review).filter((it) => isObj(it) && has(it.source_table) && has(it.source_column)).map((it) => {
    const ro = !!(it.read_only || it.system_default || it.mapping_note);
    return shape(it, ro ? 'system_default' : 'hierarchy', ro, ro ? 'confirm' : ((num(it.confidence) === null ? 1 : it.confidence) >= 0.7 ? 'confirm' : 'reject'));
  });
}

const hierAction = (r, dec) => {
  const v = (dec || {})[relKey(r)];
  return v === 'confirm' || v === 'modify' || v === 'reject' ? v : r.suggested;
};

// What is sent for one confirmed row: the relationship's own fields, with a modified target.
function hierSent(r, dec) {
  const k = relKey(r);
  const base = {
    source_table: r.source_table, source_column: r.source_column,
    target_table: r.target_table, target_column: r.target_column,
    relationship_type: r.relationship_type, confidence: r.confidence,
    data_match_rate: r.data_match_rate, reasoning: r.reasoning
  };
  Object.keys(base).forEach((x) => { if (base[x] === null || base[x] === undefined) delete base[x]; });
  if (hierAction(r, dec) === 'modify') {
    const tt = String((dec || {})[k + '#table'] || '').trim();
    const tc = String((dec || {})[k + '#column'] || '').trim();
    if (tt) base.target_table = tt;
    if (tc) base.target_column = tc;
  }
  base.customer_confirmed = true;
  return base;
}

export function hierarchyBody(payload, dec) {
  const rows = hierarchyRows(payload).filter((r) => !r.reference);
  const confirmed = [];
  const corrections = {};
  rows.forEach((r) => {
    const a = hierAction(r, dec);
    if (a === 'reject') return;
    const sent = hierSent(r, dec);
    confirmed.push(sent);
    if (a === 'modify') corrections[r.source_table + '.' + r.source_column] = sent;
  });
  const body = { confirmed_hierarchies: confirmed, hierarchy_corrections: corrections };
  if ((payload || {}).single_table_import || (payload || {}).system_default_hierarchy) body.plenum_default_hierarchy_accepted = true;
  return body;
}

// The loops in a set of relationships, as verify_hierarchy_node._detect_cycles_in_hierarchies
// finds them: source_table → target_table edges, depth first. A self-reference is a loop.
export function hierarchyCycles(rels) {
  const graph = {};
  arr(rels).forEach((r) => {
    if (r && r.source_table && r.target_table) (graph[r.source_table] = graph[r.source_table] || []).push(r.target_table);
  });
  const cycles = [];
  const visited = new Set();
  const stack = [];
  const onStack = new Set();
  const dfs = (n) => {
    visited.add(n); onStack.add(n); stack.push(n);
    (graph[n] || []).forEach((m) => {
      if (onStack.has(m)) cycles.push(stack.slice(stack.indexOf(m)).concat([m]));
      else if (!visited.has(m)) dfs(m);
    });
    stack.pop(); onStack.delete(n);
  };
  Object.keys(graph).forEach((n) => { if (!visited.has(n)) dfs(n); });
  return cycles;
}

// Parent / children per table, over every relationship the gate shows.
function hierarchyEntities(rows) {
  const parent = {};
  const children = {};
  rows.forEach((r) => {
    if (!r.source_table || !r.target_table) return;
    parent[r.source_table] = r.target_table;
    (children[r.target_table] = children[r.target_table] || new Set()).add(r.source_table);
  });
  const names = Array.from(new Set(Object.keys(parent).concat(Object.keys(children)))).sort();
  return names.map((e) => ({ entity: e, parent: parent[e] || null, children: Array.from(children[e] || []).sort() }));
}

function hierarchyJson(payload, rows) {
  const p = payload || {};
  return {
    proposed_structure: p.proposed_structure || null,
    hierarchy_tree: p.hierarchy_tree == null ? null : p.hierarchy_tree,
    relationships: rows.map((r) => ({ source_table: r.source_table, source_column: r.source_column, target_table: r.target_table, target_column: r.target_column, relationship_type: r.relationship_type || null })),
    entities: hierarchyEntities(rows)
  };
}

export function hierarchyView(payload, dec) {
  const p = payload || {};
  const single = !!(p.single_table_import || p.system_default_hierarchy);
  const rows = hierarchyRows(p);
  const refs = rows.filter((r) => r.reference);
  const review = rows.filter((r) => !r.reference);
  const actions = review.map((r) => hierAction(r, dec));
  const tally = { confirmed: actions.filter((a) => a === 'confirm').length, modified: actions.filter((a) => a === 'modify').length, rejected: actions.filter((a) => a === 'reject').length };
  const cyclesN = num(p.total_cycles) || 0;
  const orphansN = num(p.total_orphans) || 0;
  const confirmed = review.filter((r, i) => actions[i] !== 'reject').map((r) => hierSent(r, dec));
  const loops = hierarchyCycles(confirmed);
  const tree = p.hierarchy_tree == null ? null : (typeof p.hierarchy_tree === 'string' ? p.hierarchy_tree : JSON.stringify(p.hierarchy_tree, null, 2));
  return {
    title: 'How do your records nest?',
    blurb: single ? 'Your file has one table, so Plenum’s standard structure is used. Check any column references on your data below.'
      : 'These links say what belongs to what — assets in sites, work orders on assets. Confirm, change or reject each one.',
    single: single, importTable: str(p.import_table_name), importRole: str(p.import_table_plenum_role),
    stats: [
      { label: 'FK relationships', value: num(p.total_hierarchies) === null ? rows.length : p.total_hierarchies, tone: 'info' },
      { label: 'Cycles detected', value: cyclesN, tone: cyclesN > 0 ? 'risk' : 'ok' },
      { label: 'Orphaned records', value: orphansN, tone: orphansN > 0 ? 'warn' : 'ok' },
      { label: 'To review', value: review.length, tone: 'neutral' }
    ],
    tally: tally,
    documents: documentSummary(p.document_inventory),
    tablesMeta: arr(p.tables_metadata).filter(isObj).map((t) => {
      const pks = arr(t.primary_keys).map(String);
      return {
        name: String(t.table_name || ''),
        dims: (num(t.row_count) === null ? '—' : count(t.row_count)) + ' rows · ' + (num(t.column_count) === null ? '—' : count(t.column_count)) + ' cols',
        pk: pks.length && !t.surrogate_key ? 'PK: ' + pks.join(', ') + (t.composite_primary_key ? ' (composite)' : '') : '',
        surrogate: !!t.surrogate_key,
        fk: arr(t.foreign_keys).length ? 'FK: ' + arr(t.foreign_keys).join(', ') : '',
        sourceFile: str(t.source_file) || '',
        columns: arr(t.columns).filter(isObj).map((c) => ({
          name: String(c.column_name || ''), isPk: pks.indexOf(c.column_name) > -1, type: String(c.datatype || ''),
          nullable: c.nullable ? 'yes' : 'no', unique: c.unique ? 'yes' : 'no',
          samples: arr(c.sample_values).map(String).join(', ')
        }))
      };
    }),
    proposed: p.proposed_structure || single ? {
      label: single ? 'System default Plenum hierarchy' : 'Proposed structure',
      value: p.proposed_structure || 'sites → locations → assets → work_orders → tasks'
    } : null,
    refs: refs.map((r) => ({
      key: relKey(r), from: r.source_table + '.' + r.source_column,
      to: (r.target_table || '') + (r.target_column ? '.' + r.target_column : ''),
      rel: (r.relationship_type || '').toUpperCase(), reasoning: r.reasoning || ''
    })),
    showTree: p.hierarchy_tree != null || rows.length > 0,
    treeTitle: single ? 'Default hierarchy' : 'Detected hierarchy',
    tree: tree,
    entities: hierarchyEntities(rows),
    json: JSON.stringify(hierarchyJson(p, rows), null, 2),
    review: review.map((r, i) => {
      const k = relKey(r);
      const a = actions[i];
      const conf = r.confidence === null ? null : Math.round(r.confidence * 100);
      return {
        key: k, type: (r.type || 'fk').toUpperCase(), typeKey: r.type || 'fk',
        from: r.source_table + '.' + r.source_column, toTable: r.target_table || '', toColumn: r.target_column || '',
        rel: (r.relationship_type || '').toUpperCase(), conf: conf === null ? '' : conf + '%', confOk: conf !== null && conf >= 80,
        match: r.data_match_rate === null ? '' : 'rows matched ' + Math.round(r.data_match_rate * 100) + '%',
        reasoning: r.reasoning || '', action: a,
        selfRef: a !== 'reject' && !!r.target_table && r.source_table === ((dec || {})[k + '#table'] && a === 'modify' ? String(dec[k + '#table']).trim() : r.target_table),
        modTable: (dec || {})[k + '#table'] == null ? (r.target_table || '') : dec[k + '#table'],
        modColumn: (dec || {})[k + '#column'] == null ? (r.target_column || '') : dec[k + '#column']
      };
    }),
    emptyText: single ? 'No column-level references need review. Accept the default Plenum hierarchy to continue.'
      : 'No relationships require manual review. All hierarchies were auto-detected with high confidence.',
    loops: loops.map((c) => c.join(' → ')),
    submitLabel: 'Confirm links (' + tally.confirmed + ' confirmed · ' + tally.modified + ' changed · ' + tally.rejected + ' rejected)'
  };
}

// The four "Download metadata JSON" files, built in the page from the gate payload.
export function hierarchyExport(kind, payload, dec, migrationId, nowIso) {
  const p = payload || {};
  const rows = hierarchyRows(p);
  const tables = arr(p.tables_metadata).filter(isObj);
  const tableRows = tables.map((t) => ({ table_name: t.table_name, source_document: t.source_file || null, row_count: t.row_count, column_count: t.column_count, primary_keys: arr(t.primary_keys), foreign_keys: arr(t.foreign_keys) }));
  const columns = [];
  tables.forEach((t) => arr(t.columns).filter(isObj).forEach((c) => columns.push({
    table: t.table_name, column_name: c.column_name, datatype: c.datatype, nullable: !!c.nullable, unique: !!c.unique,
    primary_key: arr(t.primary_keys).indexOf(c.column_name) > -1, foreign_key: arr(t.foreign_keys).indexOf(c.column_name) > -1,
    sample_values: arr(c.sample_values)
  })));
  const rels = rows.map((r) => ({ source_table: r.source_table, source_column: r.source_column, target_table: r.target_table, target_column: r.target_column, type: r.type, relationship_type: r.relationship_type, confidence: r.confidence, data_match_rate: r.data_match_rate, reasoning: r.reasoning }));
  const hier = { hierarchy: p.hierarchy_tree == null ? null : p.hierarchy_tree, proposed_structure: p.proposed_structure || null, relationships: rels, entities: hierarchyEntities(rows) };
  const id8 = String(migrationId || '').slice(0, 8);
  const ts = String(nowIso || new Date().toISOString()).replace(/[:.]/g, '-').slice(0, 19);
  let data;
  if (kind === 'table_metadata') data = { tables: tableRows };
  else if (kind === 'column_metadata') data = { columns: columns };
  else if (kind === 'hierarchy') data = hier;
  else {
    const review = rows.filter((r) => !r.reference);
    data = {
      migration_id: migrationId || null, script_code: null, version: id8, generated_at: nowIso || new Date().toISOString(),
      documents: arr(isObj(p.document_inventory) ? p.document_inventory.files : null),
      tables: tableRows, columns: columns,
      foreign_keys: rels.filter((r) => String(r.relationship_type || '').toUpperCase() !== 'ONTOLOGY'),
      hierarchy: hier.hierarchy, proposed_structure: hier.proposed_structure, entities: hier.entities,
      ontology_matches: rels.filter((r) => String(r.relationship_type || '').toUpperCase() === 'ONTOLOGY'),
      semantic_mappings: rels.map((r) => ({ source: r.source_table + '.' + r.source_column, target: r.target_table ? r.target_table + (r.target_column ? '.' + r.target_column : '') : null, relationship_type: r.relationship_type, semantic_score: r.confidence, numeric_match_score: r.data_match_rate })),
      user_overrides: review.map((r) => {
        const s = hierSent(r, dec);
        return { source_table: r.source_table, source_column: r.source_column, original_target_table: r.target_table, original_target_column: r.target_column, action: hierAction(r, dec), modified_target_table: s.target_table || null, modified_target_column: s.target_column || null };
      }),
      validation_results: { total_hierarchies: num(p.total_hierarchies) === null ? rows.length : p.total_hierarchies, total_cycles: num(p.total_cycles) || 0, total_orphans: num(p.total_orphans) || 0, review_count: review.length }
    };
  }
  return { filename: 'migration_' + id8 + '_' + (kind || 'full_package') + '_' + ts + '.json', text: JSON.stringify(data, null, 2) };
}

// ── the write (Gate 3) ──────────────────────────────────────────────────────────────
//
// gates/gate-final.tsx, as a screen. Not as CAFM runs it: against this service CAFM never shows
// that screen — the last gate is named "write", and CAFM answers {confirmed: true} on its own the
// moment it appears, so rows land in plenum_cafm with nobody having clicked. Here the write stays
// a person's two clicks (arm, then confirm), and Reject stays, because this is the one answer in
// the flow that changes the database.

export function finalView(payload, doc) {
  const sm = isObj(payload && payload.summary) ? payload.summary : {};
  const oc = num(sm.overall_confidence);
  const raw = oc === null ? (num(sm.mapping_coverage_pct) || 0) : (oc <= 1 ? oc * 100 : oc);
  const pct = Math.max(0, Math.min(100, Math.round(raw)));
  const file = [sm.source_filename, doc && doc.source_filename].find((f) => typeof f === 'string' && f.trim() && f.trim().toLowerCase() !== 'unknown') || null;
  const total = num(sm.total_entities) === null ? num(sm.rows_to_write) : sm.total_entities;
  const counts = isObj(sm.entity_counts) ? Object.entries(sm.entity_counts).filter(([, v]) => num(v) !== null) : [];
  const facts = [['Total fields', sm.total_fields], ['T1 auto-mapped', sm.t1_mapped], ['T2 auto-mapped', sm.t2_auto_mapped], ['Human reviewed', sm.t2_human_reviewed], ['Skipped', sm.skipped]]
    .filter(([, v]) => v !== null && v !== undefined).map(([label, v]) => ({ label: label, value: String(v) }));
  const rowsN = total === null ? counts.reduce((a, [, v]) => a + v, 0) : total;
  return {
    title: 'Ready to write ' + count(rowsN) + ' ' + plural(rowsN, 'row') + ' to Plenum',
    blurb: 'Nothing has been written yet. Writing adds these rows to the live database and cannot be undone.',
    rowsLabel: count(rowsN) + ' ' + plural(rowsN, 'row'),
    pct: pct, pctLabel: pct + '%', tone: pct >= 85 ? 'ok' : pct >= 70 ? 'warn' : 'risk',
    file: file, sourceType: str(sm.source_type) || '',
    total: total === null ? null : count(total),
    counts: counts.map(([k, v]) => ({ label: k, value: count(v) })),
    facts: facts, hierarchy: str(sm.hierarchy) || '',
    rows: rowsN,
    writeLabel: 'Yes, write ' + count(rowsN) + ' ' + plural(rowsN, 'row')
  };
}

// ── shared: the uploaded documents ──────────────────────────────────────────────────
//
// DocumentSummary (migration-metadata-view.tsx) — shown at the pre-semantic and hierarchy gates.
export function documentSummary(inv) {
  return arr(isObj(inv) ? inv.files : null).filter(isObj).map((f) => {
    const tables = arr(f.tables).filter(isObj);
    const tc = num(f.table_count) === null ? tables.length : f.table_count;
    return {
      name: String(f.file_name || ''), type: String(f.file_type || '').toUpperCase(),
      sheets: num(f.sheet_count) !== null && f.sheet_count > 1 ? f.sheet_count + ' sheets' : '',
      tablesLabel: tc + ' ' + plural(tc, 'table'),
      tables: tables.map((t, i) => ({
        branch: i === tables.length - 1 ? '└──' : '├──', name: String(t.table_name || ''),
        dims: (num(t.row_count) === null ? '—' : count(t.row_count)) + ' rows · ' + (num(t.column_count) === null ? '—' : count(t.column_count)) + ' cols'
      }))
    };
  });
}

// ── pre-semantic (table routing, then column matching) ──────────────────────────────
//
// gates/gate-pre-semantic.tsx. The service asks this gate twice under one name: pass 1 is
// locked_phase "tables" (routing — only table_overrides is read), pass 2 is "columns"
// (decisions, with pass 1's overrides merged underneath). Where this differs from CAFM:
//  - Pass 1 sends a sheet only when its target is new or differs from the routing proposed.
//    CAFM echoes every sheet, and the service treats an echoed sheet whose routing differs from
//    Node 1's match as REROUTED — discarding its column approvals and sending every column to
//    semantic (pre_semantic_review_node, the reroute check against cafm_table_matches).
//  - Pass 2 sends no table_overrides: a pass-2 override wins over pass 1, and CAFM's could not
//    tell a new table made in pass 1 from a name it did not know, so it could undo that choice.
//  - A new table's column is named snake_case(source), not the unrelated canonical match the
//    rules found for it on some other table (Hoistra used to echo target_field).
// CAFM's "Change" in pass 2 is not here: it calls preview-canonical-overrides, which writes the
// run's state despite its name.

export const PS_DATA_TYPES = ['VARCHAR(255)', 'TEXT', 'INTEGER', 'BIGINT', 'NUMERIC', 'BOOLEAN', 'DATE', 'TIMESTAMP'];

export function toSnakeCase(s) {
  const x = String(s == null ? '' : s).replace(/\(([^)]*)\)/g, ' $1 ').replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  return x || 'col';
}

export function inferDataType(field) {
  const f = String(field || '').toLowerCase();
  if (/timestamp|_at\b|datetime|created|updated|modified/.test(f)) return 'TIMESTAMP';
  if (/date|_dt\b|dob|dtm/.test(f)) return 'DATE';
  if (/is_|^is\b|bool|flag|active|enabled|deactivated/.test(f)) return 'BOOLEAN';
  if (/amount|price|cost|total|rate|latitude|longitude|lat\b|lon\b|balance|qty|quantity/.test(f)) return 'NUMERIC';
  if (/_id\b|^id$|count|number|_no\b|^num/.test(f)) return 'INTEGER';
  return 'VARCHAR(255)';
}

const normName = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const depl = (s) => (s.length > 3 && s.endsWith('s') ? s.slice(0, -1) : s);

export function tableNameSimilarity(a, b) {
  const x = normName(a), y = normName(b);
  if (!x || !y) return 0;
  if (x === y) return 1;
  if (depl(x) === depl(y)) return 0.97;
  const grams = (s) => { const g = []; for (let i = 0; i < s.length - 1; i++) g.push(s.slice(i, i + 2)); return g; };
  const gx = grams(x), gy = grams(y);
  if (!gx.length || !gy.length) return 0;
  const pool = gy.slice();
  let hit = 0;
  gx.forEach((g) => { const i = pool.indexOf(g); if (i > -1) { hit += 1; pool.splice(i, 1); } });
  return (2 * hit) / (gx.length + gy.length);
}

const psPhase = (p) => (String((p && p.locked_phase) || '') === 'tables' || String((p && p.gate_step) || '') === 'table_routing'
  || (!(p && p.review_items_by_table) && !!(p && p.suggested_target_by_table)) ? 'tables' : 'columns');
export const preSemanticPhase = psPhase;

function psSources(p) {
  const out = [];
  const add = (t) => { if (t && out.indexOf(t) < 0) out.push(t); };
  Object.keys(isObj(p.suggested_target_by_table) ? p.suggested_target_by_table : {}).forEach(add);
  Object.keys(isObj(p.review_items_by_table) ? p.review_items_by_table : {}).forEach(add);
  Object.keys(isObj(p.auto_approved_by_table) ? p.auto_approved_by_table : {}).forEach(add);
  Object.keys(isObj(p.unresolved_suggestion_by_table) ? p.unresolved_suggestion_by_table : {}).forEach(add);
  arr(isObj(p.table_resolution) ? p.table_resolution.metadata_cards : null).forEach((c) => add(isObj(c) ? c.table : null));
  return out;
}

function psGroups(p) {
  const groups = arr(isObj(p.table_resolution) && isObj(p.table_resolution.duplicate_tables) ? p.table_resolution.duplicate_tables.groups : null)
    .filter((g) => isObj(g) && Array.isArray(g.tables));
  const of = {};
  groups.forEach((g) => g.tables.forEach((t) => { of[t] = g; }));
  return of;
}

// One row per sheet, a duplicate group being one row for all its sheets.
function psRepresentatives(sources, groupOf) {
  const seen = new Set();
  const reps = [];
  sources.forEach((s) => {
    const g = groupOf[s];
    if (!g) { reps.push({ key: s, label: s, members: [s] }); return; }
    const k = g.tables.join('|');
    if (seen.has(k)) return;
    seen.add(k);
    reps.push({ key: s, label: g.label || s, members: g.tables.filter((t) => sources.indexOf(t) > -1).concat(g.tables.filter((t) => sources.indexOf(t) < 0)), count: g.count || g.tables.length });
  });
  return reps;
}

function psCanonical(p, sources) {
  const list = arr(p.existing_canonical_tables).map(String);
  const out = list.length ? list.slice() : sources.slice();
  Object.values(isObj(p.table_routing_suggestion_by_table) ? p.table_routing_suggestion_by_table : {}).forEach((sg) => {
    if (isObj(sg) && sg.action === 'assign' && sg.target && out.indexOf(sg.target) < 0) out.push(sg.target);
  });
  return out;
}

function psDefault(p, src, canonical) {
  const sug = (p.suggested_target_by_table || {})[src];
  if (sug && canonical.indexOf(sug) > -1) return { target: sug, isNew: false };
  const r = (p.table_routing_suggestion_by_table || {})[src];
  if (isObj(r) && r.action === 'assign' && r.target && canonical.indexOf(r.target) > -1) return { target: r.target, isNew: false };
  if (isObj(r) && r.action === 'create' && r.suggested_new_name) return { target: toSnakeCase(r.suggested_new_name), isNew: true };
  return { target: toSnakeCase(src), isNew: true };
}

const psChoice = (p, src, canonical, dec) => {
  const v = (dec || {})['rt:' + src];
  return isObj(v) && typeof v.target === 'string' ? { target: v.target, isNew: !!v.isNew } : psDefault(p, src, canonical);
};

export function tableRoutingView(payload, dec) {
  const p = payload || {};
  const sources = psSources(p);
  const groupOf = psGroups(p);
  const canonical = psCanonical(p, sources);
  const sorted = canonical.slice().sort();
  const confOf = isObj(p.table_match_confidence_by_table) ? p.table_match_confidence_by_table : {};
  const candOf = isObj(p.table_match_candidates_by_table) ? p.table_match_candidates_by_table : {};
  const reps = psRepresentatives(sources, groupOf);
  const rows = reps.map((r) => {
    const ch = psChoice(p, r.key, canonical, dec);
    const n = normName(r.key), t = normName(ch.target);
    // A table the reader picked is theirs, not a guess to check.
    const yours = !ch.isNew && !!ch.target && isObj((dec || {})['rt:' + r.key]) && ch.target !== (p.suggested_target_by_table || {})[r.key];
    const match = ch.isNew ? 'new' : !ch.target ? 'none' : yours ? 'yours' : (n === t || depl(n) === depl(t)) ? 'exact' : 'semantic';
    const sg = (p.table_routing_suggestion_by_table || {})[r.key];
    const suggestion = match !== 'none' || !isObj(sg) ? null
      : sg.action === 'assign' && sg.target ? { label: 'Suggested: ' + sg.target, title: "Assign this sheet to the existing CAFM table '" + sg.target + "'", target: sg.target, isNew: false }
      : sg.action === 'create' && sg.suggested_new_name ? { label: "Create new table '" + sg.suggested_new_name + "'", title: "No CAFM table matched — create a new table '" + sg.suggested_new_name + "' (suggested from facilities-management vocabulary)", target: toSnakeCase(sg.suggested_new_name), isNew: true }
      : null;
    const conf = num(confOf[r.key]);
    const best = {};
    canonical.forEach((c) => { const s = tableNameSimilarity(r.key, c); if (s >= 0.34) best[c] = s; });
    arr(candOf[r.key]).filter(isObj).forEach((c) => {
      if (canonical.indexOf(c.table) < 0) return;
      const v = num(c.pct) === null ? 0 : (c.pct > 1 ? c.pct / 100 : c.pct);
      best[c.table] = Math.max(best[c.table] || 0, v);
    });
    const top = Object.entries(best).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([table, v]) => ({ table: table, pct: Math.round(v * 100), active: !ch.isNew && ch.target === table }));
    return {
      key: r.key, label: r.label, members: r.members, group: r.members.length > 1 ? { count: r.count || r.members.length, title: 'duplicate sheets routed together: ' + r.members.join(', ') } : null,
      target: ch.target, isNew: ch.isNew, match: match, suggestion: suggestion,
      // The service's confidence is for the table it proposed; a sheet pointed elsewhere has none.
      conf: ch.isNew || conf === null || conf <= 0 || ch.target !== (p.suggested_target_by_table || {})[r.key] ? '' : Math.round((conf > 1 ? conf / 100 : conf) * 100) + '% match',
      confTone: conf === null ? 'neutral' : (conf > 1 ? conf / 100 : conf) >= 0.9 ? 'ok' : (conf > 1 ? conf / 100 : conf) >= 0.7 ? 'warn' : 'risk',
      options: Array.from(new Set(sorted.concat(!ch.isNew && ch.target ? [ch.target] : []))).sort(),
      top: top
    };
  });
  return {
    rows: rows, count: rows.length,
    complete: rows.every((r) => r.target && String(r.target).trim()),
    documents: documentSummary(p.document_inventory),
    panels: tableResolutionPanels(p, false)
  };
}

// The read-only table-mapping steps (B8.1 → B12.1) from the gate's own table_resolution.
function tableResolutionPanels(p, routingConfirmed) {
  const tr = isObj(p.table_resolution) ? p.table_resolution : {};
  const pctOf = (x) => (num(x) === null ? '—' : Math.round(x * 100) + '%');
  const tone = (x) => (num(x) === null ? 'neutral' : x >= 0.9 ? 'ok' : x >= 0.7 ? 'warn' : 'risk');
  const mTone = (m) => /(exact|levenshtein)/i.test(m || '') ? 'ok' : /(rag|alias)/i.test(m || '') ? 'accent' : /(semantic|suggest)/i.test(m || '') ? 'warn' : 'neutral';
  // The tone keys on the service's own code; the words are the reader's (as the wizard's
  // migration-mapping-utils.ts plainMethodLabel / plainAliasSource).
  const mWord = (m) => /^rag(\s*\/\s*alias|_alias)?$/i.test(String(m || '').trim()) ? 'alias match' : /^(semantic )?llm$/i.test(String(m || '').trim()) ? 'AI match' : m;
  const aliasWord = (a) => (a === 'FM_TABLE_SYNONYMS' ? 'facilities-management vocabulary' : a);
  const sem = arr(tr.semantic).filter(isObj);
  return {
    pk: uniqueTablesView(p).pkRows,
    deterministic: arr(tr.deterministic).filter(isObj).map((r) => ({ source: r.source, method: mWord(r.method) || 'none', methodTone: mTone(r.method), dest: r.destination || '—', conf: pctOf(r.confidence), confTone: tone(r.confidence), note: r.note || '' })),
    rag: arr(tr.rag_alias).filter(isObj).map((r) => ({ source: r.source, hit: r.alias_hit || '', dest: r.destination || '—', conf: pctOf(r.confidence), confTone: tone(r.confidence), aliasSource: aliasWord(r.alias_source) || '' })),
    semantic: sem.map((r) => ({ source: r.source, signal: r.signal == null ? '' : String(r.signal), dest: r.destination || '—', conf: pctOf(r.confidence), confTone: tone(r.confidence), band: r.band === 'suggested' ? 'suggested — confirm' : 'review required', bandTone: r.band === 'suggested' ? 'warn' : 'neutral' })),
    final: routingConfirmed ? arr(tr.final_decisions).filter(isObj).map((r) => ({ source: r.source, dest: r.destination || '— unmatched —', method: mWord(r.method) || '', methodTone: mTone(r.method), conf: pctOf(r.confidence), confTone: tone(r.confidence) })) : [],
    show: arr(tr.metadata_cards).length > 0 || arr(tr.final_decisions).length > 0
  };
}

export function tableRoutingBody(payload, dec) {
  const p = payload || {};
  const sources = psSources(p);
  const groupOf = psGroups(p);
  const canonical = psCanonical(p, sources);
  const proposed = isObj(p.suggested_target_by_table) ? p.suggested_target_by_table : {};
  const overrides = {};
  psRepresentatives(sources, groupOf).forEach((r) => {
    const ch = psChoice(p, r.key, canonical, dec);
    const target = String(ch.target || '').trim();
    if (!target) return;
    r.members.forEach((m) => {
      if (ch.isNew) overrides[m] = { target_table: toSnakeCase(target), is_new_table: true };
      else if (target !== proposed[m]) overrides[m] = { target_table: target, is_new_table: false };
    });
  });
  return { decisions: {}, table_overrides: overrides };
}

// ── pass 2: column matching ──

const psKey = (t, f) => 'ps:' + t + '.' + f;
const puKey = (t, f) => 'pu:' + t + '.' + f;

function psTargetOf(p, src) {
  const t = (isObj(p.suggested_target_by_table) ? p.suggested_target_by_table : {})[src] || src;
  const existing = arr(p.existing_canonical_tables).map((x) => String(x).toLowerCase());
  return { table: t, isNew: existing.length > 0 && existing.indexOf(String(t).toLowerCase()) < 0 };
}

function psColumns(p, table) {
  const by = isObj(p.canonical_columns_by_table) ? p.canonical_columns_by_table : {};
  const hit = by[table] || by[String(table).toLowerCase()] || Object.entries(by).find(([k]) => k.toLowerCase() === String(table).toLowerCase());
  return arr(Array.isArray(hit) ? hit : hit ? hit[1] : null).map(String);
}

// Unresolved fields: the kept Node 2 list first, then the fields the rules at least suggested for.
function psUnresolved(p, stash) {
  const out = {};
  const put = (t, f) => { const name = typeof f === 'string' ? f : isObj(f) ? (f.source_field || f.field_name) : null; if (!name) return; (out[t] = out[t] || []); if (out[t].indexOf(name) < 0) out[t].push(name); };
  const src = isObj(stash) && Object.keys(stash).length ? stash : (isObj(p.unresolved_suggestion_by_table) ? Object.fromEntries(Object.entries(p.unresolved_suggestion_by_table).map(([t, m]) => [t, Object.keys(isObj(m) ? m : {})])) : {});
  Object.entries(src).forEach(([t, fields]) => arr(fields).forEach((f) => put(t, f)));
  // Not a field already under review or auto-approved.
  Object.keys(out).forEach((t) => {
    const taken = new Set(arr((p.review_items_by_table || {})[t]).concat(arr((p.auto_approved_by_table || {})[t])).filter(isObj).map((it) => it.source_field));
    out[t] = out[t].filter((f) => !taken.has(f));
    if (!out[t].length) delete out[t];
  });
  return out;
}

const tierTone = (t) => {
  const x = String(t || '');
  if (x === 'T1_exact') return 'ok';
  if (x === 'T1_alias') return 'accent';
  if (x === 'T1_token_containment') return 'warn';
  return 'neutral';
};

export function columnMatchingView(payload, dec, stash) {
  const p = payload || {};
  const d = dec || {};
  const review = isObj(p.review_items_by_table) ? p.review_items_by_table : {};
  const auto = isObj(p.auto_approved_by_table) ? p.auto_approved_by_table : {};
  const unresolved = psUnresolved(p, stash);
  const sugOf = isObj(p.unresolved_suggestion_by_table) ? p.unresolved_suggestion_by_table : {};
  const nearOf = isObj(p.near_duplicate_columns_by_table) ? p.near_duplicate_columns_by_table : {};
  const canon = isObj(p.column_intelligence) && isObj(p.column_intelligence.column_canonical) ? p.column_intelligence.column_canonical : {};
  const groupOf = psGroups(p);
  const sources = [];
  [review, unresolved, auto].forEach((m) => Object.keys(m).forEach((t) => { if (sources.indexOf(t) < 0) sources.push(t); }));
  let approved = 0, semantic = 0, autoSemantic = 0, totalItems = 0;
  const tierCount = {};
  const tables = psRepresentatives(sources, groupOf).map((rep) => {
    const t = rep.key;
    const tgt = psTargetOf(p, t);
    const cols = psColumns(p, tgt.table);
    const colsN = cols.map(normName);
    const items = arr(review[t]).filter(isObj);
    const unres = arr(unresolved[t]);
    const autos = arr(auto[t]).filter(isObj).filter((a) => !items.some((it) => it.source_field === a.source_field));
    const targetsUsed = [];
    const rows = items.map((it) => {
      const k = psKey(t, it.source_field);
      const rename = d[k + '#to'];
      const decision = tgt.isNew ? 'approve' : (d[k] === 'semantic' ? 'semantic' : 'approve');
      const to = rename != null ? String(rename) : (tgt.isNew ? toSnakeCase(it.source_field) : String(it.target_field || ''));
      if (tgt.isNew || decision === 'approve') approved += 1; else semantic += 1;
      if (it.tier) tierCount[it.tier] = (tierCount[it.tier] || 0) + 1;
      if (decision === 'approve' && to) targetsUsed.push(normName(to));
      const canonName = canon[String(t).toLowerCase() + '.' + it.source_field] || canon[t + '.' + it.source_field];
      const conf = num(it.confidence) === null ? null : Math.round(it.confidence * 100);
      return {
        key: k, field: it.source_field, display: canonName && canonName !== it.source_field ? canonName : it.source_field,
        was: canonName && canonName !== it.source_field ? it.source_field : '',
        pk: !!it.is_primary_key, merged: arr(it.merged_source_fields).join(', '),
        to: to, selectable: !tgt.isNew && cols.length > 0,
        options: cols.indexOf(to) > -1 || !to ? cols : [to].concat(cols),
        type: d[k + '#type'] || inferDataType(it.source_field),
        tier: String(it.tier || '').replace(/^T1_/, ''), tierTone: tierTone(it.tier), b21: !!it.b21_new_column,
        conf: conf === null ? '' : conf + '%', confTone: conf === null ? 'neutral' : conf >= 95 ? 'ok' : conf >= 85 ? 'warn' : 'risk',
        dest: tgt.isNew ? 'new' : decision === 'semantic' || !to ? '' : colsN.indexOf(normName(to)) > -1 ? 'existing' : 'new',
        modified: rename != null && String(rename) !== String(it.target_field || '') && !tgt.isNew
          ? "User-modified — system suggested '" + (it.target_field || '') + "'" : '',
        rationale: str(it.rationale) || '',
        fits: tgt.isNew || it.b21_new_column ? [] : arr(it.candidates).filter(isObj).slice(0, 3).map((c) => {
          const pc = num(c.confidence) === null ? null : Math.round(c.confidence * 100);
          return { target: c.target_field, pct: pc === null ? '' : pc + '%', tone: pc === null ? 'neutral' : pc >= 90 ? 'ok' : pc >= 70 ? 'warn' : 'risk', active: c.target_field === to, primary: !!c.is_primary };
        }),
        dataType: str(it.data_type) || '', samples: arr(it.sample_values).slice(0, 5).map(String).join(', '),
        decision: decision
      };
    });
    // Two fields into one column is flagged on both.
    rows.forEach((r) => { r.dupTarget = r.decision === 'approve' && r.to && targetsUsed.filter((x) => x === normName(r.to)).length > 1; });
    const leftover = cols.filter((c) => targetsUsed.indexOf(normName(c)) < 0);
    const unresRows = unres.map((f) => {
      const k = puKey(t, f);
      const sug = isObj(sugOf[t]) && isObj(sugOf[t][f]) ? sugOf[t][f] : {};
      totalItems += tgt.isNew ? 1 : 0;
      if (tgt.isNew) { approved += 1; return { key: k, field: f, isNew: true, to: d[k + '#to'] != null ? String(d[k + '#to']) : toSnakeCase(f), type: d[k + '#type'] || str(sug.data_type) || inferDataType(f) }; }
      const assigned = str(d[k]) || '';
      if (assigned) approved += 1; else autoSemantic += 1;
      const free = (c) => leftover.indexOf(c) > -1;
      const exact = leftover.find((c) => normName(c) === normName(f));
      const canonHit = canon[String(t).toLowerCase() + '.' + f];
      const canonSug = canonHit ? leftover.find((c) => normName(c) === normName(canonHit)) : null;
      const sugTarget = sug.target_field && free(sug.target_field) ? sug.target_field : null;
      return {
        key: k, field: f, isNew: false, assigned: assigned, leftover: leftover, onTable: tgt.table,
        defaultAssign: exact || canonSug || sugTarget || '',
        type: str(sug.data_type) || inferDataType(f), samples: arr(sug.sample_values).slice(0, 5).map(String).join(', '),
        suggested: arr(sug.candidates).filter(isObj).slice(0, 3).map((c) => {
          const tf = c.target_field || c.target;
          const pc = num(c.confidence) === null ? '' : Math.round(c.confidence * 100) + '%';
          return { target: tf, pct: pc, free: free(tf) };
        })
      };
    });
    totalItems += items.length;
    autos.forEach(() => { approved += 1; });
    return {
      key: t, label: rep.label, members: rep.members, group: rep.members.length > 1 ? { count: rep.count || rep.members.length, title: 'Duplicate sheets merged (identical columns, routed together): ' + rep.members.join(', ') } : null,
      target: tgt.table, isNew: tgt.isNew, startOpen: items.length > 0,
      t1: items.length, autoN: autos.length, toSemantic: tgt.isNew ? 0 : unresRows.filter((u) => !u.assigned).length,
      allApproved: tgt.isNew ? (items.length + unres.length) > 0 : items.length > 0 && rows.every((r) => r.decision === 'approve'),
      near: arr(nearOf[t]).filter(isObj).map((n) => n.column_a + ' ↔ ' + n.column_b + ' (' + Math.round((num(n.overlap) || 0) * 100) + '% overlap)' + (arr(n.sample_a).length ? ' · e.g. ' + arr(n.sample_a).slice(0, 2).join(', ') : '')),
      rows: rows, unresolved: unresRows,
      autos: autos.map((a) => ({ field: a.source_field, to: a.target_field || '', tier: a.tier === 'T1_identity' ? 'identity → id' : String(a.tier || '').replace(/^T1_/, ''), dest: tgt.isNew ? 'new' : colsN.indexOf(normName(a.target_field)) > -1 ? 'existing' : 'new', samples: arr(a.sample_values).slice(0, 5).map(String).join(', ') })),
      empty: tgt.isNew ? 'This new table will be created with the columns detected in the source.' : 'No fields in this table.'
    };
  });
  return {
    tables: tables, totalItems: totalItems,
    stats: [
      { label: 'T1 Approved', value: approved, tone: 'ok' },
      { label: '→ Semantic (you)', value: semantic, tone: 'accent' },
      { label: 'Auto → Semantic', value: autoSemantic, tone: 'warn' },
      { label: 'Tables', value: tables.length, tone: 'neutral' }
    ],
    tiers: Object.entries(tierCount).sort((a, b) => b[1] - a[1]).map(([t, n]) => ({ label: t.replace(/^T1_/, '') + ': ' + n, tone: tierTone(t) })),
    documents: documentSummary(p.document_inventory),
    panels: tableResolutionPanels(p, true),
    submitLabel: 'Confirm matches (' + approved + ' approved' + (semantic + autoSemantic ? ' · ' + (semantic + autoSemantic) + ' to AI matching' : '') + ')'
  };
}

export function columnMatchingBody(payload, dec, stash) {
  const p = payload || {};
  const d = dec || {};
  const review = isObj(p.review_items_by_table) ? p.review_items_by_table : {};
  const unresolved = psUnresolved(p, stash);
  const groupOf = psGroups(p);
  const decisions = {};
  const tablesOf = new Set(Object.keys(review).concat(Object.keys(unresolved)));
  tablesOf.forEach((t) => {
    // A duplicate group is answered on its first sheet; the others take the same answer per field.
    const g = groupOf[t];
    const rep = g ? (g.tables.find((x) => tablesOf.has(x)) || t) : t;
    const tgt = psTargetOf(p, t);
    const list = [];
    arr(review[t]).filter(isObj).forEach((it) => {
      const k = psKey(rep, it.source_field);
      if (tgt.isNew) {
        list.push({ source_field: it.source_field, decision: 'approve', target_field: String(d[k + '#to'] == null ? '' : d[k + '#to']).trim() || toSnakeCase(it.source_field), data_type: d[k + '#type'] || inferDataType(it.source_field) });
        return;
      }
      const row = { source_field: it.source_field, decision: d[k] === 'semantic' ? 'semantic' : 'approve' };
      const rename = d[k + '#to'] == null ? '' : String(d[k + '#to']).trim();
      if (rename && rename !== it.target_field) row.target_field = rename;
      list.push(row);
    });
    arr(unresolved[t]).forEach((f) => {
      const k = puKey(rep, f);
      if (tgt.isNew) {
        list.push({ source_field: f, decision: 'approve', target_field: String(d[k + '#to'] == null ? '' : d[k + '#to']).trim() || toSnakeCase(f), data_type: d[k + '#type'] || inferDataType(f) });
      } else if (str(d[k])) {
        list.push({ source_field: f, decision: 'approve', target_field: d[k] });
      }
    });
    if (list.length) decisions[t] = list;
  });
  return { decisions: decisions };
}

// ── field mapping (Gate 1) ──────────────────────────────────────────────────────────
//
// CAFM answers this gate on its "Semantic Mapping Review" screen (gates/gate-semantic-review.tsx,
// shown ahead of gate-field-mapping.tsx whenever there is anything flagged), so that is the screen
// drawn here, at the gate itself. Keys, per source table t and field f:
//   dec['fm:t.f'] = 'accept' | 'override' | 'reject'         (flagged; default accept)
//   dec['fm:t.f#mode'] = 'existing' | 'new'                   (override kind)
//   dec['fm:t.f#to'] / '#name' / '#type' / '#nullable'        (override target / new column)
//   dec['fu:t.f'] = 'custom' | 'raw_metadata' | 'skip'       (unmappable; default custom)
//   dec['fu:t.f#name'] / '#type'                              (the column it becomes)
//   dec['ft:t'] = {table, isNew}                               (the canonical table for sheet t)
// Different from CAFM, on purpose:
//  - Override → new column is ONE override with is_new_column (human_review_node builds the
//    ALTER from it) and an explicit target_table. CAFM sent a reject plus an unmapped "custom"
//    entry, which its own unmapped loop then overwrote whenever the sheet had unmappable fields,
//    losing the field.
//  - An unmappable field becomes the name shown, not its raw source name.
//  - Nothing is sent that the service would refuse: a blank or unusable column name stops the
//    submit with the reason. human_review_node returns early on one bad decision and applies NONE
//    of them, while the run carries on.
//  - The AI's alternatives are read as the service sends them ({target, confidence}); Hoistra's
//    old picker read target_field and came up empty.
//  - No server draft: CAFM auto-submits a saved draft, so a draft written here could be sent from
//    a CAFM Web tab. Decisions live in the page until the gate is answered.

export const FM_DATA_TYPES = ['VARCHAR(255)', 'TEXT', 'INTEGER', 'BIGINT', 'NUMERIC', 'BOOLEAN', 'DATE', 'TIMESTAMPTZ', 'UUID'];

// The name the migration service will create (schema_write_node._safe_identifier).
const safeIdent = (raw) => {
  const n = String(raw == null ? '' : raw).trim().toLowerCase().replace(/[ -]/g, '_').replace(/[^a-z0-9_]/g, '');
  return /^[a-z_][a-z0-9_]{0,62}$/.test(n) ? n : '';
};

function fmTables(p) {
  const flagged = isObj(p.review_items_by_table) ? p.review_items_by_table : isObj(p.flagged_by_table) ? p.flagged_by_table : isObj(p.tier2_flagged_by_table) ? p.tier2_flagged_by_table : {};
  const unmapped = isObj(p.unmappable_items_by_table) ? p.unmappable_items_by_table : isObj(p.unmapped_by_table) ? p.unmapped_by_table : isObj(p.tier2_unmappable_by_table) ? p.tier2_unmappable_by_table : {};
  const norm = (it) => (typeof it === 'string' ? { source_field: it } : isObj(it) ? Object.assign({}, it, { source_field: it.source_field || it.field_name }) : null);
  const f = {}, u = {};
  Object.entries(flagged).forEach(([t, list]) => { f[t] = arr(list).map(norm).filter((x) => x && x.source_field); });
  Object.entries(unmapped).forEach(([t, list]) => { u[t] = arr(list).map(norm).filter((x) => x && x.source_field); });
  const names = Array.from(new Set(Object.keys(f).concat(Object.keys(u)))).sort();
  return { flagged: f, unmapped: u, names: names };
}

// Where a sheet's fields land: the routing, then what the service said a custom column would go on.
function fmCanonical(p, t, dec) {
  const v = (dec || {})['ft:' + t];
  if (isObj(v) && typeof v.table === 'string' && v.table.trim()) return { table: v.table.trim(), isNew: !!v.isNew };
  const routed = (isObj(p.table_routing) ? p.table_routing : {})[t];
  if (typeof routed === 'string' && routed) return { table: routed, isNew: false };
  const sa = arr((isObj(p.unmappable_items_by_table) ? p.unmappable_items_by_table : {})[t]).find((it) => isObj(it) && isObj(it.suggested_action) && it.suggested_action.target_table);
  if (sa) return { table: sa.suggested_action.target_table, isNew: false };
  const cmv = arr(isObj(p.combined_mapping_view) ? p.combined_mapping_view.auto_accepted : null).find((r) => isObj(r) && r.source_table === t && r.target_table);
  return { table: cmv ? cmv.target_table : t, isNew: false };
}

const fmSuggestions = (it) => {
  const best = {};
  arr(it.suggestions).concat(arr(it.alternatives), arr(it.top_matches)).forEach((x) => {
    const field = typeof x === 'string' ? x : isObj(x) ? (x.field || x.target_field || x.target || x.value || x.name) : null;
    if (!field) return;
    let s = isObj(x) ? num(x.confidence) ?? num(x.score) ?? num(x.similarity) : null;
    if (s !== null && s > 1) s = s / 100;
    best[field] = Math.max(best[field] ?? -1, s === null ? -1 : s);
  });
  const tf = it.target_field || it.suggested_target;
  if (tf) best[tf] = Math.max(best[tf] ?? -1, num(it.confidence) ?? -1);
  return Object.entries(best).sort((a, b) => b[1] - a[1]).map(([field, s]) => ({ field: field, score: s < 0 ? null : s }));
};

function fmFlaggedDecision(p, t, it, dec) {
  const d = dec || {};
  const k = 'fm:' + t + '.' + it.source_field;
  const suggested = it.target_field || it.suggested_target || null;
  const action = d[k] === 'override' || d[k] === 'reject' || d[k] === 'accept' ? d[k] : (suggested ? 'accept' : 'override');
  const mode = d[k + '#mode'] === 'new' ? 'new' : 'existing';
  return {
    key: k, action: action, mode: mode, suggested: suggested,
    to: d[k + '#to'] == null ? (suggested || '') : String(d[k + '#to']),
    name: d[k + '#name'] == null ? toSnakeCase(it.source_field) : String(d[k + '#name']),
    type: d[k + '#type'] || 'VARCHAR(255)', nullable: d[k + '#nullable'] !== false
  };
}

function fmUnmappedDecision(p, t, it, dec) {
  const d = dec || {};
  const k = 'fu:' + t + '.' + it.source_field;
  const sa = isObj(it.suggested_action) ? it.suggested_action : {};
  const canonMap = isObj(p.column_canonical) ? p.column_canonical : {};
  const display = canonMap[t + '.' + it.source_field] || canonMap[String(t).toLowerCase() + '.' + it.source_field] || '';
  const action = ['custom', 'raw_metadata', 'skip'].indexOf(d[k]) > -1 ? d[k] : (['custom', 'raw_metadata', 'skip'].indexOf(sa.action) > -1 ? sa.action : 'custom');
  return {
    key: k, action: action, display: display && display !== it.source_field ? display : '',
    name: d[k + '#name'] == null ? (display ? toSnakeCase(display) : (sa.custom_column_name || toSnakeCase(it.source_field))) : String(d[k + '#name']),
    type: d[k + '#type'] || sa.data_type || 'VARCHAR(255)'
  };
}

// Why the answer cannot be sent yet, if it cannot. The service would drop every decision.
export function fieldMappingProblems(payload, dec) {
  const p = payload || {};
  const { flagged, unmapped } = fmTables(p);
  const out = [];
  Object.entries(flagged).forEach(([t, list]) => list.forEach((it) => {
    const x = fmFlaggedDecision(p, t, it, dec);
    if (x.action === 'override' && x.mode === 'existing' && !x.to.trim()) out.push(t + '.' + it.source_field + ': override target required');
    if (x.action === 'override' && x.mode === 'new' && !safeIdent(x.name)) out.push(t + '.' + it.source_field + ': new column name required');
  }));
  Object.entries(unmapped).forEach(([t, list]) => list.forEach((it) => {
    const x = fmUnmappedDecision(p, t, it, dec);
    if (x.action !== 'custom') return;
    if (!safeIdent(x.name)) out.push(t + '.' + it.source_field + ': new column name required');
    if (!fmCanonical(p, t, dec).table) out.push(t + '.' + it.source_field + ': target table required');
  }));
  return out;
}

export function fieldMappingBody(payload, dec) {
  const p = payload || {};
  const { flagged, unmapped } = fmTables(p);
  const known = isObj(p.canonical_columns_by_table) ? p.canonical_columns_by_table : {};
  const existingTables = arr(p.existing_canonical_tables).map((x) => String(x).toLowerCase());
  const tableIsNew = (c) => c.isNew || (existingTables.length > 0 && existingTables.indexOf(String(c.table).toLowerCase()) < 0 && !Object.prototype.hasOwnProperty.call(known, c.table));
  const fOut = {}, uOut = {};
  Object.entries(flagged).forEach(([t, list]) => {
    const canon = fmCanonical(p, t, dec);
    fOut[t] = list.map((it) => {
      const x = fmFlaggedDecision(p, t, it, dec);
      if (x.action === 'reject') return { action: 'reject', source_field: it.source_field, target_field: null, rationale: null };
      if (x.action === 'override' && x.mode === 'new') {
        return {
          action: 'override', source_field: it.source_field, target_field: safeIdent(x.name),
          is_new_column: true, target_table: safeIdent(canon.table) || canon.table, is_new_table: tableIsNew(canon),
          new_table_pk: 'id', data_type: x.type, nullable: x.nullable, rationale: 'New column created at the review gate'
        };
      }
      if (x.action === 'override') return { action: 'override', source_field: it.source_field, target_field: x.to.trim(), rationale: null };
      return { action: 'accept', source_field: it.source_field, target_field: x.suggested, rationale: null };
    });
  });
  Object.entries(unmapped).forEach(([t, list]) => {
    const canon = fmCanonical(p, t, dec);
    uOut[t] = list.map((it) => {
      const x = fmUnmappedDecision(p, t, it, dec);
      if (x.action !== 'custom') return { action: x.action, source_field: it.source_field, target_table: null, custom_column_name: null, data_type: null };
      const isNewT = tableIsNew(canon);
      return {
        action: 'custom', source_field: it.source_field, target_table: isNewT ? (safeIdent(canon.table) || canon.table) : canon.table,
        custom_column_name: safeIdent(x.name), data_type: x.type, nullable: true,
        is_new_table: isNewT, new_table_pk: isNewT ? 'id' : null
      };
    });
  });
  return { flagged: fOut, unmapped: uOut };
}

export function fieldMappingView(payload, dec) {
  const p = payload || {};
  const d = dec || {};
  const { flagged, unmapped, names } = fmTables(p);
  const cmv = isObj(p.combined_mapping_view) ? p.combined_mapping_view : {};
  const autoAccepted = arr(cmv.auto_accepted).filter(isObj);
  const tally = { accepted: 0, rejected: 0, overridden: 0, newColumns: 0 };
  const existing = arr(p.existing_canonical_tables).map(String).sort();
  const pctTxt = (s) => (s === null || s === undefined ? '—' : Math.round(s * 100) + '%');
  const toneOf = (s) => (s === null || s === undefined ? 'neutral' : s >= 0.85 ? 'ok' : s >= 0.65 ? 'warn' : 'risk');
  const tables = names.map((t) => {
    const canon = fmCanonical(p, t, d);
    const cols = psColumns(p, canon.table);
    const fl = arr(flagged[t]).map((it) => {
      const x = fmFlaggedDecision(p, t, it, d);
      if (x.action === 'accept') tally.accepted += 1;
      else if (x.action === 'reject') tally.rejected += 1;
      else { tally.overridden += 1; if (x.mode === 'new') tally.newColumns += 1; }
      const sugs = fmSuggestions(it);
      const top = sugs.slice(0, 3);
      const cur = x.action === 'override' && x.mode === 'existing' ? x.to : null;
      if (cur && !top.some((s) => s.field === cur)) { const extra = sugs.find((s) => s.field === cur); if (extra) top.push(extra); }
      const conf = num(it.confidence);
      const safe = safeIdent(x.name);
      return {
        key: x.key, field: it.source_field, action: x.action, mode: x.mode,
        target: x.suggested || '—',
        scores: top.map((s) => ({ field: s.field, score: pctTxt(s.score), active: x.action === 'override' && x.mode === 'existing' && x.to === s.field })),
        tier: str(it.tier) || 'T2_semantic', conf: pctTxt(conf), confTone: toneOf(conf), confWidth: conf === null ? 0 : Math.round(conf * 100),
        rationale: str(it.rationale) || '',
        breakdown: isObj(it.score_breakdown) ? [['Semantic', 'semantic'], ['Keyword', 'keyword'], ['Datatype', 'datatype'], ['Numeric pattern', 'numeric_pattern'], ['Vocabulary', 'ontology']]
          .map(([label, key]) => ({ label: label, v: num(it.score_breakdown[key]) ?? num(it.score_breakdown[key + '_score']) }))
          .filter((b) => b.v !== null).map((b) => ({ label: b.label, pct: Math.round(b.v * 100), tone: b.v >= 0.85 ? 'ok' : b.v >= 0.6 ? 'warn' : 'risk' })) : [],
        to: x.to, columnOptions: Array.from(new Set(sugs.map((s) => s.field).concat(cols))),
        name: x.name, nameSafe: safe, type: x.type, nullable: x.nullable,
        preview: 'Creates the column ' + (safe || '…') + ' on ' + (safeIdent(canon.table) || canon.table || '…') + ' on submit.',
        destTable: canon.table
      };
    });
    const um = arr(unmapped[t]).map((it) => {
      const x = fmUnmappedDecision(p, t, it, d);
      if (x.action === 'custom') tally.newColumns += 1;
      const safe = safeIdent(x.name);
      return {
        key: x.key, field: it.source_field, display: x.display, action: x.action,
        name: x.name, nameSafe: safe, type: x.type,
        dest: canon.isNew ? (canon.table || 'new table') + ' (new)' : canon.table,
        existingCols: canon.isNew ? [] : cols,
        collision: !canon.isNew && safe && cols.map((c) => c.toLowerCase()).indexOf(safe) > -1
          ? '⚠ ' + safe + ' already exists on ' + canon.table + ' — picking it from the list above will map this field to the existing column instead of creating a duplicate.' : ''
      };
    });
    return {
      key: t, canonical: canon.table, canonicalIsNew: canon.isNew,
      tableOptions: Array.from(new Set(existing.concat(!canon.isNew && canon.table ? [canon.table] : []))).sort(),
      t2: fl.length, unmappable: um.length,
      allAccepted: fl.every((r) => r.action === 'accept'),
      flagged: fl, unmapped: um
    };
  });
  const flaggedN = num(p.total_flagged) === null ? tables.reduce((a, t) => a + t.t2, 0) : p.total_flagged;
  const unmappableN = num(p.total_unmappable) === null ? tables.reduce((a, t) => a + t.unmappable, 0) : p.total_unmappable;
  const totalItems = tables.reduce((a, t) => a + t.t2 + t.unmappable, 0);
  return {
    title: 'Review the matches the AI wasn’t sure about',
    blurb: 'Accept a match, pick a better column, or reject the field. Fields nothing matched become new columns unless you choose otherwise.',
    totalItems: totalItems,
    alert: isObj(p.confidence_alert) && p.confidence_alert.message ? String(p.confidence_alert.message) : '',
    counters: [
      { label: 'Auto accepted', value: num(p.auto_accepted_count) === null ? autoAccepted.length : p.auto_accepted_count, tone: 'ok' },
      { label: 'Flagged', value: flaggedN, tone: 'warn' },
      { label: 'Unmappable', value: unmappableN, tone: 'risk' }
    ],
    accepted: autoAccepted.map((r) => ({
      source: r.source_table + '.' + r.source_field,
      dest: r.target_table && r.target_field ? r.target_table + '.' + r.target_field : r.target_field || '—',
      conf: pctTxt(num(r.confidence))
    })),
    tally: [
      { label: 'Accepted', value: tally.accepted, tone: 'ok' },
      { label: 'Rejected', value: tally.rejected, tone: 'risk' },
      { label: 'Overridden', value: tally.overridden, tone: 'warn' },
      { label: 'New columns', value: tally.newColumns, tone: 'accent' }
    ],
    tables: tables,
    types: FM_DATA_TYPES,
    submitLabel: 'Submit decisions (' + tally.accepted + ' accepted · ' + tally.overridden + ' changed · ' + tally.rejected + ' rejected)'
  };
}
