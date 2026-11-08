[build-system]
requires = ["setuptools>=77.0.3"]
build-backend = "setuptools.build_meta"

[project]
name = "{{name}}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["evlet>=0.3.0,<0.4"]

[tool.evlet]
app = true
title = "{{title}}"

[tool.setuptools]
package-dir = {"{{name}}" = "."}
include-package-data = true
