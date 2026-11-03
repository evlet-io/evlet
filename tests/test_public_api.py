"""Pins the app-facing API (docs/api-stability.md) so a change to it is always deliberate.

Regenerate the snapshot after an intended change, then add a CHANGELOG entry:

    .venv/bin/python tests/test_public_api.py
"""

from pathlib import Path
import inspect
import json
import re
import sys

import evlet
import evlet.fields
import evlet.ui

SNAPSHOT = Path(__file__).parent / "fixtures" / "public-api.json"
REGENERATE = ".venv/bin/python tests/test_public_api.py"

# The evlet.ui names the app docs (docs/ui.md, docs/customizing.md) and the Playground use.
UI_NAMES = ("Bind", "Button", "Card", "Column", "ComponentDefinition", "CustomComponent", "Field", "FormLayout",
            "FormView", "Grid", "ListView", "Navigate", "PageView", "Select", "Text", "TextInput", "ViewRegistry",
            "When")

# Methods and decorators apps call on exported names, beyond their constructors.
MEMBERS = {"Model": ("to_dict", "validate"), "Child": ("to_dict", "validate"), "validate": ("confirm", "cancel"),
           "action": ("confirm", "cancel")}


def signature(value):
    if inspect.isclass(value):
        if issubclass(value, BaseException):
            return "exception"
        # Fields wrap __init__ in a __new__ that records arguments, so read __init__ itself.
        if value.__init__ is object.__init__:
            return f"class{inspect.signature(value)}"
        found = inspect.signature(value.__init__)
        return f"class{found.replace(parameters=tuple(found.parameters.values())[1:])}"
    if callable(value):
        return f"def{inspect.signature(value)}"
    if hasattr(value, "keys"):
        return f"{type(value).__name__}{sorted(value)}"
    return type(value).__name__


def normalise(text):
    # Sentinel defaults print their memory address.
    return re.sub(r" at 0x[0-9a-f]+", "", text)


def current():
    # Every field forwards its keywords to Field, which is not exported itself.
    api = {"evlet": {}, "evlet.fields": {"Field": normalise(signature(evlet.fields.Field))}, "evlet.ui": {}}
    for name in sorted(evlet.__all__):
        api["evlet"][name] = normalise(signature(getattr(evlet, name)))
    for name in UI_NAMES:
        api["evlet.ui"][name] = normalise(signature(getattr(evlet.ui, name)))
    for owner, names in MEMBERS.items():
        for name in names:
            api["evlet"][f"{owner}.{name}"] = normalise(signature(getattr(getattr(evlet, owner), name)))
    return api


def test_public_api_matches_snapshot():
    expected = json.loads(SNAPSHOT.read_text())
    actual = current()
    lines = []
    for module in ("evlet", "evlet.fields", "evlet.ui"):
        before, after = expected.get(module, {}), actual[module]
        for name in sorted(before.keys() - after.keys()):
            lines.append(f"  removed {module}.{name}")
        for name in sorted(after.keys() - before.keys()):
            lines.append(f"  added   {module}.{name}: {after[name]}")
        for name in sorted(before.keys() & after.keys()):
            if before[name] != after[name]:
                lines.append(f"  changed {module}.{name}:\n    was {before[name]}\n    now {after[name]}")
    assert not lines, (
        "The public API changed:\n" + "\n".join(lines) + "\n\n"
        "Apps depend on these names (docs/api-stability.md). A removed or changed name or argument must keep "
        "working with a DeprecationWarning for at least one minor release before it goes. If the change is "
        f"intended, regenerate the snapshot with `{REGENERATE}` and add a CHANGELOG.md entry that says what "
        "apps must change.")


def test_exports_resolve_and_are_unique():
    assert len(evlet.__all__) == len(set(evlet.__all__))
    assert [name for name in evlet.__all__ if not hasattr(evlet, name)] == []


def test_snapshot_failure_explains_the_policy(monkeypatch):
    monkeypatch.setattr(evlet, "__all__", [name for name in evlet.__all__ if name != "Model"])
    try:
        test_public_api_matches_snapshot()
    except AssertionError as error:
        message = str(error)
    else:
        raise AssertionError("Removing an export must fail the public API test")
    assert "removed evlet.Model" in message
    assert "docs/api-stability.md" in message and "DeprecationWarning" in message and REGENERATE in message


if __name__ == "__main__":
    SNAPSHOT.write_text(json.dumps(current(), indent=2, sort_keys=True) + "\n")
    print(f"Wrote {SNAPSHOT.relative_to(Path.cwd()) if SNAPSHOT.is_relative_to(Path.cwd()) else SNAPSHOT}", file=sys.stderr)
