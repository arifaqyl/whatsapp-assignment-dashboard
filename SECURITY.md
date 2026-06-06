# Security Policy

## Supported Versions

Security fixes apply to the current `main` branch and the latest release tag.

## Reporting a Vulnerability

Do not open a public issue for secrets, credentials, or exploitable bugs.

Use a private channel instead:

- GitHub private security advisory, if enabled
- Direct message to the repository owner

Include:

- affected file or feature
- exact impact
- reproduction steps
- any exposed secret material, if applicable

## Local Safety Rules

- Keep `config.py` untracked.
- Keep `storageState.json`, `*.db`, and logs out of git.
- Never commit API keys, bot tokens, or VLE credentials.
