#!/bin/sh
# Rebuild the integration branch: upstream/main plus every feature branch
# listed in branches.txt, merged in order. rerere replays conflict fixes.
set -eu
cd "$(git -C "$(dirname "$0")" rev-parse --show-toplevel)/.."
git fetch -q upstream
git switch -q integration
git reset -q --hard upstream/main
grep -v '^\s*\(#\|$\)' notes/branches.txt | while read -r b; do
    echo "merge $b"
    git merge -q --no-ff --no-edit "$b" || {
        git diff --name-only --diff-filter=U | grep -q . && {
            echo "conflict in $b: fix, commit, then run this again" >&2
            exit 1
        }
        git commit -q --no-edit
    }
done
# Fixes for interactions between branches (not conflicts, so rerere has
# none): one patch each, applied in name order.
for f in notes/fixups/*.patch; do [ -e "$f" ] && git am -q "$f"; done
git log --oneline upstream/main..integration
