// Minimal store base: the prototype's logic class was written against a React-class-like
// API (state / setState / forceUpdate / lifecycle). This keeps that contract and lets React
// subscribe to changes via useHoistra().
export class Controller {
  constructor() { this.props = {}; this._subs = new Set(); }
  setState(patch, cb) {
    const p = typeof patch === 'function' ? patch(this.state) : patch;
    this.state = Object.assign({}, this.state, p);
    this._subs.forEach((fn) => fn());
    if (cb) cb();
  }
  forceUpdate() { this._subs.forEach((fn) => fn()); }
  subscribe(fn) { this._subs.add(fn); return () => this._subs.delete(fn); }
}
