#!/bin/sh
# Hoist Trace Lab: the final Golden Rule (G0) audit (G0 addendum 1 and 3).
#
# READ-ONLY. It runs `git status` and `git diff` and nothing else: no add, commit, stash, checkout, reset,
# restore or clean, and `--no-optional-locks` keeps even `git status` from refreshing the index. Run it from
# anywhere inside the repository:
#
#   sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit.sh
#
# It prints every modified or deleted TRACKED file (staged or not, against HEAD) and fails (exit 1) when
#   * a modified tracked file is not one of the approved attachments A1 to A8 (addendum 3), or
#   * any tracked file is deleted or changes type (the approved changes only ever add lines), or
#   * anything under svc-deepagents, svc-udr or svc-operations-intelligence is modified, deleted, staged or
#     new (untracked), because Trace Lab never touches those services.
# Another session's uncommitted files are listed apart as "not Trace Lab's" and never counted: a fixed list of
# tracked files (FOREIGN below) and the untracked files of one new folder (FOREIGN_NEW). Untracked files
# elsewhere (Trace Lab's own folders, docker-compose.tracelab.yml, docs) are shown by `git status` above and
# are not a failure: G0 forbids changing existing files, not creating Trace Lab's own.
# Exit 2: not in a repository, or git could not answer (fail closed).
#
# To prove both outcomes (PASS with only those extra files, FAIL on any other unapproved file or on any change
# under the three services) without touching this repository, run the self-test, which builds throwaway git
# repositories and runs this script in each:
#
#   sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit.sh --self-test
#
# For each approved file that changed it then prints `git diff HEAD --numstat` (added, removed, path) beside
# what the addendum expects, so the counts can be compared by eye. Comments and a blank line make some counts
# a little higher than the addendum's approximate figures (A7, A8).
set -u

if [ "${1:-}" = "--self-test" ]; then
  exec sh "$(dirname "$0")/g0_audit_selftest.sh"
fi

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "g0_audit: not inside a git repository" >&2; exit 2; }
cd "$ROOT" || exit 2

G="git --no-optional-locks -c core.quotepath=off"
TAB=$(printf '\t')

# The approved attachments (the addendum's complete list of edits to existing files).
APPROVED='
apps/frontend/src/App.jsx
apps/frontend/src/logic/integrations.js
apps/frontend/src/components/shell/PlatformCostPage.jsx
apps/frontend/Dockerfile
deploy/allinone/Dockerfile
apps/frontend/infra/single-url/nginx.conf
deploy/allinone/nginx.conf
deploy/allinone/supervisord.conf
apps/frontend/vite.config.js
apps/frontend/src/logic/users.js
apps/frontend/src/screens/Users.jsx
'

# Another session's uncommitted work: not Trace Lab's, never touched by it. The first five are the
# instance picker's; CLAUDE.md is the token-saving session's trim of the backend rules file.
FOREIGN='
apps/frontend/src/logic/instances.js
apps/frontend/src/logic/superAdmin.js
apps/frontend/src/screens/InstancePicker.jsx
apps/frontend/test/instances.test.mjs
apps/frontend/test/superAdminLive.test.mjs
apps/backend/cafm-connector-service-final/CLAUDE.md
'

# Another session's NEW folder: the spec the trimmed CLAUDE.md points to. Its untracked files are listed
# apart. Only untracked ones: a tracked file edited in this folder is still a violation.
FOREIGN_NEW='
apps/backend/cafm-connector-service-final/docs
'

# The services Trace Lab imports and never edits.
SERVICES='
apps/backend/cafm-connector-service-final/svc-deepagents
apps/backend/cafm-connector-service-final/svc-udr
apps/backend/cafm-connector-service-final/svc-operations-intelligence
'

in_list() { printf '%s\n' "$2" | grep -Fxq -- "$1"; }

in_service() {
  while IFS= read -r svc; do
    [ -n "$svc" ] || continue
    case "$1" in "$svc"/*) return 0 ;; esac
  done <<EOF
$SERVICES
EOF
  return 1
}

expect_for() {
  case "$1" in
    apps/frontend/src/App.jsx) echo "A1: 2 added" ;;
    apps/frontend/src/logic/integrations.js) echo "A2: 3 added and 1 removed (2 new lines and 1 changed line)" ;;
    apps/frontend/src/components/shell/PlatformCostPage.jsx) echo "A3: 2 added" ;;
    apps/frontend/Dockerfile) echo "A4: 2 added" ;;
    deploy/allinone/Dockerfile) echo "A4 2 + A6 1: 3 added" ;;
    apps/frontend/infra/single-url/nginx.conf) echo "A5: 6 added" ;;
    deploy/allinone/nginx.conf) echo "A7: about 3 added" ;;
    deploy/allinone/supervisord.conf) echo "A8: about 12 added" ;;
    apps/frontend/vite.config.js) echo "A9: the Trace Lab page as a second build input" ;;
    apps/frontend/src/logic/users.js) echo "A10: 1 added (labUser on each live row)" ;;
    apps/frontend/src/screens/Users.jsx) echo "A11: 2 added (the Trace Lab access control)" ;;
    *) echo "?" ;;
  esac
}

PROBLEMS=0
TOUCHED=""
FOREIGN_SEEN=""

echo "== git status --short =="
$G status --short || { echo "g0_audit: git status failed" >&2; exit 2; }

CHANGES=$($G diff --name-status --no-renames HEAD) || { echo "g0_audit: git diff failed" >&2; exit 2; }

echo
echo "== modified or deleted tracked files =="
LISTED=0
while IFS=$TAB read -r code path; do
  [ -n "$path" ] || continue
  case "$code" in
    A) continue ;;          # added to the index: a new file, not a change to an existing one
    D) kind=deleted ;;
    M) kind=modified ;;
    *) kind="changed ($code)" ;;   # a type change (T), unmerged (U) and the rest: never a line-level edit
  esac
  if in_list "$path" "$FOREIGN"; then
    FOREIGN_SEEN="$FOREIGN_SEEN$kind: $path
"
    continue
  fi
  LISTED=1
  echo "$kind: $path"
  if in_service "$path"; then
    continue                # reported once, below, with the untracked files of the services
  fi
  case "$kind" in
    deleted)
      echo "  DELETED: $path (a tracked file is never deleted)"
      PROBLEMS=$((PROBLEMS + 1))
      ;;
    modified)
      if in_list "$path" "$APPROVED"; then
        TOUCHED="$TOUCHED$path
"
      else
        echo "  NOT APPROVED: $path"
        PROBLEMS=$((PROBLEMS + 1))
      fi
      ;;
    *)
      echo "  UNEXPECTED: $path (git reports $code; approved attachments are line-level additions)"
      PROBLEMS=$((PROBLEMS + 1))
      ;;
  esac
done <<EOF
$CHANGES
EOF
[ "$LISTED" = 1 ] || echo "(none)"

echo
echo "== not Trace Lab's (another session's files, listed apart; never counted) =="
APART=0
if [ -n "$FOREIGN_SEEN" ]; then printf '%s' "$FOREIGN_SEEN"; APART=1; fi
while IFS= read -r dir; do
  [ -n "$dir" ] || continue
  NEW_FILES=$($G ls-files --others --exclude-standard -- "$dir") || { echo "g0_audit: git ls-files failed" >&2; exit 2; }
  while IFS= read -r newfile; do
    [ -n "$newfile" ] || continue
    echo "new (untracked): $newfile"
    APART=1
  done <<EOF
$NEW_FILES
EOF
done <<EOF
$FOREIGN_NEW
EOF
[ "$APART" = 1 ] || echo "(none)"

echo
echo "== svc-deepagents, svc-udr, svc-operations-intelligence: nothing may change =="
SVC_PATHS=$(printf '%s\n' "$SERVICES" | sed '/^$/d')
# shellcheck disable=SC2086  # the three paths contain no spaces or glob characters
SVC_CHANGES=$($G status --porcelain -- $SVC_PATHS) || { echo "g0_audit: git status failed" >&2; exit 2; }
if [ -n "$SVC_CHANGES" ]; then
  while IFS= read -r line; do
    echo "  SERVICE CHANGED: $line"
    PROBLEMS=$((PROBLEMS + 1))
  done <<EOF
$SVC_CHANGES
EOF
else
  echo "(unchanged)"
fi

echo
echo "== numstat of the approved attachments that changed (added, removed, path) =="
SHOWN=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  in_list "$f" "$TOUCHED" || continue
  line=$($G diff HEAD --numstat -- "$f") || { echo "g0_audit: git diff failed" >&2; exit 2; }
  printf '%s    <- addendum: %s\n' "$line" "$(expect_for "$f")"
  SHOWN=1
done <<EOF
$APPROVED
EOF
[ "$SHOWN" = 1 ] || echo "(none changed)"

echo
if [ "$PROBLEMS" -eq 0 ]; then
  echo "RESULT: PASS - every changed existing file is an approved attachment, and the three services are untouched"
  exit 0
fi
echo "RESULT: FAIL - $PROBLEMS problem(s)"
exit 1
