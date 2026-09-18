# Working on pod-pi

- The host CLI is `pod_pi/cli.py`; `bin/pod-pi` loads it using only Python's standard library.
- Profiles are JSON defaults. Each instance receives a copy of `container/` and its own persistent home and workspace. Do not copy runtime state from another instance.
- Keep the default profile independent of GPU hardware, host usernames, private endpoints and model providers. Hardware and workload requirements belong in profiles or individual instance recipes.
- Pi's `home/.pi/agent/settings.json` is the live extension configuration. Preserve user settings, package filters and credentials when changing startup logic.
- Use subprocess argument arrays, preserve collision and ownership checks, and never replace active containers automatically. Building should not restart workloads.
- Never read or publish runtime credentials as part of documentation or examples.
- Validate lifecycle and configuration changes with `python3 -m unittest discover -s tests -v`. Use a fresh uniquely named instance for real container checks. Do not stop existing user workloads.
- When adapting one instance, inspect `<name>-pi path`, `instance.json` and `container/Containerfile`. Record dependencies in its recipe; explain that recreation discards changes outside home/workspace.
