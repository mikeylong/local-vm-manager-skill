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

The repository is public and can be cloned without authentication. Follow your account-selection policy before authenticated GitHub operations. The chosen checkout path is used throughout; no particular username or home-directory path is required.

`bootstrap.py` installs pinned Lima and a sparse Debian ARM64 base. `bootstrap_node.py` downloads the Linux ARM64 Node archive for guest execution. They do not boot a VM. `settings.json` is created from the reviewed [defaults](settings.example.json) and stays untracked.

The installer registers the skill at `${CODEX_HOME:-$HOME/.codex}/skills/local-vm-manager`, adds the `local-vm-manager` MCP entry, and loads a user LaunchAgent that sweeps every 30 seconds and at login. Its service name is derived from the checkout path. All conflicts are checked before registration: an existing skill, MCP entry, or launch-agent file pointing elsewhere is preserved and reported. An identical installation can be repeated safely. `--codex-home` selects another Codex profile; `--launch-agent-dir` selects another plist directory. `--no-load` writes registrations without loading the recovery service and is intended for setup testing, not unattended browser jobs.

Restart or reload your Codex MCP connection after installation, then read the installed skill. CLI commands are available immediately. Run VM CLI invocations outside the macOS shell sandbox with approval for that invocation. Keep the checkout at its installed path; reinstall deliberately before relocating it. The repository is the canonical source for manager code and the skill. Local runtime state and task bindings are configuration, never a second source tree to edit.

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

Repeated `--test` options select a subset of the allowlisted entrypoints in [lvm/suites.py](lvm/suites.py); omitting them runs the full maintained set. These profiles require the corresponding project and its fixtures/lockfile. The profile names do not install those applications. The fixed Surfaces connected-observer adapter is also included; see the local binding section below.

Surfaces uses its locked Playwright Chromium. JudgmentKit uses the existing configured-Chrome option with Debian ARM64 Chromium, whose observed version is recorded separately. Required engines and frozen acceptance requirements remain binding. Linux results do not establish Safari or native Apple behavior.

## Canonical source and an existing installation

Keep code changes in this repository and the installed skill linked to `skills/local-vm-manager`. An ignored `installation.json` can select an existing data root. The controller keeps its owner UUID, receipts, settings, tools, cache, and evidence there; it reads guest helpers and starts supervisors from the canonical checkout. `bin/lvm inventory` reports both paths. Do not move an ownership record or historical evidence to make an installation match a new checkout.

An explicit migration from the original layout is supported:

```sh
.venv/bin/python scripts/install.py --migrate-from /absolute/path/to/legacy-manager --dry-run
.venv/bin/python scripts/install.py --migrate-from /absolute/path/to/legacy-manager
```

The installer requires no active job, cleanup blocker, or remaining runtime. It holds the admission lock during registration, accepts only the exact supplied legacy CLI/MCP/skill paths, preserves unrelated Codex settings, saves registration backups under ignored `verification/`, and loads the new recovery service before retiring the recognized legacy service. Foreign registrations are rejected. Historical source remains as a backup; legacy CLI launchers and Python imports delegate to this checkout. `--wait-idle SECONDS` can hold admission while an existing job finishes naturally, without stopping its VM. Existing MCP processes need a connection reload or app restart; a changed config file does not replace code already in memory.

## Local Surfaces connected-observer binding

[lvm/surfaces_connected.py](lvm/surfaces_connected.py) preserves the adapter for the two reviewed technical fixtures, `npm-vite-dist` and `npm-vite-nested-output`:

```sh
bin/lvm surfaces-connected-observer --label surfaces-connected-observer
```

The matching MCP tool is `vm_run_surfaces_connected_observer(label)`. Neither interface accepts arbitrary commands, source directories, fixtures, budgets, or runtime names. A fresh installation leaves this profile disabled. It requires a separately reviewed `adapters/surfaces-connected.json` in the data root, containing schema version 1, the exact task/project/state/script/Node/tsx paths, fixed launch/expectation/Node sizes and SHA-256 pins, and the tsx tree digest. The current Mac's binding is retained locally and excluded from Git; installing on another Mac does not copy its task data or authorization.

Dispatch verifies the reviewed independent expectation, source pins, fresh owned state, and deadlines. The workload also seals the adapter configuration and canonical manager source hashes. Changed or expired bindings block dispatch. This migration does not renew a binding, fix the observer preparation blocker, or rerun those fixtures. Recovery authenticates and reuses the original external evidence manifest. An empty pre-pointer directory is removable only with recorded fresh-state intent, matching device/inode, private mode, no entries, and no open handles; that path uses `rmdir` only. Original failed results and incomplete fixture evidence remain failed/incomplete. A future authorized fixture run needs its own current reviewed binding.

This specialized profile reserves two 20 GiB fixture backings plus up to 2 GiB runtime assets within the shared resource policy. Task evidence is capped at 64 MiB, with a 2 MiB manager allowance; the bound task maintains its nonrefunded evidence accounting. Evidence manifests and identity/open-handle checks gate deletion of only the fresh marked runtime. Results are technical fixture evidence and do not establish product, native, Safari, or deployment acceptance.

## MCP workflow

Use `vm_inventory`, then `vm_start` and `vm_capture` for page captures, or `vm_run_playwright` / `vm_run_browser_suites` for supported suites. MCP starts return while provisioning. Renew with `vm_renew` at least every 30 seconds and poll `vm_status`; status reads do not renew the lease. Call `vm_finish` for capture jobs after success or failure. Suite jobs export and clean up automatically.

Require `implementation=resource-reservations-v2` in inventory before using an existing connection. Its `capacity` result shows held reservations and unaccounted state. Stale clients must reconnect or use the canonical CLI.

The default lease is two minutes and the hard job deadline is one hour. The host supervisor handles owner death and lease expiry. The independent LaunchAgent recovers interrupted supervisors, with at most three automatic recovery attempts. Recovery after a real host reboot has not been exercised in the original pilot.

## Evidence and deletion

`evidence/JOB_ID/` retains only the allowlisted artifacts for that job. Depending on the workload, these include screenshots, rendered HTML, logs, JSON/HTML reports, traces, source hashes, and browser environment versions. Bundles and extracted files are checked by SHA-256 and size before runtime deletion. OS disks, browser profiles, credentials, dependency trees, and toolchains are excluded from routine evidence.

Before reporting completion, verify `vm_deleted=true`, `runtime_deleted=true`, `evidence_complete=true`, and `evidence_verified=true`. Report the test result separately. Failed tests still retain diagnostics and require cleanup. `cleanup_blocked` is unfinished work: preserve the diagnostics and resolve that job’s owned cleanup. Other jobs can run when admission confirms safe remaining capacity; a failed job’s reservation cannot be ignored or cleared manually.

The controller deletes only its own marked runtime namespace after identity and open-file checks. It never adopts, starts, stops, resizes, prunes, or deletes existing external VMs. It does not query the default Lima/Tart registries for lifecycle operations. Native macOS/iOS, Xcode, simulator, and device tests remain outside this implementation.

## Disk limits and Git hygiene

Ordinary guests use 2 virtual CPUs, 2 GiB RAM, and a 12 GiB virtual disk. The default shared policy admits up to three independent jobs and four guests, capped at 12 virtual CPUs, 16 GiB guest RAM, and 80 GiB of committed storage. The installer adapts CPU/RAM limits to a smaller Mac; inventory reports the effective policy. The development Mac has 16 cores and 128 GiB RAM. Fresh admission also checks available-memory estimates and disk space, preserving a 16 GiB host-memory floor and 10 GiB disk floor on that Mac.

Reservations are persisted in each job record under the admission lock **before** creating runtime directories. They include runtime growth (16 GiB per ordinary job including the guest backing and runtime overhead), evidence export/extraction headroom, and a serialized 256 MiB source-staging allowance. Admission counts actual storage plus the unused portion of every active or stranded reservation. Existing evidence and reusable dependency/base-image storage remain in actual usage. Allocation figures can count shared APFS extents more than once; they are not exclusive reclaimable space or a filesystem quota.

A cleanup-blocked job remains visible and retains its CPU/RAM/guest/storage claim until its owned runtime is verified deleted. It does not impose a global stop on other jobs. A per-job storage overrun cancels that job; an actual host disk emergency can still stop writes. Unknown runtime directories, ownership mismatches, or conflicting external runtime claims fail admission closed. Jobs keep independent `LIMA_HOME`, inputs, leases, source identities, and evidence paths.

The fixed Surfaces profile counts as two guests, 8 virtual CPUs, and 10 GiB RAM, with a 42 GiB external runtime envelope and its evidence allowances. It exclusively reserves its reviewed state path, so two copies cannot share that path. The ordinary browser profile remains available alongside it when all shared limits fit.

Evidence accumulates and counts toward the budget. New runs are refused when space cannot be reserved; old evidence is never automatically pruned. Guest runtime disks are deleted only after exported evidence verification. Existing native workflows and external VMs remain outside this manager’s authority.

To upgrade an existing single-job installation without replacing its owner identity or evidence:

```sh
.venv/bin/python scripts/configure_resources.py --dry-run
.venv/bin/python scripts/configure_resources.py
.venv/bin/python scripts/install.py
```

Stop active manager jobs before upgrading. A known cleanup-blocked job can remain accounted for; unknown storage cannot. The upgrade backs up the previous settings under the existing data root’s `state/resource-policy-backups/`. Reinstalling invokes the same migration and leaves an established resource policy unchanged.

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

The concurrent implementation passes 77 unit tests covering process races, bounded admission, unknown-state rejection, frozen guest dimensions, job-ID collision safety, partial allocation, owner/supervisor recovery, and exact Surfaces cleanup. A real MCP transport check reports `implementation=resource-reservations-v2` and all nine supported tools.

Real Linux checks overlap independent browser jobs, keep a healthy guest usable while another cleanup is blocked, admit new work when capacity remains, and recover agent and supervisor crashes. Every newly created test guest must finish with verified evidence and runtime deletion. The old Surfaces preparation failure remains failed with incomplete fixture evidence; only its authenticated cleanup is reconciled. Raw receipts and preservation reports stay local and excluded from Git. These checks ran on the development Apple Silicon Mac; a second physical Mac has not been tested.

For explicit concurrent lifecycle acceptance (creates new owned Linux guests):

```sh
.venv/bin/python scripts/verify_concurrency.py
```

The harness overlaps two independently owned browser workloads, intentionally holds an open handle to block one job’s cleanup, verifies the other still executes, admits new jobs with safe capacity remaining, and exercises agent/supervisor crash cleanup. `--reconcile-job JOB_ID` additionally permits cleanup-only recovery of that exact pre-existing blocked Surfaces job after fresh ownership/evidence checks; never pass an unrelated job. It does not rerun either Surfaces fixture. Reports stay under ignored `verification/`, with all job evidence retained in the configured data root.

## Recover an oversized failed export

A completed test run can produce more diagnostics than the default 250 MiB allowance. Keep the guest and its reservation until all required evidence is recovered. Do not rerun the tests, remove traces, or clear its reservation to make export fit.

After measuring the evidence and authorizing a cap for that one job, use the canonical CLI:

```sh
bin/lvm recover-export JOB_ID --evidence-mib 352
```

The command accepts only an already-started Playwright job in `cleanup_blocked` with no live supervisor. The cap must increase and may not exceed 512 MiB. Job ID, ownership, evidence-directory identity, and fresh shared capacity are checked before mutation. The evidence reservation grows atomically to at least three times the new allowance plus 16 MiB; an existing larger reservation is never reduced. Insufficient capacity refuses the operation without changing the job. The global default and other job allowances stay unchanged.

Recovery verifies the original guest helper hash and invokes only its sealing function under the approved cap. It preserves every original evidence path and byte, including repeated trace copies, then performs the normal bundle/file verification and owned-runtime cleanup. A failed test result remains failed even when cleanup succeeds. Inspect the terminal receipt's result and all evidence/deletion flags separately. This command creates no VM and does not rerun tests. Run it outside the macOS shell sandbox with approval for that invocation.
