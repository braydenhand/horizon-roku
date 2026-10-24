# Notes for coding agents

Start with [README.md](README.md) (what Horizon is, setup) and [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) (architecture, Roku behaviour, gotchas, history). Short version:

- **Three parts:** the Roku app (`app/`, BrightScript/SceneGraph), the watcher on a Pi (`watcher.py`, `media.py`, `roku_ecp.py`), and the build/deploy scripts (`build.py`, `deploy_pi.py`).
- **Local config:** `config.json` (copy from `config.example.json`) holds the TV address and developer password. Never commit it.
- **Commands:** `python build.py` installs the app on the TV (restarting it). `python deploy_pi.py` updates the watcher. Debug with `tools/console.py` (TV console), `tools/listen.py` (TV events), `tools/ws_query.py`, and `ssh <pi> journalctl -u roku-watcher`.
- **Geometry** is defined once at the top of `build.py` and reaches the app through `data/apps.json`. The app requests glass for those rectangles from the watcher.

Rules that came from real bugs:

1. `tools/remote.py` presses buttons on a real TV. **Screenshot (`shot:`) and check focus before sending OK.** Unchecked sequences launched apps on the owner's TV twice.
2. If the launcher stops responding, it has crashed into the BrightScript debugger. Read `tools/console.py` before guessing.
3. Animations must target permanent nodes; don't target a node id that gets removed and recreated.
4. Free images on hidden or covered posters (`uri = ""`). The TV has about 100 MB of image memory, shared with Roku's own UI.
5. When a sheet closes, give focus back to `keyTrap` explicitly, and make the sheet ignore keys while hidden.
6. The Pi may run other critical services. The watcher must never need network or firewall changes; diff `pi/netstate.sh` output before and after anything that could touch them.
7. Look up every node you use in `init` (a missing `findNode` gives `invalid`, and assigning to it crashes).
