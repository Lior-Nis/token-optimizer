# Publishing @alexgreensh/token-optimizer (Claude Code cloud package)

Publish the npm package with every plugin release, from the release commit, after the version bump. The package version must equal `.claude-plugin/plugin.json`; `build.py` sets it, so never edit it by hand.

1. Build the exact tarball that will be published:

   ```
   python3 packaging/npm/build.py --pack
   ```

   It prints the filename, size and `integrity` (sha512) of `build/npm/alexgreensh-token-optimizer-<version>.tgz`.

2. Run the cloud tests: `python3 -m pytest tests/test_cloud_kit.py -q` (the end-to-end test installs that tarball into a fake VM home).

3. Publish that file, not the directory, so the registry's integrity equals the one printed above. Use a fresh granular access token with publish rights on `@alexgreensh/token-optimizer` only, in a throwaway npmrc, never in `~/.npmrc`:

   ```
   export NPM_CONFIG_USERCONFIG="$(mktemp)"
   printf '//registry.npmjs.org/:_authToken=%s\n' "$NPM_TOKEN" > "$NPM_CONFIG_USERCONFIG"
   npm publish build/npm/alexgreensh-token-optimizer-<version>.tgz --access public
   rm -f "$NPM_CONFIG_USERCONFIG"; unset NPM_CONFIG_USERCONFIG NPM_TOKEN
   ```

   First publish only: the `@alexgreensh` scope must belong to the publishing npm account (user or org named `alexgreensh`).

4. Verify what users will get:

   ```
   npm view @alexgreensh/token-optimizer@<version> dist.integrity   # must equal step 1
   python3 skills/token-optimizer/scripts/measure.py cloud-setup-script
   ```

   The second command reads the registry; it fails loudly if the version is not there.

5. Revoke the token. Update the setup script in any cloud environment you run, then start a session there and run `cloud-doctor` (see docs-site install/claude-code-cloud).

Never unpublish or republish a version: pinned setup scripts carry its sha512, so the bytes for a version must never change. Fix forward with a new version.
