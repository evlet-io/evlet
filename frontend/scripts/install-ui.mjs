import { cp, mkdir, rm } from 'node:fs/promises';
const target = new URL('../../evlet/ui/static/', import.meta.url);
await mkdir(target, { recursive: true });
await rm(new URL('assets/', target), { recursive: true, force: true });
await cp(new URL('../dist/', import.meta.url), target, { recursive: true });
for (const licence of ['IBMPlexSans-OFL.txt', 'JetBrainsMono-OFL.txt'])
  await cp(new URL(`../fonts/${licence}`, import.meta.url), new URL(`assets/${licence}`, target));
console.log('Installed browser assets and font licences in the Python package.');
