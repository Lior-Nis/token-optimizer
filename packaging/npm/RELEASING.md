# Publishing @alexgreensh/token-optimizer (Claude Code cloud package)

Publish the npm package with every plugin release, from the release commit, after the version bump. The package version must equal `.claude-plugin/plugin.json`; `build.py` sets it, so never edit it by hand.

1. Build the exact tarball that will be published:

   ```
   python3 packaging/npm/build.py --pack
   ```

   It prints the filename, size and `integrity` (sha512) of `build/npm/alexgreensh-token-optimizer-<version>.tgz`. It refuses while anything under `hooks/`, `skills/`, `.claude-plugin/`, `packaging/npm/` or the license files is uncommitted or untracked, and packs tracked files only, so the published bytes are exactly the release commit.

2. Run the cloud tests: `python3 -m pytest tests/test_cloud_kit.py -q` (the end-to-end test installs that tarball into a fake VM home).

3. Publish: automatic. Creating the GitHub release runs the `npm` job in `.github/workflows/sign-release.yml`, which builds this same tarball from the tag and publishes it with npm trusted publishing (GitHub OIDC, no token, provenance attached). npmjs.com trusts only that workflow file in the `npm-publish` environment, which only `v*` tags can deploy to. Nobody logs in or answers a 2FA prompt. A re-run skips a version that is already live.

   Manual fallback (only if the workflow is down): `npm login` and `npm publish build/npm/alexgreensh-token-optimizer-<version>.tgz --access public` in a real terminal.

4. Gate: prove the version is on npm with the exact bytes from step 1. The release is not done until this passes:

   ```
   python3 scripts/check_npm_published.py --expect-integrity <integrity from step 1>
   python3 skills/token-optimizer/scripts/measure.py cloud-setup-script
   ```

   The first command is the same check as `npm view @alexgreensh/token-optimizer@<version> dist.integrity` and exits 1 when the version is missing or its sha512 differs. The second reads the registry too and fails loudly if the version is not there. The Sign release workflow publishes, then runs the same gate as its last step (after the CHECKSUMS.sha256 upload, so the script installer is never held hostage) and goes red for a release whose npm package is missing.

5. Revoke the token. Update the setup script in any cloud environment you run, then start a session there and run `cloud-doctor` (see docs-site install/claude-code-cloud).

Never unpublish or republish a version: pinned setup scripts carry its sha512, so the bytes for a version must never change. Fix forward with a new version.

`cloud-setup-script --latest` prints a script that follows npm's `latest` tag. It checks each download against the sha512 the registry publishes for that tarball, which proves the bytes arrived intact, not who published them: it trusts whatever is published under the package name, so anyone who gets publish rights on the npm account can ship code into every environment using `--latest`. The pinned default does not have that exposure.
