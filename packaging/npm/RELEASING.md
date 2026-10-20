# Publishing @alexgreensh/token-optimizer (Claude Code cloud package)

Publish the npm package with every plugin release, from the release commit, after the version bump. The package version must equal `.claude-plugin/plugin.json`; `build.py` sets it, so never edit it by hand.

1. Build the exact tarball that will be published:

   ```
   python3 packaging/npm/build.py --pack
   ```

   It prints the filename, size and `integrity` (sha512) of `build/npm/alexgreensh-token-optimizer-<version>.tgz`. It refuses while anything under `hooks/`, `skills/`, `.claude-plugin/`, `packaging/npm/` or the license files is uncommitted or untracked, and packs tracked files only, so the published bytes are exactly the release commit.

2. Run the cloud tests: `python3 -m pytest tests/test_cloud_kit.py -q` (the end-to-end test installs that tarball into a fake VM home).

3. Publish that file, not the directory, so the registry's integrity equals the one printed above. Use a fresh granular access token with publish rights on `@alexgreensh/token-optimizer` only, in a throwaway npmrc, never in `~/.npmrc`, from an npm account with two-factor authentication set to "auth and writes":

   ```
   export NPM_CONFIG_USERCONFIG="$(mktemp)"
   printf '//registry.npmjs.org/:_authToken=%s\n' "$NPM_TOKEN" > "$NPM_CONFIG_USERCONFIG"
   npm publish build/npm/alexgreensh-token-optimizer-<version>.tgz --access public
   rm -f "$NPM_CONFIG_USERCONFIG"; unset NPM_CONFIG_USERCONFIG NPM_TOKEN
   ```

   First publish only: the `@alexgreensh` scope must belong to the publishing npm account (user or org named `alexgreensh`).

   Provenance: `npm publish --provenance` only works from a supported CI runner with an OIDC identity (GitHub Actions with `permissions: id-token: write`); from a laptop npm refuses it. There is no publish workflow today, so local releases publish without provenance, and the protection is the 2FA account plus a single-package token revoked right after. Adding provenance means a release-only workflow that runs `npm publish <tgz> --provenance --access public` with an `NPM_TOKEN` secret; that is Alex's call (it puts a publish credential in the repo's secrets).

4. Gate: prove the version is on npm with the exact bytes from step 1. The release is not done until this passes:

   ```
   python3 scripts/check_npm_published.py --expect-integrity <integrity from step 1>
   python3 skills/token-optimizer/scripts/measure.py cloud-setup-script
   ```

   The first command is the same check as `npm view @alexgreensh/token-optimizer@<version> dist.integrity` and exits 1 when the version is missing or its sha512 differs. The second reads the registry too and fails loudly if the version is not there. Publish to npm BEFORE `gh release create`: the Sign release workflow runs the same gate as its last step (after the CHECKSUMS.sha256 upload, so the script installer is never held hostage) and goes red for a release whose npm package is missing.

5. Revoke the token. Update the setup script in any cloud environment you run, then start a session there and run `cloud-doctor` (see docs-site install/claude-code-cloud).

Never unpublish or republish a version: pinned setup scripts carry its sha512, so the bytes for a version must never change. Fix forward with a new version.

`cloud-setup-script --latest` prints a script that follows npm's `latest` tag. It checks each download against the sha512 the registry publishes for that tarball, which proves the bytes arrived intact, not who published them: it trusts whatever is published under the package name, so anyone who gets publish rights on the npm account can ship code into every environment using `--latest`. The pinned default does not have that exposure.
