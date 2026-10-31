// samplingActions — everything the Map and Progress tabs do: read the batch plan, the batches, the map and the
// progress figures for the chosen period, fill, preview and draw a review batch, withdraw one, read and download
// the manifest, bring a map group forward, and open a map answer on Conversations. Every request goes through
// reviewApi.js. State lives in the store's `sampling` slice, tagged with the store's seq (logic/sampling.js
// samplingOf): a company switch (resetForCompany bumps the seq) leaves the old slice unread. A reply issued
// before the switch is dropped and writes nothing; a reply overtaken by a newer read of the same thing is
// dropped; the app's stale-company error stays quiet.
//
// Create draws exactly what the preview showed: the previewed request, with its period and seed. While a
// preview or a draw is running nothing else is sent, so one click draws one batch.
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { reviewApi } from './reviewApi.js';
import { openInConversations } from './actions.js';
import { batchNotice, batchRequest, nextForm, samplingOf, samplingWindow } from './logic/sampling.js';

const get = () => tlStore.get();
const R = () => samplingOf(get());
const setR = (patch) => tlStore.set((s) => {
  const r = samplingOf(s);
  return { sampling: Object.assign({}, r, typeof patch === 'function' ? patch(r) : patch) };
});
const live = (seq) => seq === get().seq;
const msg = (e) => (e && e.message) || String(e);
const windowNow = () => samplingWindow(R().range);
const BLOCKS = [5, 10, 20];
const asks = { plan: 0, batches: 0, map: 0, progress: 0, manifest: 0 };

// ── the tabs ────────────────────────────────────────────────────────────────────────────────────────────────
// What the Map tab and the Progress tab read when they open.
export function loadMapTab() { return Promise.all([loadPlan(), loadBatches(), loadMap()]); }
export function loadProgressTab() { return Promise.all([loadPlan(), loadProgress()]); }

// A new period: what is read again is the plan, the map and (on Progress) the figures; a preview and a chosen
// map group belonged to the old period.
export function setRange(key) {
  setR({ range: key, preview: null, mapFocus: null });
  loadMap();
  loadPlan();
  if (get().tab === 'progress') loadProgress();
}

// ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────
export async function loadPlan() {
  const seq = get().seq;
  const ask = (asks.plan += 1);
  try {
    const out = await reviewApi.samplingPlan(windowNow());
    if (!live(seq) || ask !== asks.plan) return;
    setR({ plan: out, planError: '', planLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.plan) return;
    setR({ planError: msg(e) });
  }
}

export async function loadBatches() {
  const seq = get().seq;
  const ask = (asks.batches += 1);
  try {
    const out = await reviewApi.batches({ include_withdrawn: true });
    if (!live(seq) || ask !== asks.batches) return;
    setR({ batches: (out && out.batches) || [], batchesError: '', batchesLoadedAt: Date.now() });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.batches) return;
    setR({ batchesError: msg(e) });
  }
}

export async function loadMap() {
  const seq = get().seq;
  const ask = (asks.map += 1);
  setR({ mapLoading: true, mapError: '' });
  try {
    const out = await reviewApi.mapData(windowNow());
    if (!live(seq) || ask !== asks.map) return;
    setR({ map: out, mapLoading: false, mapLoadedAt: Date.now() });
  } catch (e) {
    if (!live(seq) || ask !== asks.map) return;
    setR(Object.assign({ mapLoading: false }, isStaleScope(e) ? {} : { mapError: msg(e) }));
  }
}

export async function loadProgress() {
  const seq = get().seq;
  const ask = (asks.progress += 1);
  const w = windowNow();
  setR({ progressLoading: true, progressError: '' });
  try {
    const out = await reviewApi.progress({ since: w.since, until: w.until, block: R().progressBlock || 10 });
    if (!live(seq) || ask !== asks.progress) return;
    setR({ progress: out, progressLoading: false, progressLoadedAt: Date.now() });
  } catch (e) {
    if (!live(seq) || ask !== asks.progress) return;
    setR(Object.assign({ progressLoading: false }, isStaleScope(e) ? {} : { progressError: msg(e) }));
  }
}

export function setProgressBlock(n) {
  if (BLOCKS.indexOf(n) < 0) return;
  setR({ progressBlock: n });
  loadProgress();
}

// ── drawing a batch ─────────────────────────────────────────────────────────────────────────────────────────
export function setBatchForm(patch) {
  setR((r) => ({ form: nextForm(r.form, patch), preview: null, error: '' }));
}

// "Next batch": the plan's next step with its strategy and size (another fair batch when no plan is read).
export function nextBatch() {
  const plan = R().plan;
  setBatchForm({ plan_step: (plan && plan.next_step) || 'extra' });
}

// What the batch would draw, without drawing it. Null when it could not be asked.
export async function previewBatch() {
  if (R().busy) return null;
  const seq = get().seq;
  const req = batchRequest(R().form, windowNow());
  setR({ busy: true, error: '', notice: '' });
  try {
    const out = await reviewApi.samplingPreview(req);
    if (!live(seq)) return null;
    setR({ busy: false, preview: Object.assign({}, out, { request: req }) });
    return out;
  } catch (e) {
    if (!live(seq)) return null;
    setR(Object.assign({ busy: false }, isStaleScope(e) ? {} : { error: msg(e) }));
    return null;
  }
}

// Draws what the preview showed: the previewed request (same period and seed) when there is one. true when drawn.
export async function createBatch() {
  const r = R();
  if (r.busy) return false;
  const seq = get().seq;
  const req = r.preview && r.preview.request ? r.preview.request : batchRequest(r.form, windowNow());
  setR({ busy: true, error: '', notice: '' });
  try {
    const out = await reviewApi.createBatch(req);
    if (!live(seq)) return false;
    setR({ busy: false, preview: null, notice: batchNotice(out && out.batch) });
    loadPlan();
    loadBatches();
    loadMap();
    return true;
  } catch (e) {
    if (!live(seq)) return false;
    setR(Object.assign({ busy: false }, isStaleScope(e) ? {} : { error: msg(e) }));
    return false;
  }
}

// Withdraw a batch with a reason; the plan and the batch list (and Progress, when it is open) are read again.
export async function withdrawBatch(batchId, reason) {
  const why = String(reason || '').trim();
  if (!why) { setR({ error: 'Say why the batch is withdrawn.' }); return false; }
  const seq = get().seq;
  try {
    await reviewApi.withdrawBatch(batchId, why);
    if (!live(seq)) return false;
    loadPlan();
    loadBatches();
    if (get().tab === 'progress') loadProgress();
    return true;
  } catch (e) {
    if (!isStaleScope(e) && live(seq)) setR({ error: msg(e) });
    return false;
  }
}

// ── the manifest ────────────────────────────────────────────────────────────────────────────────────────────
export async function loadManifest() {
  const seq = get().seq;
  const ask = (asks.manifest += 1);
  try {
    const out = await reviewApi.manifest();
    if (!live(seq) || ask !== asks.manifest) return;
    setR({ manifest: out, manifestOpen: true, manifestError: '' });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.manifest) return;
    setR({ manifestError: msg(e) });
  }
}

export function closeManifest() { setR({ manifestOpen: false }); }

// The manifest as a JSON file (every batch with its seed and each answer's reason).
export function downloadManifest() {
  const m = R().manifest;
  if (!m || typeof document === 'undefined' || typeof URL === 'undefined' || !URL.createObjectURL) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(m, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = 'review-batches-manifest.json';
  document.body.appendChild(a);
  a.click();
  a.remove();
  const t = setTimeout(() => URL.revokeObjectURL(url), 1000);
  if (t && t.unref) t.unref();
}

// ── the map ─────────────────────────────────────────────────────────────────────────────────────────────────
// A group is brought forward; choosing it again lets go.
export function focusCluster(id) { setR((r) => ({ mapFocus: r.mapFocus === id ? null : id })); }

// A map answer (mapView(...).nodes[i], or a raw map row) opens on Conversations.
export function openMapNode(node) {
  openInConversations(node.conversationKey || node.conversation_key, node.turnId || node.turn_id);
}
