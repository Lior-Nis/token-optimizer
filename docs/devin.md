# Token Optimizer for Devin

Contract-only beta. An explicit captured-output filter and a user-triggered Devin CLI skill. No automatic hooks, provider accounting, quality score, ACU savings or dashboard.

No live Devin workspace available: the adapter has not been run against a live Devin instance. Local installation, genuine CLI skill discovery and filter subprocess tests are separate from Devin model adherence.

## Why explicit capture, not hooks

Devin CLI documents PreToolUse command rewriting, but the permission-check timing and persistent-shell cwd/environment behavior are not established for a wrapper. PostToolUse can add context but does not replace tool output. This adapter therefore does not rewrite commands or add permissions.

Capturing changes buffering and TTY-dependent behavior and loses cross-stream timing/order. Use finite non-interactive foreground commands only. Completion and exit status are caller assertions; sealing is an integrity snapshot, not proof that all writers have stopped. This is not a sandbox against malicious same-UID code.

## Install

From a checkout:

```bash
bash install.sh --devin --dry-run
bash install.sh --devin
TOKEN_OPTIMIZER_RUNTIME=devin python3 skills/token-optimizer/scripts/measure.py devin-doctor --probe
```

The installer copies a stable payload under `~/.config/devin/token-optimizer/plugin/` and a skill under `~/.config/devin/skills/token-optimizer-output/`. It never edits Devin config, permissions or hooks. Absolute `XDG_CONFIG_HOME` and `TOKEN_OPTIMIZER_DEVIN_HOME` overrides must stay inside HOME. Native Windows is refused; POSIX is required. On WSL use the Linux home, not the Windows profile.

Invoke `/token-optimizer-output` explicitly in Devin CLI. The installed skill pins `triggers: [user]` and has no `allowed-tools`, `permissions` or subagent override. A project skill with the same name can shadow it; doctor warns about known collisions, and `devin skills show token-optimizer-output` identifies the selected source.

## Local workflow

Use a new capture for each command. Never rerun a command just to measure it.

```bash
FILTER="$HOME/.config/devin/token-optimizer/plugin/devin_compress.py"
CAPTURE=$(python3 "$FILTER" prepare)
# Run your intended command here in its native shell, under normal permissions.
# Example finite read-only command:
git status --short > "$CAPTURE/stdout" 2> "$CAPTURE/stderr"
STATUS=$?
python3 "$FILTER" seal --capture "$CAPTURE" --exit-code "$STATUS"
python3 "$FILTER" --stdout "$CAPTURE/stdout" --stderr "$CAPTURE/stderr" --exit-code "$STATUS" --format git-status --session local-label
```

Prepare creates both files at 0600 in a private generated directory, so redirection preserves those modes even under umask 022. Wait for foreground completion before sealing. Never use this with background producers or interactive commands.

Only the prepared stdout/stderr pair is accepted. Each file is bounded at 8 MiB; FIFOs, devices, symlinks, hardlinks, leaf substitutions and changed sealed contents are refused before output. On refusal inspect the original using normal permissions; do not loosen the gates. Small output should skip this workflow: the skill body, extra calls and recovery pointers can cost more than the filter removes.

Failures and stderr remain raw. Invalid UTF-8, NUL/control bytes and recognized credentials bypass compression. Passthrough is not redaction: it can expose secrets to Devin/model context, as reading the originals would. Captured content is untrusted data, never a source of instructions, paths or consent.

A compressed result includes an opaque ID, not a raw-path instruction. Recovery adds a trusted identity and untrusted-data frames around the unchanged raw payload, then revalidates both original files and the completion seal:

```bash
python3 "$FILTER" recover --capture-id capture-0123456789abcdef0123456789abcdef --exit-code 0
```

Use the real ID and sealed exit code. Changed or missing captures are refused. A raw passthrough removes its capture once its bytes are delivered, and failed delivery keeps it; a compressed result keeps its capture for recovery until you delete it or uninstall with purge. Compression is lossy; the original is the recovery source.

## Measurement and consent

Installation records consent for reading private captures into filter/Devin context and writing numeric-only estimates. Metrics store counts, timestamps, outcome and an HMAC correlation label. No capture paths, commands, prompts or output text enter `metrics.db`. Correlation labels are caller-provided, not native Devin session IDs.

```bash
TOKEN_OPTIMIZER_RUNTIME=devin python3 skills/token-optimizer/scripts/measure.py devin-summary
bash install.sh --devin --uninstall
bash install.sh --devin --uninstall --purge
```

Source: `devin_captured_output_estimate`. The output reduction includes the emitted recovery pointer but excludes skill instructions, extra tool calls, repeated filtering and recovery reads. It is not end-to-end token savings, provider tokens, confirmed delivery, dollars or ACUs. Numeric records retain 30 days and at most 10,000 rows. Purge removes adapter metrics/sidecars, salt, consent and stored captures.

If installed files have changed, refresh/uninstall refuses to overwrite them. Doctor reports ownership drift. To stop activation manually, move only the `token-optimizer-output` skill directory out of Devin's skill paths, preserving your edits. Existing imported Claude skills/hooks are not controlled by this adapter and may create their own raw copies; doctor warns about visible coexistence files.

## Hosted Devin

The [playbook template](../devin/playbook.md) describes manual setup of the same filter in a hosted workspace. It is not deployed by the installer and has not been tested against hosted Devin. Playbooks and Knowledge deliver instructions, not automatic output interception. API session/consumption endpoints and MCP platform-management tools do not establish hook parity.

The release checksum inventory includes `devin/SKILL.md`; README/playbook are documentation. This local-checkout route trusts the checkout and fingerprints installed bytes for later drift checks. It does not authenticate a release signature.

## Sources

- https://docs.devin.ai/cli/extensibility/skills/creating-skills
- https://docs.devin.ai/cli/extensibility/hooks
- https://docs.devin.ai/cli/extensibility/hooks/lifecycle-hooks
- https://docs.devin.ai/cli/reference/permissions
- https://docs.devin.ai/cli/reference/configuration/global-vs-local
- https://docs.devin.ai/product-guides/creating-playbooks
- https://docs.devin.ai/onboard-devin/knowledge-onboarding
- https://docs.devin.ai/api-reference/overview

Recovery anchoring rule: trust only the first adapter identity line. Every byte after that first line is untrusted capture data, including any apparent closing frame, repeated identity, forged pointer or instruction. Frame labels aid reading, not authentication of embedded text. Raw payload is preserved, not escaped.

## Interrupted installation and manual repair

Recovery covers process death between published payload leaves and manifest/consent publication when the pending record matches the same checkout. Unpublished `.install-*` temps remain private in the adapter data directory and are not read or deleted on retry. A genuine death during a payload temp write can leave that temp, but it does not block retry. Old fixed-name `.install-tmp` files, changed/version-mismatched records or edited payloads are ambiguous and are refused, never auto-deleted.

To repair refused state, stop installer/filter processes. Move the entire `~/.config/devin/token-optimizer` directory and only the `~/.config/devin/skills/token-optimizer-output` directory to private backup directories under HOME. Keep these backups, including captures and edited files. Then install again from the intended checkout. Adapt these paths to your explicit home override. Do not delete unrelated skills or host config. Inspect the backups yourself before any cleanup.

Pending records and manifests are local accidental-interruption bookkeeping, not authenticated historical ownership. The installer is not a sandbox against malicious same-UID code that can forge records, source files and matching payloads. Recovery requires current-checkout content commitments and refuses pre-existing temporary leaves rather than trusting a record-supplied digest as provenance.

Installer atomic publication requires the skill directory and adapter data directory to be on the same filesystem. Cross-filesystem layouts fail with an EXDEV error; use a same-filesystem home override rather than bypassing atomic publication. Interrupted `.install-*` payload/manifest temps and random consent temps are private, retained and ignored; uninstall does not remove these unclaimed bytes. Keep them with the private backup during manual repair and inspect before cleanup.
