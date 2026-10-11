---
title: Devin Output Filter - Plan
type: feat
date: 2026-10-12
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---
# Devin Output Filter - Plan

## Goal Capsule
- Objective: Devin users can reduce repetitive command output while keeping the original and seeing honest local estimates.
- Means: explicit captured-output filtering through a user-invoked Devin CLI skill with triggers: [user], not hooks (KTD1).
- Authority: one draft PR; no merge, paid sessions or hosted deployment. Separate user approval permitted free CLI installation and free agent signup, without quota-consuming calls before entitlement verification.

---
## Product Contract
### Summary
Add a documented Devin CLI skill, a local output filter, private numeric measurement, installation and doctor commands. Supply a hosted-Devin playbook template for manual use of the same filter, without deployment claims.
### Problem Frame
Automatic exec rewriting cannot be proven safe against Devin's persistent-shell cwd/environment and undocumented permission timing. Hooks also expose no provider token counters or billing totals. Explicit filtering avoids changing command execution or host policies.
### Requirements
- R1. Never execute or rewrite the original command and never add auto-approval permissions.
- R2. Read only bounded owner-only regular stdout/stderr files inside adapter-prepared private capture directories, leaving them byte-for-byte unchanged.
- R3. Preserve captured failures, stderr and invalid UTF-8 as raw output; return the supplied original exit status.
- R4. Emit compressed output only when a recovery pointer to the validated opaque recovery ID fits inside a net estimated reduction.
- R5. Persist only numeric estimates and privately keyed HMAC session labels after installation consent.
- R6. Install only an owned skill directory and private adapter payload; preserve every host config and foreign skill.
- R7. Label estimates as local output estimates, not provider usage, delivery confirmation, quality or ACU savings.
- R8. Verify malformed inputs, symlinks, special files, large files, failure paths, install cycles and dozens of isolated synthetic sessions.
- R9. State no live Devin instance/session was tested. Installed CLI skill discovery is separate from model adherence.
### Scope Boundaries
No lifecycle hooks, command execution, provider accounting, dashboard parity, smart compaction, native Windows, automatic hosted integration, MCP server or deployed knowledge. Existing imported Claude hooks remain unchanged and are diagnosed as a coexistence warning.
### Assumptions
Users/Devin create stdout and stderr capture files explicitly in their own shell under normal permissions. Original capture cleanup is theirs, not the filter's. The extra invocation is intentional.

---
## Planning Contract
### Key Technical Decisions
- KTD1. Deliver a user-triggered SKILL.md under the documented user skills directory, with no allowed-tools, permissions or subagent frontmatter. https://docs.devin.ai/cli/extensibility/skills/creating-skills. This avoids unknown rewrite ordering, permission checks and persistent-shell state.
- KTD2. Prepare a private capture directory with fixed stdout/stderr names, then seal only after foreground completion using both content hashes and the original status. Filtering rejects unsealed or changed captures. Filter two explicit absolute capture paths with a bounded original exit-code argument and a format hint from a fixed enum. No shell parsing or subprocess execution exists in the filter. Accept regular files only, owner UID, mode 0600, no symlink components; open with no-follow and compare file identity before/after reading.
- KTD3. Reuse bash_compress.compress and token_estimate. Format hints select compression patterns; they are not commands to execute. UTF-8 success without stderr may compress, all other bounded captures pass through.
- KTD4. Originals are caller-owned captures, not duplicated archives. Consent names possible confidential/personal contents, pointer lifetime and cleanup. Opaque recovery revalidates both files and seal, refusing changed or missing captures; the adapter never deletes captures. Numeric metrics retain 30 days and at most 10,000 rows in private SQLite.
- KTD5. Use split devin_compress.py, devin_state.py, devin_install.py and devin_doctor.py. Install a stable copied minimal dependency payload plus an ownership manifest; refuse symlink/foreign destinations and refuse uninstall if ownership or bytes changed. Respect absolute XDG_CONFIG_HOME only within HOME and a namespaced override with the same guard.
- KTD6. Keep measure dispatch out of foreign collectors and add Devin to the foreign-runtime guard. No generic shared dashboard or usage command is allowed under Devin.
### High-Level Technical Design
Data flow: host runs command normally -> private stdout/stderr captures and exit status -> explicit filter validation -> raw passthrough or shared compression plus recovery pointer -> numeric estimate record.
Lifecycle: absent -> consent and owned skill/payload -> idempotent refresh -> uninstall owned files only; purge deletes only adapter metrics and consent, never original captures.
Decision flow: unsafe input -> error and recovery guidance; bounded failed/stderr/binary -> raw; success but no net reduction -> raw; otherwise compressed output with original-file pointer.
### Risks
- Same-UID malicious producers are explicitly outside the guarantee. Sealing is an integrity snapshot, not an authenticated completion receipt; the caller asserts status and foreground completion. Retained writers can seal a prefix, which is accepted only until later mutation invalidates it.
- Numeric estimates exclude skill/tool/pointer orchestration overhead except the emitted recovery pointer; no end-to-end savings or per-provider-session aggregation is claimed. Correlation labels are caller-provided, not native Devin session IDs.
- Redirection changes buffering/TTY behavior and loses cross-stream order; only finite non-interactive foreground capture is supported. Seal asserts completed production, not independent observation.
- Credential-bearing text and UTF-8 control/binary payloads bypass compression; shared false expand-key messaging causes passthrough.
- Skills are instructions, not automatic enforcement. Devin's adherence and model-visible delivery remain unverified without a live session.
- Capture-file permissions and cleanup are explicit user responsibilities. The filter creates no second durable raw copy.
- Existing Claude imports may fire other adapters' hooks in Devin. Doctor warns, never edits imports or config.
- POSIX file permissions and no-follow are required; native Windows install is refused.
- Concurrent host edits to owned installed files stop refresh/uninstall, never overwrite them.

---
## Implementation Units
### U1. Filter and private estimate state
**Goal:** R1-R5 and R7.
**Files:** skills/token-optimizer/scripts/devin_compress.py, skills/token-optimizer/scripts/devin_state.py, tests/test_devin_compress.py, tests/test_devin_state.py, skills/token-optimizer/scripts/runtime_env.py.
**Dependencies:** none.
**Approach:** KTD2-KTD4; no command execution path.
**Execution note:** Observe failing contract tests before implementation.
**Test scenarios:**
- Success filters repeated text and preserves original bytes with a usable pointer.
- Nonzero exit, stderr and binary data pass through unchanged.
- Missing consent, malformed arguments, FIFOs, symlinks, shared permissions and oversized captures refuse with no partial output.
- Final pointer overhead defeats reduction and triggers raw passthrough.
- Concurrent numeric writes retain distinct hashed session keys and no prompt/command/output strings.
**Verification:** real local filter subprocess tests over dozens of synthetic captures.
### U2. Installer, doctor and CLI dispatch
**Goal:** R6 and R9.
**Files:** skills/token-optimizer/scripts/devin_install.py, skills/token-optimizer/scripts/devin_doctor.py, skills/token-optimizer/scripts/measure.py, install.sh, devin/SKILL.md, tests/test_devin_install.py.
**Dependencies:** U1.
**Approach:** KTD5-KTD6. Render stable installed Python/filter paths into the skill, without permission-grant fields.
**Test scenarios:**
- Dry run has no writes; foreign config/skills unchanged.
- Install/reinstall/uninstall preserves original captures and unrelated files.
- Destination symlink, changed owned file and foreign skill collision refuse.
- Installed filter works when checkout moved; doctor distinguishes local probe from live session.
- Official downloaded Devin CLI discovers the installed skill without authentication.
**Verification:** isolated HOME install lifecycle and real local CLI discovery.
### U3. Docs, mirrors and evidence
**Goal:** R7-R9.
**Files:** devin/README.md, devin/playbook.md, docs/devin.md, docs-site/src/content/docs/platforms/devin.mdx, docs-site/src/content/docs/install/devin.mdx, docs-site/src/content/docs/reference/configuration.mdx, docs-site/src/content/docs/reference/capability-matrix.mdx, docs-site/src/content/docs/reference/cli.mdx, docs-site/astro.config.mjs, README.md, CHANGELOG.md, generated mirrors.
**Dependencies:** U1, U2.
**Approach:** No parity promises; hosted template must be manually deployed and is not validated there.
**Test scenarios:** documented commands match dispatch, docs build succeeds, mirrors reproduce.
**Verification:** docs checks/build, visual preview, full Python suite and independent reviews.

---
## Verification Contract
Focused tests; at least 48 synthetic sessions; real install/refresh/remove; downloaded CLI version/help and skill discovery; full Python suite, safety regressions, docs integrity/build and mirror checks. Record exact manifests and counts. At least ten adversarial perspectives; a final independent batch is clean only with no retained actionable findings and all selected reviewers returned.

## Definition of Done
One draft PR with receipts, head SHA and CI state. No live Devin workspace available: the adapter has not been run against a live Devin instance. CLI installation/discovery, local subprocess filters and estimates are the provable boundary. Publishing credential revoked and secret files removed after push.

## Sources / Research
- https://docs.devin.ai/cli/extensibility/hooks and https://docs.devin.ai/cli/extensibility/hooks/lifecycle-hooks - rewrite contract, no result replacement, exec persistent shell input.
- https://docs.devin.ai/cli/reference/permissions and https://docs.devin.ai/cli/reference/configuration/global-vs-local - permission rules and all-source hooks, no proven rewrite-order contract.
- https://docs.devin.ai/cli/reference/configuration/read-config-from - Claude imports default enabled.
- https://docs.devin.ai/cli/extensibility/skills/creating-skills - supported explicit skill format, user trigger and no auto-approval fields.
- https://docs.devin.ai/work-with-devin/devin-cli - official installer.
- https://docs.devin.ai/api-reference/overview and https://docs.devin.ai/v3-openapi.json - hosted session/consumption APIs, not output interception.
- https://docs.devin.ai/product-guides/creating-playbooks and https://docs.devin.ai/onboard-devin/knowledge-onboarding - manual reusable instruction delivery.
- https://docs.devin.ai/onboard-devin/environment - VM snapshots, environment and secrets.
- https://docs.devin.ai/product-guides/automations and https://docs.devin.ai/work-with-devin/devin-mcp - webhook session triggers and platform management MCP.
