# Token Optimizer for Claude Code cloud sessions

This package is [Token Optimizer](https://github.com/alexgreensh/token-optimizer), the Claude Code plugin, packed for Claude Code cloud sessions (claude.ai/code). Cloud sessions don't load plugins, so this package carries the same hooks and scripts as the plugin, version for version.

You don't `npm install` it. Install it from your cloud environment's setup script:

1. On a machine with Token Optimizer installed, run `python3 measure.py cloud-setup-script` (or ask Claude: "print the Token Optimizer cloud setup script").
2. Paste the output into claude.ai/code > your environment > Setup script.

Each new session in that environment then runs the real Token Optimizer hooks, in any repo. The setup script downloads this tarball from registry.npmjs.org and checks its sha512 before running anything in it. It never fails your setup and prints nothing (log: `~/.cache/token-optimizer-cloud/install.log` in the VM).

Docs: https://alexgreensh.github.io/token-optimizer/install/claude-code-cloud/

License: PolyForm Noncommercial 1.0.0, plus the small-business grant in `LICENSE-SMALL-BUSINESS.md`.
