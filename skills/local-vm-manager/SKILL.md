---
name: local-vm-manager
description: Run isolated Linux browser captures, selected Playwright web tests, existing Surfaces and JudgmentKit browser suites, or the fixed reviewed Surfaces connected-observer workload through the local VM manager, with bounded storage, renewable leases, preserved evidence, and verified guest deletion. Use by default for general web testing and browser verification. Reserve Safari for checks with a concrete Safari-specific or native Apple acceptance requirement.
---

Use the manager from this repository checkout. Resolve the installed skill symlink to `<manager-root>/skills/local-vm-manager/SKILL.md`; `<manager-root>` is the checkout containing `bin/lvm`, `lvm/`, and `settings.example.json`. The MCP configuration also records that root as its `cwd`. Use the actual resolved checkout path for all commands below. It supports Linux browser captures, selected npm Playwright specs, and the existing Node browser suites in Surfaces and JudgmentKit. Native macOS/iOS tests and general shell RPC are not implemented.

General web testing and browser verification are VM-first. An authorized request for web testing is sufficient to use the manager; the user does not need to name a VM, Playwright, the manager, or this skill. Run routine layout, responsive behavior, theme, DOM, screenshot, interaction, and download checks through the supported Linux workflows. Linux VM work does not depend on host Safari MCP availability.

Reserve Safari MCP for a concrete Safari-specific or native Apple acceptance requirement, and name that requirement before using it. An older project preference for host Safari does not make a general web check Safari-specific. Preserve required engines, frozen assessments, and native workflows. If the manager does not support the requested workload, report the blocker rather than switching to host Safari, another host browser, or an ad hoc VM.

1. Check `vm_inventory`: require `implementation=resource-reservations-v2`, inspect `capacity` and `held_jobs`, and let the manager admit each job. A missing marker means a stale client; reconnect or use the canonical CLI. Independent jobs may overlap within the configured CPU, RAM, guest-count, and storage reservations. A cleanup-blocked job keeps its reservation and evidence; it does not block unrelated jobs when safe capacity remains. Unknown or unaccounted runtime state fails closed. Never adopt, stop, prune, or delete an existing external VM.
2. Call `vm_start` with a descriptive label. Poll `vm_status` until `ready`. Call `vm_renew` at least every 30 seconds while working or provisioning. The default lease is two minutes; the hard job deadline is one hour. A live MCP server does not establish that an agent is still working.
3. Call `vm_capture` with an authorized HTTP(S) URL, or `pilot` for the deterministic browser fixture. This runs Chromium inside Linux, with no host mounts or host browser launch. Poll until the request's `result.json`, PNG, rendered HTML, and browser log appear in `exports`. Name the actual engine in the result. Linux Chromium/WebKit evidence cannot establish Safari or native Apple behavior.
4. Call `vm_finish`, including after failed checks. Poll for a terminal receipt. Success requires `vm_deleted`, `runtime_deleted`, `evidence_complete`, `evidence_verified`, and the expected capture files; inspect the result's `passed` value too. A `cleanup_blocked` status is unfinished work. Preserve the error and resolve that job’s owned cleanup. Unrelated jobs may proceed only when the manager admits them within its remaining reservations; never delete or discount a blocked job to create capacity.
5. Return the job ID, useful evidence paths, and deletion result. Keep screenshots, logs, and source changes on the host outside iCloud. Never export browser profiles, credentials, toolchains, caches, or whole guest disks as routine evidence. Evidence overflow is an explicit failure, not permission to discard required results.

The supervisor handles owner-process death and expired leases. A launchd sweeper recovers interrupted supervisors. Do not rely on an agent's final instruction or `finally` block as the cleanup mechanism.

Prerequisites are an Apple Silicon Mac, Python 3.12+, and the completed bootstrap and registration steps in the repository README. If MCP tools are unavailable in the current chat after installation, use the CLI. Set `LVM_ROOT` to the actual checkout path discovered above:

```sh
LVM_ROOT="/absolute/path/to/local-vm-manager-skill"
"$LVM_ROOT/bin/lvm" run --url pilot --label browser-pilot
```

This repository is the canonical source for code and skill. `installation.json` may select an existing local data root; receipts and evidence stay there. Do not edit a historical installation copy.

The CLI owns its lease until completion and waits for deletion. Use `"$LVM_ROOT/bin/lvm" status JOB_ID` to inspect a receipt. Do not substitute direct Lima/Tart commands or launch a host browser. When macOS/iOS/Safari is required, report that this manager's native profile is not implemented and follow the project's separately authorized native workflow.

For an authorized run of a supported Playwright web suite, call `vm_run_playwright` with a canonical `source_dir`, explicit `tests/browser/*.spec.ts` paths, `expected_tests`, and a label. It returns while provisioning; renew every 30 seconds and poll until terminal. Do not call capture while this workload runs. The CLI manages renewals automatically:

```sh
"$LVM_ROOT/bin/lvm" playwright \
  --source /absolute/local/web/repo \
  --test tests/browser/example.spec.ts \
  --expected-tests 1 --label web-regression
```

The runner freezes regular web source files and the npm lockfile, installs locked dependencies and matching Playwright Chromium inside Linux, builds the web package, then discovers and runs only the selected files with one worker and zero retries. Browser sandboxing remains enabled. Setup requires the pinned Linux Node archive (`.venv/bin/python scripts/bootstrap_node.py`) and guest network access to npm, Debian, and Playwright downloads. Inspect the project's `build` script first; this npm profile is for trusted web projects and does not implement native builds. Do not point it at untrusted code or a package whose build invokes native tests.

Success additionally requires `test_result.passed`, the exact expected test count, no skipped/flaky/unexpected tests, unchanged selected test hashes, and all lifecycle receipt flags. Evidence under `evidence/JOB_ID/playwright/` includes JSON/HTML reports, traces for every test, failure screenshots/video when produced, environment versions, progress, source hashes, and logs. An allowlisted archive is streamed out, checked by SHA-256, safely extracted, and every file is verified before guest deletion; source trees, node_modules, browser caches, and OS disks are excluded. Failed checks still retain diagnostics and clean up the owned guest. A new MCP tool requires reloading the connection; use the CLI in an existing chat if its tool list is stale.

For Surfaces and JudgmentKit's existing Node browser suites, use `vm_run_browser_suites(source_dir, profile, tests?, label)` or the CLI:

```sh
"$LVM_ROOT/bin/lvm" browser-suites \
  --source /absolute/path/to/surfaces --profile surfaces
"$LVM_ROOT/bin/lvm" browser-suites \
  --source /absolute/path/to/judgmentkit --profile judgmentkit
```

Omitting `--test` runs the profile's maintained set. Use repeated `--test` for an explicit subset of the allowlisted entrypoints listed in `lvm/suites.py`. The Surfaces set includes its seven npm browser commands plus the documented retained-session and guidance browser checks. JudgmentKit includes its six browser test files. Other historical QA scripts are not implicitly selected. Arbitrary commands and native tests are rejected.

These profiles freeze unchanged source and lockfiles, install dependencies inside Linux, and run each original entrypoint sequentially. Surfaces uses its locked Playwright Chromium with sandboxing and trace instrumentation. JudgmentKit uses its existing configured-Chrome environment option with Debian ARM64 Chromium; record this observed browser version separately from the locked `@sparticuz/chromium` package. No host dependency tree or browser is used.

Inspect `playwright/suite-results.json` for every command's status, exit code, and source hash preservation, and `environment.json` for the engine. Logs, generated screenshots, component evidence, and available traces are exported under `suite-logs` and `suite-artifacts`. Surfaces retained-session checks receive only the exact historical Git objects needed by their existing lookup, with the original blob hash verified in Linux. Missing retained fixtures are `blocked`, never passing tests; preserve those diagnostics and do not fabricate data or weaken assertions. Overall success requires every selected suite to pass and the normal evidence/deletion receipt flags. A failed or blocked suite still cleans up its owned guest.

For the one reviewed Surfaces connected-observer fixture pair, use the fixed CLI profile below, or `vm_run_surfaces_connected_observer` after reloading the MCP connection:

```sh
"$LVM_ROOT/bin/lvm" surfaces-connected-observer \
  --label surfaces-connected-observer-v1
```

This profile accepts no source path, command, fixture, runtime, or budget override. It verifies the exact reviewed Surfaces launch and independent expectation, the pinned host Node/tsx loader, the fresh task state root, both declared fixture identities, and the workload deadline before dispatch. It is authorized only for the bound technical fixtures: never treat it as drawDB/Share, retained-data, deployment, publication, or owner-value acceptance. Preserve the external evidence manifest and require the terminal receipt to show `test_result.passed`, fixture count two, `vm_deleted=true`, `runtime_deleted=true`, `evidence_complete=true`, and `evidence_verified=true`.

The connected-observer adapter requires the local `adapters/surfaces-connected.json` binding under the selected data root. It is disabled on a fresh Mac until separately reviewed inputs are configured. It reserves both 20 GiB fixture backings plus the host floor; its external task evidence remains bounded at 64 MiB, with an additional 2 MiB manager allowance. It cannot override a stale binding or a blocked fixture.

Each ordinary job reserves its own guest resources, runtime growth, and evidence-export headroom before allocation. Normal completion, crashes, and cancellation release the reservation only after verified runtime deletion and evidence verification. Retained evidence still counts toward the storage budget. Never manually clear a reservation. The fixed Surfaces adapter reserves two 4-vCPU/5-GiB guests, two 20-GiB backing envelopes plus 2 GiB runtime assets, and its separate evidence allowances; its one reviewed state path is exclusive to that job.
