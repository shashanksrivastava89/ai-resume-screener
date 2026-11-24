/** Vite's `?raw` suffix hands a file's TEXT to the importer.
 *
 *  Used by the tests that pin something jsdom cannot evaluate — a sticky header, a
 *  pane that must scroll rather than clip — where the guarantee lives in the CSS
 *  rule or the source itself. Declared here because this package has no @types/node,
 *  and reaching for `node:fs` in a browser test would be the wrong shape anyway. */
declare module "*?raw" {
  const source: string;
  export default source;
}
