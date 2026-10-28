# VM Update Guide (Local CORRECT Installations)

All CORRECT VMs run this software locally. Use this procedure when pulling an updated `main` branch so local configuration is not overwritten.

## Quick, safe update

1. Back up your local configuration files:
   ```bash
   cp config.yaml config.yaml.local
   cp -r pacs-config pacs-config.local.bak 2>/dev/null || true
   ```

2. Stash any local working-tree changes:
   ```bash
   git stash push -u -m "vm-local-config"
   ```

3. Pull the latest code:
   ```bash
   git fetch origin
   git checkout main
   git pull --ff-only origin main
   ```

4. Restore only your config files (do not restore code changes):
   ```bash
   mv -f config.yaml.local config.yaml
   # Merge any new keys from pacs-config/config.pacs.yaml.example if needed.
   ```

5. Re-install dependencies if pyproject/setup changed:
   ```bash
   python3.11 -m venv .venv  # if needed
   source .venv/bin/activate
   pip install -e .
   ```

6. Restart the service:
   ```bash
   python -m netrt_core.main --config config.yaml
   ```

## Notes

- `config.yaml`, `config.pacs.yaml`, and `config.pacs-example.yaml` ARE tracked by git; always back them up before pulling.
- Quarantine directories are cleared automatically on application startup.
- If local edits conflict, resolve by keeping your config values while accepting upstream code changes.