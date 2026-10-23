---
name: local-vm-manager
description: Run isolated Linux browser captures, selected Playwright web tests, or existing Surfaces and JudgmentKit browser suites through the local VM manager, with bounded storage, renewable leases, preserved evidence, and verified guest deletion. Use by default for general web testing and browser verification. Reserve Safari for checks with a concrete Safari-specific or native Apple acceptance requirement.
---

Use the manager at `/Users/mike/Codex/local-vm-manager`. It supports Linux browser captures, selected npm Playwright specs, and the existing Node browser suites in Surfaces and JudgmentKit. Native macOS/iOS tests and general shell RPC are not implemented.

General web testing and browser verification are VM-first. An authorized request for web testing is sufficient to use the manager; Mike does not need to name a VM, Playwright, the manager, or this skill. Run routine layout, responsive behavior, theme, DOM, screenshot, interaction, and download checks through the supported Linux workflows. Linux VM work does not depend on host Safari MCP availability.

Reserve Safari MCP for a concrete Safari-specific or native Apple acceptance requirement, and name that requirement before using it. An older project preference for host Safari does not make a general web check Safari-specific. Preserve required engines, frozen assessments, and native workflows. If the manager does not support the requested workload, report the blocker rather than switching to host Safari, another host browser, or an ad hoc VM.

1. Check `vm_inventory` for an active job or cleanup blocker. The manager owns only VMs it creates in its private namespace. Never adopt, stop, prune, or delete an existing external VM.
2. Call `vm_start` with a descriptive label. Poll `vm_status` until `ready`. Call `vm_renew` at least every 30 seconds while working or provisioning. The default lease is two minutes; the hard job deadline is one hour. A live MCP server does not establish that an agent is still working.
3. Call `vm_capture` with an authorized HTTP(S) URL, or `pilot` for the deterministic browser fixture. This runs Chromium inside Linux, with no host mounts or host browser launch. Poll until the request's `result.json`, PNG, rendered HTML, and browser log appear in `exports`. Name the actual engine in the result. Linux Chromium/WebKit evidence cannot establish Safari or native Apple behavior.
4. Call `vm_finish`, including after failed checks. Poll for a terminal receipt. Success requires `vm_deleted`, `runtime_deleted`, `evidence_verified`, and the expected capture files; inspect the result's `passed` value too. A `cleanup_blocked` status is unfinished work. Preserve the error and resolve the owned cleanup before requesting another VM.
5. Return the job ID, useful evidence paths, and deletion result. Keep screenshots, logs, and source changes on the host outside iCloud. Never export browser profiles, credentials, toolchains, caches, or whole guest disks as routine evidence. Evidence overflow is an explicit failure, not permission to discard required results.

The supervisor handles owner-process death and expired leases. A launchd sweeper recovers interrupted supervisors. Do not rely on an agent's final instruction or `finally` block as the cleanup mechanism.

If MCP tools are unavailable in the current chat, the CLI is immediately usable:

```sh
/Users/mike/Codex/local-vm-manager/bin/lvm run --url pilot --label browser-pilot
```

The CLI owns its lease until completion and waits for deletion. Use `bin/lvm status JOB_ID` to inspect a receipt. Do not substitute direct Lima/Tart commands or launch a host browser. When macOS/iOS/Safari is required, report that this manager's native profile is not implemented and follow the project's separately authorized native workflow.

For an authorized run of a supported Playwright web suite, call `vm_run_playwright` with a canonical `source_dir`, explicit `tests/browser/*.spec.ts` paths, `expected_tests`, and a label. It returns while provisioning; renew every 30 seconds and poll until terminal. Do not call capture while this workload runs. The CLI manages renewals automatically:

```sh
/Users/mike/Codex/local-vm-manager/bin/lvm playwright \
  --source /absolute/local/web/repo \
  --test tests/browser/example.spec.ts \
  --expected-tests 1 --label web-regression
```

The runner freezes regular web source files and the npm lockfile, installs locked dependencies and matching Playwright Chromium inside Linux, builds the web package, then discovers and runs only the selected files with one worker and zero retries. Browser sandboxing remains enabled. Setup requires the pinned Linux Node archive (`.venv/bin/python scripts/bootstrap_node.py`) and guest network access to npm, Debian, and Playwright downloads. Inspect the project's `build` script first; this npm profile is for trusted web projects and does not implement native builds. Do not point it at untrusted code or a package whose build invokes native tests.

Success additionally requires `test_result.passed`, the exact expected test count, no skipped/flaky/unexpected tests, unchanged selected test hashes, and all lifecycle receipt flags. Evidence under `evidence/JOB_ID/playwright/` includes JSON/HTML reports, traces for every test, failure screenshots/video when produced, environment versions, progress, source hashes, and logs. An allowlisted archive is streamed out, checked by SHA-256, safely extracted, and every file is verified before guest deletion; source trees, node_modules, browser caches, and OS disks are excluded. Failed checks still retain diagnostics and clean up the owned guest. A new MCP tool requires reloading the connection; use the CLI in an existing chat if its tool list is stale.

For Surfaces and JudgmentKit's existing Node browser suites, use `vm_run_browser_suites(source_dir, profile, tests?, label)` or the CLI:

```sh
/Users/mike/Codex/local-vm-manager/bin/lvm browser-suites \
  --source /Users/mike/SurfacesPlatformV2 --profile surfaces
/Users/mike/Codex/local-vm-manager/bin/lvm browser-suites \
  --source /Users/mike/JudgmentKit2 --profile judgmentkit
```

Omitting `--test` runs the profile's maintained set. Use repeated `--test` for an explicit subset of the allowlisted entrypoints listed in `lvm/suites.py`. The Surfaces set includes its seven npm browser commands plus the documented retained-session and guidance browser checks. JudgmentKit includes its six browser test files. Other historical QA scripts are not implicitly selected. Arbitrary commands and native tests are rejected.

These profiles freeze unchanged source and lockfiles, install dependencies inside Linux, and run each original entrypoint sequentially. Surfaces uses its locked Playwright Chromium with sandboxing and trace instrumentation. JudgmentKit uses its existing configured-Chrome environment option with Debian ARM64 Chromium; record this observed browser version separately from the locked `@sparticuz/chromium` package. No host dependency tree or browser is used.

Inspect `playwright/suite-results.json` for every command's status, exit code, and source hash preservation, and `environment.json` for the engine. Logs, generated screenshots, component evidence, and available traces are exported under `suite-logs` and `suite-artifacts`. Surfaces retained-session checks receive only the exact historical Git objects needed by their existing lookup, with the original blob hash verified in Linux. Missing retained fixtures are `blocked`, never passing tests; preserve those diagnostics and do not fabricate data or weaken assertions. Overall success requires every selected suite to pass and the normal evidence/deletion receipt flags. A failed or blocked suite still cleans up its owned guest.
