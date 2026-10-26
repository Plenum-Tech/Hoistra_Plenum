// flag — Trace Lab's build switch. ON only in a build made with VITE_TRACE_LAB=1 (the frontend Dockerfiles'
// ARG, attachment A4). The guard is api/client.js's own: under node (the tests) import.meta.env is undefined,
// so ON is false there, and every flag-off path is what the tests see by default.
const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};

export const ON = env.VITE_TRACE_LAB === '1';

export function enabled() { return ON; }
