# Local VM Manager Skill

Run general web testing through disposable Linux VMs, preserve useful evidence, and verify guest deletion before reporting completion. Safari is reserved for checks with a concrete Safari-specific or native Apple acceptance requirement.

The installable skill is [`skills/local-vm-manager/SKILL.md`](skills/local-vm-manager/SKILL.md).

## Requirements

1. An existing local VM manager at `/Users/mike/Codex/local-vm-manager`, with its Python environment, pinned Lima/Debian dependencies, storage settings, and crash-recovery supervisor configured.
2. Its `local-vm-manager` MCP connection or `bin/lvm` CLI. VM CLI commands on macOS require approved execution outside the shell sandbox.
3. A supported browser workload. The skill covers HTTP(S) page captures, selected npm Playwright specs, and maintained Surfaces and JudgmentKit browser suites. General guest shell RPC and native macOS/iOS execution are outside its current capabilities.

This repository contains the skill instructions. The manager implementation and bootstrap installers remain a separate local dependency. Installing this skill alone does not install the VM runtime or MCP server. The current instructions use Mike's local project paths.

## Install the skill

After the manager is configured, clone this repository outside iCloud:

```sh
git clone https://github.com/mikeylong/local-vm-manager-skill.git \
  /Users/mike/Codex/local-vm-manager-skill
```

For a new Codex installation with no existing `local-vm-manager` skill:

```sh
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
ln -s /Users/mike/Codex/local-vm-manager-skill/skills/local-vm-manager \
  "${CODEX_HOME:-$HOME/.codex}/skills/local-vm-manager"
```

If that skill path already exists, keep it and compare the files before choosing an installation source. This publication leaves Mike's active skill link pointing to the manager's original skill folder. The repository copy is a snapshot; it does not automatically update that active installation.

## First run

Ask Codex to use the skill for an authorized browser check. The agent first checks manager inventory. A deterministic capture pilot is available through the CLI:

```sh
/Users/mike/Codex/local-vm-manager/bin/lvm run --url pilot --label browser-pilot
```

That command creates a new manager-owned Linux VM, exports its evidence, and waits for deletion. Use the workload-specific commands in `SKILL.md` for existing test suites. If a workload is unsupported, report the blocker instead of falling back to a host browser or an ad hoc VM.

## Evidence and completion

Evidence is retained under `/Users/mike/Codex/local-vm-manager/evidence/JOB_ID/`. Depending on the workload, it includes screenshots, rendered HTML, logs, JSON/HTML reports, traces, source hashes, and browser environment information. Whole OS disks, browser profiles, credentials, dependency trees, and toolchains are excluded.

Before reporting completion, verify `vm_deleted=true`, `runtime_deleted=true`, `evidence_complete=true`, and `evidence_verified=true`. Report the test result separately: successful cleanup does not turn a failed test into a pass. `cleanup_blocked` means work remains unfinished. Preserve diagnostics and resolve only the manager-owned cleanup before starting another job.

The manager owns only guests it creates in its private namespace. Existing external VMs, historical evidence, unrelated source work, and native test workflows remain protected. Storage admission can refuse new runs as retained evidence accumulates; evidence is not automatically pruned.

## Validate changes

Run Codex's skill validator against the skill folder:

```sh
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/local-vm-manager
```

Static validation checks the skill package. Runtime, crash recovery, and VM deletion require separate manager verification. This repository publication does not rerun those checks or create a VM.
