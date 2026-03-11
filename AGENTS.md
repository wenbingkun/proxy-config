# AGENTS.md

## Project purpose
This repository maintains shared proxy configuration assets for:
- Quantumult X
- Clash / Mihomo

## Architecture rules
- Shared reusable rules go under `rules/`
- Quantumult X specific files go under `quantumultx/`
- Clash / Mihomo specific files go under `clash/`
- Do not put QX MitM private materials (passphrase, p12, certificates) into Git
- Do not commit secrets, tokens, subscription URLs, or credentials directly

## Migration goals
- Prefer shared rule assets where practical
- Keep client-specific syntax in each client folder
- Preserve behavior as much as possible during refactoring
- Make minimal safe changes first

## Output expectations
- Explain file migrations clearly
- Prefer incremental edits
- Preserve comments when useful
- Add placeholders for sensitive content instead of real values