# tmux-server.sh: the tmux server a project's team runs on, and the
# environment its workers must not inherit. Sourced by scripts/teamflow and
# pane.sh. It defines functions and constants only.
#
# Each project's team runs on a tmux server of its own, `tmux -L <socket>`,
# whose socket name comes from the team's session name (tf_socket_name). The
# launch record (<mailbox>/active) stores it as SOCKET. Pane IDs (%N) are
# numbered per server, so another project's windows can never reuse them.
# Teams started by teamflow 0.3 or older ran on the shared default server,
# and their records have no SOCKET line.

TEAMFLOW_LEGACY_SOCKET=default

# Claude Code session-identity variables. Any process started from a Claude
# Code session carries them, and a tmux server started there keeps them in
# its global environment. A worker CLI that inherits them starts as a child
# of that session: it takes the session's name and can reach its messaging
# socket. Workers start without them. The user's own settings stay: PATH,
# HOME, CLAUDE_CONFIG_DIR, ANTHROPIC_*, CLAUDE_IOS_CONFIG, and teamflow's
# AGENT_* variables. A pattern ends in * to match a prefix.
TEAMFLOW_SESSION_ENV="CLAUDECODE CLAUDE_CODE_* CLAUDE_PID CLAUDE_JOB_DIR CLAUDE_EFFORT AI_AGENT"

# tmux records the directory it started in as PWD in a new server's global
# environment, and OLDPWD comes along from the starting shell. Both name
# the Delegator's directory, so a new server drops them too.
TEAMFLOW_SERVER_ENV_EXTRA="PWD OLDPWD"

tf_socket_name() { # $1 session name -> the socket name of the team's tmux server
  local s
  s="$(printf '%s' "$1" | LC_ALL=C tr -c 'A-Za-z0-9_-' '-')"
  # A socket path has a length limit (104 bytes on macOS). Keep the end,
  # which holds the session's hash of the project path.
  [ "${#s}" -le 64 ] || s="${s:0:55}${s: -9}"
  printf '%s' "$s"
}

tf_session_var() { # $1 variable name -> exit 0 when workers must not inherit it
  local p
  local -a pats
  read -r -a pats <<EOF
$TEAMFLOW_SESSION_ENV
EOF
  for p in "${pats[@]}"; do
    # Unquoted in a case pattern, $p matches as a pattern, never as files.
    case "$1" in $p) return 0 ;; esac
  done
  return 1
}

tf_session_vars() { # NAME=value lines on stdin -> the session-identity names among them
  local line name
  while IFS= read -r line; do
    name="${line%%=*}"
    [ "$name" != "$line" ] || continue
    tf_session_var "$name" && printf '%s\n' "$name"
  done
  return 0
}

TF_TAB="$(printf '\t')"

# tf_worker_pane <socket> <session> <worker>
#   -> "<pane id> <dead 0|1> <panes in its window> <window name>"
# The pane of that worker in that session, on that server: the pane sits in
# the window named after the worker, or in the view window (teamflow view)
# with the worker's @teamflow_worker option. A pane whose @teamflow_worker
# names another worker never counts. Exits 1 when no pane fits, 2 when
# several do. Every row is checked by name: tmux falls back to other
# sessions or panes for some targets that do not exist, so the target alone
# proves nothing.
tf_worker_pane() {
  tmux -L "$1" list-panes -s -t "=$2:" \
    -F "#{session_name}$TF_TAB#{window_name}$TF_TAB#{@teamflow_worker}$TF_TAB#{pane_dead}$TF_TAB#{pane_id}$TF_TAB#{window_panes}" 2>/dev/null \
    | awk -F "$TF_TAB" -v s="$2" -v w="$3" '
        $1 == s && (($2 == w && ($3 == "" || $3 == w)) || ($2 == "view" && $3 == w)) { hit = $5 " " $4 " " $6 " " $2; n++ }
        END { if (n == 0) exit 1; if (n > 1) exit 2; print hit }'
}

tf_unset_session_env() { # a shell snippet that unsets the session-identity variables of its own environment
  # Worker windows run their command with /bin/sh (WORKER_SHELL in scripts/teamflow).
  local cases
  cases="$(printf '%s' "$TEAMFLOW_SESSION_ENV" | tr ' ' '|')"
  printf 'for v in $(env | cut -d= -f1); do case "$v" in %s) unset "$v" ;; esac; done;' "$cases"
}
