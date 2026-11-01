// The runtime config the server puts in every page it serves, as a non-executable block:
//
//   <script id="evlet-config" type="application/json">{"frontend":"evlet.desk","base":"/app","api":"/api",...}</script>
//
// The router base, links and API calls read base and api from it, so one build runs at any
// mount path and on any host.

export interface RuntimeConfig {
  frontend: string;
  /** Where this frontend's model screens are mounted, such as /app. */
  base: string;
  /** Where the API is served, such as /api. */
  api: string;
  realm: string;
  host: string;
  version: string;
  /** Where Docs are served on this host, or null when they are off. */
  docs: string | null;
}

/** What the desk shell uses when a page has no block, such as the Vite development server. */
const FALLBACK: RuntimeConfig = { frontend: 'evlet.desk', base: '/app', api: '/api', realm: 'desk', host: '', version: '', docs: '/docs' };
const PATH = /^\/(?:[a-z0-9][a-z0-9_-]*)?$/;

export function readConfig(doc: Document = document): RuntimeConfig {
  let value: Partial<RuntimeConfig> = {};
  try { value = JSON.parse(doc.getElementById('evlet-config')?.textContent || '{}'); } catch { /* the fallback applies */ }
  const config = { ...FALLBACK, ...value };
  // Only one-segment paths are accepted, so the block cannot point the app at another origin.
  for (const key of ['base', 'api'] as const) if (!PATH.test(config[key])) config[key] = FALLBACK[key];
  if (config.docs !== null && !PATH.test(config.docs)) config.docs = FALLBACK.docs;
  return config;
}
