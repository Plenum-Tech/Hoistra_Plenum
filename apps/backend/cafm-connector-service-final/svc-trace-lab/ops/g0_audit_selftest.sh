#!/bin/sh
# Hoist Trace Lab: self-test of the Golden Rule audit (ops/g0_audit.sh). Proves BOTH outcomes without
# touching the real repository:
#
#   sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit_selftest.sh
#   sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit.sh --self-test      (the same)
#
# It builds throwaway git repositories under a temporary directory (TMPDIR, else /tmp), runs the audit in each
# with a different change, and checks the exit code and the RESULT line:
#
#   * PASS (exit 0) when only an approved attachment, the instance picker's files, or the token-saving
#     session's files differ (the trimmed CLAUDE.md and the untracked docs folder);
#   * FAIL (exit 1) on any other modified file, a sibling of CLAUDE.md, and any change (modified or new)
#     under svc-deepagents, svc-udr or svc-operations-intelligence, also when the extra files are present;
#   * exit 2 outside a git repository (fail closed).
#
# Exit 0 when every outcome is as expected, 1 when one is not, 2 when it cannot run. Safety: every git call
# goes through tgit(), pinned to the temporary repository with --git-dir and --work-tree, every GIT_*
# variable of the caller is removed (a hook's GIT_DIR must not redirect it), git may not search above the
# temporary directory, and the only thing it ever deletes is that directory. G0_AUDIT=/path/to/script runs a
# different audit script (the tests use it to prove the self-test notices a broken audit).
set -u

HERE=$(cd "$(dirname "$0")" && pwd) || { echo "g0_audit_selftest: cannot find its own folder" >&2; exit 2; }
AUDIT=${G0_AUDIT:-$HERE/g0_audit.sh}
[ -f "$AUDIT" ] || { echo "g0_audit_selftest: no audit script at $AUDIT" >&2; exit 2; }
command -v git >/dev/null 2>&1 || { echo "g0_audit_selftest: git is not installed" >&2; exit 2; }

for name in $(env | sed -n 's/^\(GIT_[A-Za-z0-9_]*\)=.*/\1/p'); do unset "$name"; done

T=$(mktemp -d "${TMPDIR:-/tmp}/g0-selftest.XXXXXX") || { echo "g0_audit_selftest: cannot make a temporary directory" >&2; exit 2; }
[ -n "$T" ] && [ -d "$T" ] || { echo "g0_audit_selftest: no temporary directory" >&2; exit 2; }
trap 'rm -rf "$T"' EXIT
trap 'rm -rf "$T"; exit 2' HUP INT TERM
REPO="$T/repo"
PLAIN="$T/plain"
mkdir -p "$REPO" "$PLAIN" || exit 2

export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CEILING_DIRECTORIES="$T" HOME="$T" LC_ALL=C

tgit() {
  git --git-dir="$REPO/.git" --work-tree="$REPO" -c user.name=selftest -c user.email=selftest@invalid -c commit.gpgsign=false "$@"
}

BE=apps/backend/cafm-connector-service-final
APPROVED_FILE=apps/frontend/src/App.jsx
PICKER_FILE=apps/frontend/src/logic/instances.js
RULES_FILE=$BE/CLAUDE.md
SIBLING_FILE=$BE/README.md
OTHER_FILE=apps/frontend/src/other.js
SPEC_FILE=$BE/docs/architecture/cafm-platform-spec.md

put() {   # put <path> <text>: write a file inside the temporary repository
  mkdir -p "$(dirname "$REPO/$1")" && printf '%s\n' "$2" > "$REPO/$1"
}

grow() {  # grow <path>: add a line to a tracked file
  printf 'added\n' >> "$REPO/$1"
}

fresh() { # a new repository with one base commit
  rm -rf "$REPO" && mkdir -p "$REPO" && tgit init -q || exit 2
  for base_file in "$APPROVED_FILE" "$PICKER_FILE" "$RULES_FILE" "$SIBLING_FILE" "$OTHER_FILE" \
                   "$BE/svc-deepagents/src/a.py" "$BE/svc-udr/src/b.py" "$BE/svc-operations-intelligence/src/c.py"; do
    put "$base_file" "line 1"
  done
  tgit add -A && tgit commit -q -m base || exit 2
}

TOTAL=0
BAD=0

# check <label> <expected exit code> [text the output must contain]: run the audit in the temporary repository.
check() {
  label=$1 want=$2 needle=${3:-}
  TOTAL=$((TOTAL + 1))
  OUT=$(cd "$REPO" && sh "$AUDIT" 2>&1)
  got=$?
  case $want in 0) verdict=PASS ;; 1) verdict=FAIL ;; *) verdict="exit $want" ;; esac
  if [ "$got" != "$want" ]; then
    BAD=$((BAD + 1))
    echo "MISMATCH expect $verdict (exit $want), got exit $got: $label"
    printf '%s\n' "$OUT" | sed 's/^/    | /'
    return
  fi
  if [ "$want" = 0 ] && ! printf '%s\n' "$OUT" | grep -q '^RESULT: PASS'; then
    BAD=$((BAD + 1)); echo "MISMATCH expect PASS (exit 0), but no RESULT: PASS line: $label"; return
  fi
  if [ "$want" = 1 ] && ! printf '%s\n' "$OUT" | grep -q '^RESULT: FAIL'; then
    BAD=$((BAD + 1)); echo "MISMATCH expect FAIL (exit 1), but no RESULT: FAIL line: $label"; return
  fi
  if [ -n "$needle" ] && ! printf '%s\n' "$OUT" | grep -qF -- "$needle"; then
    BAD=$((BAD + 1)); echo "MISMATCH expect $verdict (exit $want) with \"$needle\" in the output: $label"
    printf '%s\n' "$OUT" | sed 's/^/    | /'
    return
  fi
  echo "ok   expect $verdict (exit $want): $label"
}

echo "== audit self-test: $AUDIT =="

fresh
check "a clean tree" 0

fresh; grow "$APPROVED_FILE"
check "an approved attachment changed ($APPROVED_FILE)" 0

fresh; grow "$PICKER_FILE"
check "the instance picker session's file edited ($PICKER_FILE)" 0 "modified: $PICKER_FILE"

fresh; grow "$RULES_FILE"; put "$SPEC_FILE" "the spec"
check "only the token-saving session's files differ (CLAUDE.md edited, docs/ untracked)" 0 "new (untracked): $SPEC_FILE"

fresh; grow "$RULES_FILE"; put "$SPEC_FILE" "the spec"; grow "$PICKER_FILE"; grow "$APPROVED_FILE"
check "every extra file together, with an approved change" 0

fresh; grow "$OTHER_FILE"
check "an unapproved file edited ($OTHER_FILE)" 1 "NOT APPROVED: $OTHER_FILE"

fresh; grow "$SIBLING_FILE"
check "a sibling of CLAUDE.md edited ($SIBLING_FILE)" 1 "NOT APPROVED: $SIBLING_FILE"

fresh; grow "$RULES_FILE"; put "$SPEC_FILE" "the spec"; grow "$OTHER_FILE"
check "an unapproved file edited next to the extra files (CLAUDE.md, docs/)" 1 "NOT APPROVED: $OTHER_FILE"

for svc in svc-deepagents svc-udr svc-operations-intelligence; do
  case $svc in svc-deepagents) svc_file=a.py ;; svc-udr) svc_file=b.py ;; *) svc_file=c.py ;; esac
  fresh; grow "$BE/$svc/src/$svc_file"
  check "a file edited under $svc" 1 "SERVICE CHANGED"
  fresh; put "$BE/$svc/src/stray.py" "x = 1"
  check "a new untracked file under $svc" 1 "SERVICE CHANGED"
done

fresh; grow "$RULES_FILE"; put "$SPEC_FILE" "the spec"; grow "$BE/svc-udr/src/b.py"
check "a service change next to the extra files (CLAUDE.md, docs/)" 1 "SERVICE CHANGED"

# Outside any repository: the audit must not guess.
TOTAL=$((TOTAL + 1))
OUT=$(cd "$PLAIN" && sh "$AUDIT" 2>&1)
got=$?
if [ "$got" = 2 ] && printf '%s\n' "$OUT" | grep -q 'not inside a git repository'; then
  echo "ok   expect exit 2 (fail closed): outside a git repository"
else
  BAD=$((BAD + 1)); echo "MISMATCH expect exit 2 (fail closed), got exit $got: outside a git repository"
fi

echo
if [ "$BAD" -eq 0 ]; then
  echo "SELF-TEST: PASS - $TOTAL of $TOTAL outcomes as expected"
  exit 0
fi
echo "SELF-TEST: FAIL - $BAD of $TOTAL outcomes not as expected"
exit 1
