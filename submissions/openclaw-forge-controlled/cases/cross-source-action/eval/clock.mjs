// Freeze managed briefing tools only; never gateway timers.
const RealDate = Date;
const fixed = RealDate.parse('2026-09-24T12:00:00Z');
globalThis.Date = class extends RealDate {
  constructor(...args) { super(...(args.length ? args : [fixed])); }
  static now() { return fixed; }
};
