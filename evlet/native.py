"""Build and serve trusted app-owned native panel modules."""

from pathlib import Path
import json
import re
import shutil
import subprocess


def build_assets(packages, stage, resources):
    assets, descriptors = {}, []
    for package in packages:
        root = package.source_root / 'views'
        files = [path for path in package.source_files() if path.is_relative_to(root) and path.suffix in ('.ts', '.js', '.tsx', '.jsx')]
        if not files: continue
        node = shutil.which('node')
        if node is None: raise ValueError('Building native extensions requires Node 24; install the supported toolchain')
        if any(path.suffix in ('.tsx', '.jsx') for path in files):
            raise ValueError('Native panel modules use .ts/.js with the host panel contract; JSX source needs a compatible prebuilt .js module')
        destination = stage / 'assets' / package.name
        inputs = []
        outputs = [str(path.relative_to(root).with_suffix('.mjs')).casefold() for path in files]
        if len(set(outputs)) != len(outputs):
            raise ValueError('Native modules with the same name and different extensions would overwrite each other')
        for path in files:
            relative = path.relative_to(root).with_suffix('.mjs')
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            inputs.append({'source': str(path), 'target': str(target), 'relative': str(relative)})
        script = r'''
import fs from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {pathToFileURL} from 'node:url';
const inputs = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
for (const input of inputs) {
  let source = fs.readFileSync(input.source, 'utf8');
  if (input.source.endsWith('.ts')) source = stripTypeScriptTypes(source, {mode: 'transform'});
  source = source.replace(/((?:from\s*|import\s*\(|import\s*)['"])([^'"]+)(['"])/g, (_, before, specifier, after) => {
    if (!specifier.startsWith('./') && !specifier.startsWith('../')) throw new Error('Native module imports must be relative');
    if (!/\.(ts|js|mjs)$/.test(specifier)) throw new Error('Native imports require an explicit .ts/.js extension');
    return before + specifier.replace(/\.(ts|js)$/, '.mjs') + after;
  });
  if (/sourceMappingURL|sourceURL/.test(source)) throw new Error('Native source maps are not published');
  fs.writeFileSync(input.target, source);
}
const result = [];
for (const input of inputs) {
  const module = await import(pathToFileURL(input.target));
  if (module.extension) {
    const {name, target, override = false} = module.extension;
    if (typeof name !== 'string' || typeof target !== 'string' || typeof override !== 'boolean' || typeof module.extension.render !== 'function') throw new Error('Invalid native panel extension');
    result.push({name, target, override, file: input.relative});
  }
}
process.stdout.write(JSON.stringify(result));
'''
        request = stage / ('native-' + package.name + '.json')
        request.write_text(json.dumps(inputs))
        result = subprocess.run([node, '--input-type=module', '-e', script, str(request)], capture_output=True, text=True, timeout=120)
        request.unlink()
        if result.returncode: raise ValueError('Native extension build failed: ' + result.stderr.strip())
        for descriptor in json.loads(result.stdout):
            if descriptor['target'] not in resources: raise ValueError('Unknown native extension target: ' + descriptor['target'])
            if not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', descriptor['name']): raise ValueError('Use a stable native panel name')
            existing = next((item for item in descriptors if (item['target'], item['name']) == (descriptor['target'], descriptor['name'])), None)
            if existing and not descriptor['override']: raise ValueError('Native extension collision requires override=true')
            if existing: descriptors.remove(existing)
            descriptor['app'] = package.name
            descriptors.append(descriptor)
        assets[package.name] = [item['relative'] for item in inputs]
    return {'panels': descriptors, 'files': assets}


def serve(business, path, user):
    prefix = '/assets/apps/'
    parts = path.removeprefix(prefix).split('/')
    if len(parts) < 3 or not getattr(business, 'build_manifest', None): return None
    fingerprint, app, *relative = parts
    manifest = business.build_manifest
    if fingerprint != manifest['fingerprint'] or '/'.join(relative) not in manifest.get('native', {}).get('files', {}).get(app, []): return None
    permitted = False
    for panel in manifest['native']['panels']:
        if panel['app'] != app: continue
        if can_access(business, panel['target'], user): permitted = True
    if not permitted: return None
    content = (business.build_directory / 'assets' / app / Path(*relative)).read_bytes()
    from .business import Download
    return 200, Download(content), [(b'content-type', b'text/javascript; charset=utf-8')]


def can_access(business, target, user):
    model = business.registry.resources.get(target)
    report = business.forms.reports.get(target)
    if model: return business.policy.allows(user, model, 'read')
    if report: return user.role in report.roles
    for (owner, name), dialog in business.forms.dialogs.items():
        resource = getattr(owner, '__resource_id__', business.registry.name_for(owner)) + '.' + name
        if resource == target:
            capability, operation = dialog.permission or (owner, 'update')
            return business.policy.allows(user, owner, 'update') and business.policy.allows(user, business.registry.resolve(capability), operation)
    return False
