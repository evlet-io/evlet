import { cp, mkdir, rm } from 'node:fs/promises';
const target = new URL('../../evlet/ui/static/', import.meta.url);
await mkdir(target, { recursive: true });
await rm(new URL('assets/', target), { recursive: true, force: true });
await cp(new URL('../dist/', import.meta.url), target, { recursive: true });
console.log('Installed browser assets in the Python package.');
