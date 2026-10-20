"""Release publishes npm from CI with trusted publishing (5.13.52). Each planted
bug breaks that route; the listed tests must go red.

Shape: (name, [test node ids], file_relpath, [(old, new), ...]).
"""

WF = ".github/workflows/sign-release.yml"
T = "tests/test_check_npm_published.py"

MUTATIONS = [
    ("npm job loses its OIDC identity (publish would 404/401)",
     [T + "::test_release_publishes_npm_with_trusted_publishing_not_a_token"],
     WF,
     [("      id-token: write\n", "      id-token: none\n")]),
    ("npm job republishes a version that is already live",
     [T + "::test_npm_publish_is_idempotent_for_a_retried_release"],
     WF,
     [('          if npm view "@alexgreensh/token-optimizer@$ver" version >/dev/null 2>&1; then',
       '          if false; then')]),
]
