[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "{{name}}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["evlet>=0.1.0,<0.2"]

[tool.evlet]
app = true
title = "{{name}}"

[tool.setuptools.packages.find]
include = ["{{name}}*"]
