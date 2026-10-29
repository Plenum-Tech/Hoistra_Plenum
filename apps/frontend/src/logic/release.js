// release — notice when the server is serving a newer build than this tab is running.
//
// The assets are content-hashed, so an old chunk keeps loading forever: a tab opened before a
// deploy goes on running the old code with no error and no sign, and a bug reported against it is
// a bug in a build nobody is serving any more. index.html is served no-store so a reload always
// lands on the new build — this is what tells a long-lived tab that a reload is worth doing.
//
// How it knows: the image writes dist/version.json at build time with the same id baked into the
// bundle as VITE_BUILD_ID. Boot id and served id differ => a release happened under this tab.

export const BUILD_ID = String(import.meta.env?.VITE_BUILD_ID || '').trim();

const URL = '/version.json';
const EVERY_MS = 5 * 60 * 1000;

/** The build the server is serving now, or '' when it cannot be read. */
export async function servedBuild() {
  try {
    const r = await fetch(`${URL}?t=${Date.now()}`, { cache: 'no-store', credentials: 'omit' });
    if (!r.ok) return '';
    const body = await r.json();
    return String(body?.build || '').trim();
  } catch {
    return '';               // offline, or the file is not there: say nothing
  }
}

/**
 * Call `onNewRelease(served)` the first time the server reports a different build.
 *
 * Checks on a timer and whenever the tab is shown again, which is when a person who left it open
 * overnight comes back. Does nothing at all when there is no build id to compare — a dev server, or
 * an image built without the argument — so no banner can appear on a build that cannot be stale.
 *
 * Returns a function that stops the watching.
 */
export function watchForRelease(onNewRelease, { everyMs = EVERY_MS } = {}) {
  if (!BUILD_ID) return () => {};
  let stopped = false;

  const check = async () => {
    if (stopped || document.visibilityState === 'hidden') return;
    const served = await servedBuild();
    if (stopped || !served || served === BUILD_ID) return;
    stopped = true;          // tell them once; the banner stays until they reload
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onShown);
    onNewRelease(served);
  };

  const onShown = () => { if (document.visibilityState === 'visible') check(); };
  const timer = setInterval(check, everyMs);
  document.addEventListener('visibilitychange', onShown);

  return () => {
    stopped = true;
    clearInterval(timer);
    document.removeEventListener('visibilitychange', onShown);
  };
}

/**
 * Reload onto the newly served build, leaving nothing of the old one behind.
 *
 * A browser cannot be told from script to do what ctrl+shift+R does — location.reload(true) has
 * been ignored for years — so this does the parts that actually matter and that a plain reload can
 * miss: any Cache Storage entries and any service worker, both of which outlive a reload and can
 * keep serving the old page. index.html and version.json are served no-store, so once those are
 * gone the reload fetches the new document and, through it, the new hashed assets.
 *
 * A cache-busting query parameter was tried here and dropped: the app writes the address bar itself
 * while it boots, so the parameter either survived in the URL or the navigation was swallowed. With
 * both entry documents served no-store it bought nothing anyway.
 */
export async function hardReload() {
  try {
    if (typeof caches !== 'undefined' && caches?.keys) {
      const keys = await caches.keys();
      await Promise.all(keys.map((k) => caches.delete(k)));
    }
  } catch { /* storage blocked, or a private window: the reload still helps */ }

  try {
    const sw = navigator.serviceWorker;
    if (sw?.getRegistrations) {
      const regs = await sw.getRegistrations();
      await Promise.all(regs.map((r) => r.unregister()));
    }
  } catch { /* nothing registered, or not permitted here */ }

  window.location.reload();
}
