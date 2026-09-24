#!/usr/bin/env bash
# Fail if any denylisted string appears in the repository.
#
# The denylist itself is never committed: it is read from the first fenced
# block under the CLAUDE.local.md heading that mentions this script, or from
# a plain one-per-line file named by $SANITIZE_DENYLIST_FILE (useful in CI,
# where the list can come from a secret). Matching is case-insensitive and
# literal.
#
# Matches are reported as "pattern #N" plus a location, never the pattern
# text, so a failing CI log does not leak the list it is protecting.
#
# Usage:
#   scripts/check_sanitized.sh                 # working tree, index, history, refs
#   scripts/check_sanitized.sh --message FILE  # a commit message (commit-msg hook)
set -euo pipefail

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

load_denylist() {
    if [[ -n "${SANITIZE_DENYLIST_FILE:-}" ]]; then
        cat -- "$SANITIZE_DENYLIST_FILE"
    elif [[ -f CLAUDE.local.md ]]; then
        awk '
            /^#+ / { under = index($0, "check_sanitized.sh") > 0; next }
            under && /^```/ { if (inside) exit; inside = 1; next }
            inside { print }
        ' CLAUDE.local.md
    fi | tr -d '\r' | sed -e 's/[[:space:]]*$//' -e '/^$/d'
}

mapfile -t patterns < <(load_denylist)
if (( ${#patterns[@]} == 0 )); then
    echo "check_sanitized: no denylist found (CLAUDE.local.md or \$SANITIZE_DENYLIST_FILE)." >&2
    echo "check_sanitized: refusing to pass without one." >&2
    exit 2
fi

found=0
report() {
    # $1 = pattern index, $2 = where; one line per hit.
    echo "  pattern #$1: $2" >&2
    found=1
}

if [[ "${1:-}" == "--message" ]]; then
    msg_file=${2:?--message needs a file}
    for i in "${!patterns[@]}"; do
        if grep -qiF -e "${patterns[$i]}" -- "$msg_file"; then
            report "$((i + 1))" "commit message"
        fi
    done
else
    has_commits=0
    git rev-parse -q --verify HEAD >/dev/null && has_commits=1

    for i in "${!patterns[@]}"; do
        p=${patterns[$i]}
        n=$((i + 1))

        # Working tree: tracked files plus untracked files that are not ignored.
        while IFS= read -r hit; do
            report "$n" "working tree $hit"
        done < <(git grep --untracked -I -n -i -F -e "$p" | cut -d: -f1,2 || true)

        # Index, which is what the next commit will actually contain.
        while IFS= read -r hit; do
            report "$n" "index $hit"
        done < <(git grep --cached -I -n -i -F -e "$p" | cut -d: -f1,2 || true)

        # File paths, which git grep does not search.
        while IFS= read -r hit; do
            report "$n" "path $hit"
        done < <(git ls-files --cached --others --exclude-standard | grep -iF -e "$p" || true)

        if (( has_commits )); then
            # Content added or removed anywhere in history, on every ref.
            while IFS= read -r hit; do
                report "$n" "history (diff) $hit"
            done < <(git log --all -i -S"$p" --format=%h || true)

            # Commit messages.
            while IFS= read -r hit; do
                report "$n" "history (message) $hit"
            done < <(git log --all -i -F --grep="$p" --format=%h || true)

            # Paths that ever existed.
            while IFS= read -r hit; do
                report "$n" "history (path) $hit"
            done < <(git log --all --name-only --format= | sort -u | grep -iF -e "$p" || true)
        fi

        # Branch and tag names.
        while IFS= read -r hit; do
            report "$n" "ref $hit"
        done < <(git for-each-ref --format='%(refname)' | grep -iF -e "$p" || true)
    done
fi

if (( found )); then
    echo "check_sanitized: denylisted strings found (see CLAUDE.local.md for the list)." >&2
    exit 1
fi
echo "check_sanitized: clean (${#patterns[@]} patterns)."
