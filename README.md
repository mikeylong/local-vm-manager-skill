# Local VM Manager

Run browser checks in small, disposable Linux VMs. Keep screenshots, logs, reports, and traces on the Mac, then verify that each guest and its runtime directory have been deleted. The repository includes the host controller, MCP server, CLI, installer, and [Codex skill](skills/local-vm-manager/SKILL.md).

General web testing is VM-first. Safari is reserved for a stated Safari-specific or native Apple acceptance requirement. Native macOS/iOS tests and existing VMs keep their existing workflows.

## Install on another Mac

Requirements: an **Apple Silicon Mac**, Python **3.12 or newer**, Git, and `uv`. Intel Macs are not supported by this release. Keep the checkout outside iCloud-synced Documents/Desktop folders. Bootstrap needs HTTPS access to GitHub, Debian cloud images, PyPI, and Node.js; browser jobs also need Debian/npm/Playwright downloads. Dependencies are downloaded locally and verified against pinned hashes.

```sh
mkdir -p "$HOME/Codex"
git clone https://github.com/mikeylong/local-vm-manager-skill.git \
  "$HOME/Codex/local-vm-manager-skill"
cd "$HOME/Codex/local-vm-manager-skill"
uv sync --locked
.venv/bin/python scripts/bootstrap.py
.venv/bin/python scripts/bootstrap_node.py
.venv/bin/python scripts/install.py --dry-run
.venv/bin/python scripts/install.py
```

The repository is private, so Git access requires authorization to `mikeylong/local-vm-manager-skill`. Follow your account-selection policy before GitHub operations. The chosen checkout path is used throughout; no particular username or home-directory path is required.

`bootstrap.py` installs pinned Lima and a sparse Debian ARM64 base. `bootstrap_node.py` downloads the Linux ARM64 Node archive for guest execution. They do not boot a VM. `settings.json` is created from the reviewed [defaults](settings.example.json) and stays untracked.

The installer registers the skill at `${CODEX_HOME:-$HOME/.codex}/skills/local-vm-manager`, adds the `local-vm-manager` MCP entry, and loads a user LaunchAgent that sweeps every 30 seconds and at login. Its service name is derived from the checkout path. All conflicts are checked before registration: an existing skill, MCP entry, or launch-agent file pointing elsewhere is preserved and reported. An identical installation can be repeated safely. `--codex-home` selects another Codex profile; `--launch-agent-dir` selects another plist directory. `--no-load` writes registrations without loading the recovery service and is intended for setup testing, not unattended browser jobs.

Restart or reload your Codex MCP connection after installation, then read the installed skill. CLI commands are available immediately. Run VM CLI invocations outside the macOS shell sandbox with approval for that invocation. Keep the checkout at its installed path; reinstall deliberately before relocating it. This publication does not replace any existing installation on the author's Mac.

## First browser check

From the installed checkout:

```sh
bin/lvm inventory
bin/lvm run --url pilot --label browser-pilot
```

The deterministic pilot exercises a browser form and exports a screenshot, rendered HTML, browser log, and result JSON. The CLI renews its own lease, finishes the job, and waits for verified deletion. An HTTP(S) URL is also supported:

```sh
bin/lvm run --url https://example.com --label page-check
bin/lvm status JOB_ID
```

A loopback URL refers to the guest, not the Mac. The manager provides page captures and supported test workloads, rather than a general interactive browser or guest shell API. Report unsupported workloads; do not silently switch to a host browser or ad hoc VM.

## Existing test suites

For a trusted npm web project with locked Playwright dependencies:

```sh
bin/lvm playwright --source /absolute/path/to/web-project \
  --test tests/browser/example.spec.ts --expected-tests 1 --label web-check
```

Inspect the project's web build command first. This profile accepts explicitly selected `tests/browser/*.spec.ts` files, preserves their hashes, runs one worker with zero retries, and rejects native specs. Match the expected count to the selected tests.

The maintained Node-suite profiles preserve the existing project entrypoints:

```sh
bin/lvm browser-suites --source /absolute/path/to/surfaces --profile surfaces
bin/lvm browser-suites --source /absolute/path/to/judgmentkit --profile judgmentkit
```

Repeated `--test` options select a subset of the allowlisted entrypoints in [lvm/suites.py](lvm/suites.py); omitting them runs the full maintained set. These profiles require the corresponding project and its fixtures/lockfile. The profile names do not install those applications. Custom connected-observer adapters under separate development are outside this baseline.

Surfaces uses its locked Playwright Chromium. JudgmentKit uses the existing configured-Chrome option with Debian ARM64 Chromium, whose observed version is recorded separately. Required engines and frozen acceptance requirements remain binding. Linux results do not establish Safari or native Apple behavior.

## MCP workflow

Use `vm_inventory`, then `vm_start` and `vm_capture` for page captures, or `vm_run_playwright` / `vm_run_browser_suites` for supported suites. MCP starts return while provisioning. Renew with `vm_renew` at least every 30 seconds and poll `vm_status`; status reads do not renew the lease. Call `vm_finish` for capture jobs after success or failure. Suite jobs export and clean up automatically.

The default lease is two minutes and the hard job deadline is one hour. The host supervisor handles owner death and lease expiry. The independent LaunchAgent recovers interrupted supervisors, with at most three automatic recovery attempts. Recovery after a real host reboot has not been exercised in the original pilot.

## Evidence and deletion

`evidence/JOB_ID/` retains only the allowlisted artifacts for that job. Depending on the workload, these include screenshots, rendered HTML, logs, JSON/HTML reports, traces, source hashes, and browser environment versions. Bundles and extracted files are checked by SHA-256 and size before runtime deletion. OS disks, browser profiles, credentials, dependency trees, and toolchains are excluded from routine evidence.

Before reporting completion, verify `vm_deleted=true`, `runtime_deleted=true`, `evidence_complete=true`, and `evidence_verified=true`. Report the test result separately. Failed tests still retain diagnostics and require cleanup. `cleanup_blocked` is unfinished work: preserve the diagnostics and resolve only manager-owned cleanup before another run.

The controller deletes only its own marked runtime namespace after identity and open-file checks. It never adopts, starts, stops, resizes, prunes, or deletes existing external VMs. It does not query the default Lima/Tart registries for lifecycle operations. Native macOS/iOS, Xcode, simulator, and device tests remain outside this implementation.

## Disk limits and Git hygiene

Defaults are 2 CPUs, 2 GiB RAM, a 12 GiB virtual guest disk, one active job, a 20 GiB allocated-storage budget for this checkout, a 10 GiB host free-space floor, and a 250 MiB evidence allowance per job. Admission reserves the guest disk and runtime checks monitor usage. These are monitored limits, not a filesystem hard quota. APFS shared extents can be counted more than once, so allocation is not exclusive reclaimable space.

Evidence accumulates and counts toward the budget. New runs are refused when space cannot be reserved; old evidence is never automatically pruned. One reusable Debian base and downloaded dependencies remain cached between runs. Guest runtime disks are deleted after evidence verification.

[.gitignore](.gitignore) excludes runtime/state, evidence, caches, downloaded tools and images, virtual environments, generated settings, verification output, archives, and credential filenames. Commit only reviewed source changes; ignore rules do not sanitize source code or authorize `git add -f`. Never add a guest disk or preserved evidence to Git.

## Verification

Unit tests launch no browser or VM:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_mcp.py
```

`check_mcp.py` verifies the actual stdio tool schemas, inventory, and rejection of native/foreign requests. It creates no VM. Its report and all other validation output remain ignored by Git.

After installation, a real pilot can verify the normal lifecycle:

```sh
bin/lvm run --url pilot --label normal-validation
.venv/bin/python scripts/verify_pilot.py normal JOB_ID
```

The repository also includes explicit crash checks. These create new manager-owned guests and signal only their own client/supervisor processes; run them deliberately after the recovery service is loaded:

```sh
.venv/bin/python scripts/verify_pilot.py crash
.venv/bin/python scripts/verify_reaper.py
```

Each check retains local evidence and verifies guest/runtime deletion. Runtime verification on the development Mac does not establish a test on a second physical Mac. Before changing installation or recovery behavior, run the relevant isolated installer tests and lifecycle checks.

## Current validation

The portable checkout passed 40 unit tests, a real stdio MCP check, and three Linux capture pilots: normal completion, agent-client SIGKILL, and supervisor SIGKILL recovered by its separate launch service. All three preserved verified evidence and deleted their guests and runtime directories. These checks ran on an Apple Silicon development Mac with a fresh Python environment and downloaded dependencies; a second physical Mac has not been tested. Raw receipts, evidence, and verification reports remain local and excluded from Git.
