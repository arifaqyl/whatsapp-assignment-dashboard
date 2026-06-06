# Contributing

## Scope

This repo is for a public-safe academic assistant that works with generic VLEs and WAHA-backed WhatsApp intake.

## Before You Open a PR

- Run the test suite.
- Keep secrets out of `config.py` and git history.
- Prefer small, focused changes.
- Update `README.md` if behavior or setup changes.

## Code Style

- Keep changes direct and readable.
- Prefer config-driven behavior over hardcoded values.
- Avoid adding UniKL-specific assumptions back into the public path.

## Checks

Recommended before PR:

```powershell
python -m unittest discover -s tests -v
python -m py_compile *.py
```

## Reporting Bugs

Open an issue with:

- what you expected
- what happened
- exact command or message used
- relevant logs, with secrets removed
