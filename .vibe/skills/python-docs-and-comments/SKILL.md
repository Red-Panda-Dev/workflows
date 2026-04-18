---
name: python-docs-and-comments
description: Review and improve Python comments and docstrings for aiohttp backend code.
---

# Python Docs and Comments

Use this skill when the task is specifically about:

- adding missing docstrings
- improving handler contracts
- cleaning stale comments
- reviewing TODO/FIXME quality

Follow the canonical policy in `.skills/python-docs-and-comments.md`.

Apply these rules:

- keep comments high-signal and non-obvious
- prefer explaining intent, invariants, side effects, and risks
- avoid narrating obvious code
- use Google-style docstrings
- document only facts supported by the code
- do not invent route paths, status codes, auth requirements, or side effects
- add handler docstrings for aiohttp entrypoints
- update stale comments/docstrings in the same edit
