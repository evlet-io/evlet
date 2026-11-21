"""Model scripts: one JavaScript file per document, wired from the Python model (docs/model-scripts.md).

Meta.script names a .js file relative to the folder of the module that declares the model; it may go up with ..
as long as the file stays inside the app package. The browser loads it from /assets/scripts/<hash>/<path>, where
<path> is relative to the app package and the hash covers every .js file of the app, so one script may import any
other of the app by relative path (../trade.js). The server never runs it and never depends on it.
"""

from hashlib import sha256
from pathlib import Path, PurePosixPath
import sys

PREFIX = '/assets/scripts/'


def compile_scripts(registry):
    """({model: {'url', 'on_open'?}}, {hash: folder}) for the documents that declare Meta.script.

    Checked at start-up: the file exists and ends in .js; a script belongs to a document, not to a Table row (ledger
    models refuse Meta.script when defined, and a prompt's is checked then too, prompt_script); every hook (a field's on_change, tooltip=Js, look=Js and control, Meta.on_open and a
    custom layout block) is on a document with a script. Raises ValueError naming the model."""
    from ..fields import script_method
    roots, scripts, uses = {}, {}, {}
    for model in registry.models.values():
        meta = getattr(model, 'Meta', None)
        script, on_open = getattr(meta, 'script', None), getattr(meta, 'on_open', None)
        root = registry.root_of(model)
        if root is not model and (script is not None or on_open is not None):
            raise ValueError(f'{model.__name__}: a Table row uses the script of its document; declare Meta.script on {root.__name__}')
        customs = [f'{model.__name__}.Meta.layout ({block["name"]}, custom=)' for block in registry.layouts.get(model, {}).get('blocks', ())
                   if block['kind'] == 'custom']
        if root is not model and customs:
            raise ValueError(f'{customs[0]}: a custom block belongs to a document layout, not to a Table row')
        found = uses.setdefault(root, [])
        found += [f'{model.__name__}.{name} ({", ".join(field.hooks)})' for name, field in model.__app_fields__.items() if getattr(field, 'hooks', None)]
        found += customs
        if on_open is not None:
            found.append(f'{model.__name__}.Meta.on_open')
            script_method(on_open, f'{model.__name__}.Meta.on_open')
        if script is not None:
            folder, path = located(model, script)
            digest = fingerprint(folder)
            roots[digest] = folder
            scripts[model] = {'url': f'{PREFIX}{digest}/{path}', **({'on_open': on_open} if on_open else {})}
    for root, found in uses.items():
        if found and root not in scripts:
            raise ValueError(f'{root.__name__}: {found[0]} names a script method, so {root.__name__} needs Meta.script, '
                             'the .js file that holds it')
    return scripts, roots


def compile_extensions(registry, extensions):
    """{model: ((Extend, its schema entry), ...)} in the order given (app install order); checked at start-up: the
    model exists and is a document, each on_change names one of its fields ("field" or "table.field"), and the
    script is a file beside the extension. Raises ValueError naming the extension."""
    from ..declarations import Extend
    result = {}
    for extension in extensions:
        if not (isinstance(extension, type) and issubclass(extension, Extend)):
            raise ValueError(f'Register Extend classes in extensions=, not {extension!r}')
        meta = extension.Meta
        try:
            model = registry.resolve(meta.model)
        except (KeyError, ValueError):
            raise ValueError(f'{extension.__name__}.Meta.model: there is no model {meta.model!r}') from None
        if registry.root_of(model) is not model:
            raise ValueError(f'{extension.__name__}.Meta.model: {model.__name__} is a Table row; extend its document')
        for key in getattr(meta, 'on_change', {}):
            table, _, name = key.rpartition('.')
            owner = model.__tables__[table].child if table in model.__tables__ else model if not table else None
            if owner is None or name not in getattr(owner, '__app_fields__', {}):
                raise ValueError(f'{extension.__name__}.Meta.on_change: {model.__name__} has no field {key!r}')
        entry = {}
        if getattr(meta, 'script', None) is not None:
            folder, path = located(extension, meta.script)
            digest = fingerprint(folder)
            registry.script_roots[digest] = folder
            entry = {'url': f'{PREFIX}{digest}/{path}', **({'on_open': meta.on_open} if getattr(meta, 'on_open', None) else {}),
                     **({'on_change': dict(meta.on_change)} if getattr(meta, 'on_change', None) else {})}
        result.setdefault(model, []).append((extension, entry))
    return {model: tuple(items) for model, items in result.items()}


# Script folders of prompts, which are not in a registry: a prompt names its script when its class is defined. Its
# module's packages are imported by then, so its app root is known.
PROMPT_ROOTS = {}


def prompt_script(prompt, script, on_open):
    """A prompt's Meta.script as the open response carries it; raises ValueError naming the prompt and the path."""
    folder, path = located(prompt, script)
    digest = fingerprint(folder)
    PROMPT_ROOTS[digest] = folder
    return {'url': f'{PREFIX}{digest}/{path}', **({'on_open': on_open} if on_open else {})}


def app_root(owner):
    """The folder of the app package that declares `owner`: the outermost package of its module (an app's
    package.source_root), or the module's own folder when it is in no package."""
    module = sys.modules.get(owner.__module__)
    file = getattr(module, '__file__', None)
    if not file:
        return None
    folder = Path(file).resolve().parent
    parts = owner.__module__.split('.')
    for _ in range(max(len(parts) - (1 if hasattr(module, '__path__') else 2), 0)):
        folder = folder.parent
    return folder


def located(model, script):
    """(app folder, path in it) of a model's, prompt's or extension's Meta.script; raises ValueError naming the
    model and the path. The path is relative to the module's folder and may not leave the app package."""
    where = f'{model.__name__}.Meta.script'
    path = PurePosixPath(script) if type(script) is str else None
    if path is None or path.suffix != '.js' or path.is_absolute() or any(part == '' or part != '..' and part.startswith('.') for part in path.parts):
        raise ValueError(f'{where}: name a .js file relative to the folder of {model.__module__}, such as "quotation.js" '
                         f'or "../trade.js", not {script!r}')
    module = sys.modules.get(model.__module__)
    folder = Path(module.__file__).resolve().parent if getattr(module, '__file__', None) else None
    root = app_root(model)
    if folder is None or root is None:
        raise ValueError(f'{where}: {script} is not a file')
    target = (folder / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f'{where}: {script} leaves the app package {root}; a script lives inside its app')
    relative = target.relative_to(root)
    if any(part.startswith('.') for part in relative.parts):
        raise ValueError(f'{where}: {script} is in a hidden folder, which is not served')
    if not target.is_file():
        raise ValueError(f'{where}: {script} is not a file in {folder}')
    return root, relative.as_posix()


def fingerprint(folder):
    """A short hash of every .js file of the app folder and below (dot-folders skipped), so a changed script gets a new address."""
    digest = sha256()
    for file in sorted(folder.rglob('*.js')):
        relative = file.relative_to(folder)
        if not any(part.startswith('.') for part in relative.parts):
            digest.update(relative.as_posix().encode() + b'\0' + file.read_bytes() + b'\0')
    return digest.hexdigest()[:16]


def script_response(registry, path):
    """GET /assets/scripts/<hash>/<path>: a .js file of an app's script folder, or None (404). <path> is relative
    to the app package; .., dot-folders and links that leave the app are refused."""
    from .serving import TYPES, _file, page
    digest, _, rest = path.removeprefix(PREFIX).partition('/')
    folder = getattr(registry, 'script_roots', {}).get(digest) or PROMPT_ROOTS.get(digest)
    parts = PurePosixPath(rest).parts
    if folder is None or not rest.endswith('.js') or '\\' in rest or any(part == '..' or part.startswith('.') for part in parts):
        return None
    target = _file(folder, rest)
    return None if target is None else page(target.read_bytes(), TYPES['.js'])
