#!/usr/bin/env bash
# Install, update or remove the skills in this pack plus the recommended third-party
# skills listed in recommended-skills.txt.
#
#   ./install.sh                     -> interactive menus (install / update / uninstall / list)
#   ./install.sh --yes               -> install everything user-wide (~/.claude/skills), no questions
#   ./install.sh --project           -> install into ./.claude/skills (commit it to share with the team)
#   ./install.sh --local             -> install into ./.claude/skills, kept out of git
#   ./install.sh --agent claude,codex -> target agent(s): claude (default) | codex | agents (.agents/skills) | all
#                                       combine with --project / --local, e.g. --agent all --project
#   ./install.sh --dir <path>        -> install into any skills dir
#   ./install.sh <skill> [<skill>..] -> only the named skill(s)
#   ./install.sh --select            -> checkbox menu to choose skills
#   ./install.sh --no-recommended    -> only this pack's own skills
#   ./install.sh --only-recommended  -> only the third-party skills
#   ./install.sh --update            -> update every skill installed by this script, in every scope
#   ./install.sh --uninstall <skill>.. -> remove named skills everywhere (--uninstall --yes: everything, everywhere)
#   ./install.sh --list              -> show installed skills, scope and version
#   ./install.sh --package [<skill>..] -> build dist/<skill>.zip for upload to Claude.ai / the Claude API
# Remote one-liner (same flags after `bash -s --`):
#   curl -fsSL https://raw.githubusercontent.com/Dipeshpal/dipesh-skills/main/install.sh | bash
set -euo pipefail

REPO_URL="${DIPESH_SKILLS_REPO:-https://github.com/Dipeshpal/dipesh-skills.git}"
LOCK=".dipesh-skills.lock"   # per skills dir: name <TAB> source <TAB> commit <TAB> date
export GIT_TERMINAL_PROMPT=0   # fail fast instead of prompting for credentials
agent=claude      # comma list of claude | codex | agents (or all) -> ~/.<agent>/skills or ./.<agent>/skills
scope=user        # user | project | local
target=""
target_set=0
names=()
own=1
recommended=1
select=0
local_scope=0
action=install
interactive=0
yes=0
[[ $# -eq 0 ]] && interactive=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) scope=project; target_set=1; shift ;;
    --local) scope=local; local_scope=1; target_set=1; shift ;;
    --agent) agent="$2"; shift 2 ;;
    --codex) agent=codex; shift ;;
    --dir) target="${2/#\~/$HOME}"; target_set=1; shift 2 ;;
    --package) action=package; shift ;;
    --no-recommended) recommended=0; shift ;;
    --only-recommended) own=0; shift ;;
    --select) select=1; shift ;;
    --update) action=update; shift ;;
    --uninstall) action=uninstall; shift ;;
    --list) action=list; shift ;;
    -y|--yes) yes=1; shift ;;
    -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
    -*) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
    *) names+=("$1"); shift ;;
  esac
done

custom_dir=0; [[ -n "$target" ]] && custom_dir=1
[[ $agent == all ]] && agent=claude,codex,agents
IFS=, read -r -a agents <<< "$agent"
for a in "${agents[@]}"; do
  case $a in claude|codex|agents) ;; *) echo "unknown agent: $a (claude | codex | agents | all)" >&2; exit 1 ;; esac
done
skills_dir() {  # agent scope -> skills dir
  if [[ $2 == user ]]; then echo "${HOME}/.$1/skills"; else echo "$(pwd)/.$1/skills"; fi
}
set_targets() {  # one skills dir per chosen agent, unless --dir / custom gave one
  if [[ -n "$target" ]]; then targets=("$target"); return; fi
  targets=(); local a; for a in "${agents[@]}"; do targets+=("$(skills_dir "$a" "$scope")"); done
}
set_targets

# No terminal (CI, piped with no tty): use defaults instead of hanging on a menu.
if ! { : < /dev/tty; } 2>/dev/null; then
  interactive=0; select=0
fi

# ---- arrow-key menus (draw on and read from /dev/tty, so curl | bash works) ----

ESC_WAIT=0.05; (( BASH_VERSINFO[0] < 4 )) && ESC_WAIT=1   # bash 3.2 (macOS) only takes whole seconds
read_key() {  # prints: up | down | enter | space | <char>
  local k rest
  IFS= read -rsn1 k < /dev/tty
  if [[ $k == $'\e' ]]; then
    IFS= read -rsn2 -t "$ESC_WAIT" rest < /dev/tty || true
    case "$rest" in '[A') echo up ;; '[B') echo down ;; *) echo esc ;; esac
  elif [[ -z $k ]]; then echo enter
  elif [[ $k == ' ' ]]; then echo space
  else echo "$k"; fi
}

# menu_one "Title" opt1 opt2 ...  -> sets PICK to chosen index
menu_one() {
  local title=$1; shift
  local opts=("$@") n=$# cur=0 i key
  printf '%s\n' "$title" > /dev/tty
  printf '\e[?25l' > /dev/tty
  while true; do
    for i in "${!opts[@]}"; do
      if [[ $i -eq $cur ]]; then printf '\e[2K\e[36m❯ %d. %s\e[0m\n' $((i + 1)) "${opts[$i]}"
      else printf '\e[2K  %d. %s\n' $((i + 1)) "${opts[$i]}"; fi
    done > /dev/tty
    printf '\e[2K\e[2m  ↑/↓ move · Enter select\e[0m\n' > /dev/tty
    key=$(read_key)
    case "$key" in
      up) cur=$(( (cur - 1 + n) % n )) ;;
      down) cur=$(( (cur + 1) % n )) ;;
      enter) break ;;
      [1-9]) [[ $key -le $n ]] && cur=$((key - 1)) ;;
    esac
    printf '\e[%dA' $((n + 1)) > /dev/tty
  done
  printf '\e[?25h\n' > /dev/tty
  PICK=$cur
}

# menu_many "Title" opt1 opt2 ...  -> sets PICKS to chosen indexes (all pre-selected)
menu_many() {
  local title=$1; shift
  local opts=("$@") n=$# cur=0 i key all on=()
  for i in "${!opts[@]}"; do on[$i]=${MENU_DEFAULT:-1}; done
  if [[ -n "${MENU_ON:-}" ]]; then for i in "${!opts[@]}"; do on[$i]=0; done; for i in $MENU_ON; do on[$i]=1; done; fi
  printf '%s\n' "$title" > /dev/tty
  printf '\e[?25l' > /dev/tty
  while true; do
    for i in "${!opts[@]}"; do
      local box='[ ]'; [[ ${on[$i]} -eq 1 ]] && box='[x]'
      if [[ $i -eq $cur ]]; then printf '\e[2K\e[36m❯ %s %s\e[0m\n' "$box" "${opts[$i]}"
      else printf '\e[2K  %s %s\n' "$box" "${opts[$i]}"; fi
    done > /dev/tty
    printf '\e[2K\e[2m  ↑/↓ move · Space toggle · a all/none · Enter confirm\e[0m\n' > /dev/tty
    key=$(read_key)
    case "$key" in
      up) cur=$(( (cur - 1 + n) % n )) ;;
      down) cur=$(( (cur + 1) % n )) ;;
      space) on[$cur]=$(( 1 - on[cur] )) ;;
      a) all=1; for i in "${!opts[@]}"; do [[ ${on[$i]} -eq 1 ]] || all=0; done
         for i in "${!opts[@]}"; do on[$i]=$(( 1 - all )); done ;;
      enter) break ;;
    esac
    printf '\e[%dA' $((n + 1)) > /dev/tty
  done
  printf '\e[?25h\n' > /dev/tty
  PICKS=()
  for i in "${!opts[@]}"; do [[ ${on[$i]} -eq 1 ]] && PICKS+=("$i"); done
  return 0
}


# ---- sources ----
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"; { printf "\e[?25h" > /dev/tty; } 2>/dev/null || true' EXIT

# This pack: the local checkout if run from it, otherwise a fresh clone. Updates always
# try a fresh clone first, so an old local checkout can't "update" to stale files.
src="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
[[ -n "$src" && -d "$src/plugins" ]] || src=""
if [[ -z "$src" || $action == update ]]; then
  if git clone --depth 1 --quiet "$REPO_URL" "$work/self" 2>/dev/null; then
    src="$work/self"
  elif [[ -n "$src" ]]; then
    echo "warning: could not fetch $REPO_URL, updating from local checkout $src" >&2
  else
    echo "error: could not fetch $REPO_URL" >&2; exit 1
  fi
fi

# Version of one of this pack's skills: the repo commit, or a content hash when the
# checkout has no commit or uncommitted edits to that skill.
own_version() {
  local d; d="$(echo "$src"/plugins/*/skills/"$1")"
  if git -C "$src" rev-parse -q --verify HEAD >/dev/null 2>&1 && [[ -z "$(git -C "$src" status --porcelain -- "$d")" ]]; then
    git -C "$src" rev-parse --short HEAD
  else
    echo "h$( (cd "$d" && find . -type f -print0 | sort -z | xargs -0 sha1sum) | sha1sum | cut -c1-7)"
  fi
}

own_names=()
for d in "$src"/plugins/*/skills/*/; do own_names+=("$(basename "$d")"); done

rec_names=() rec_repos=() rec_paths=()
if [[ -f "$src/recommended-skills.txt" ]]; then
  while read -r name repo path; do
    [[ -z "$name" || "$name" == \#* ]] && continue
    rec_names+=("$name"); rec_repos+=("$repo"); rec_paths+=("${path:-.}")
  done < "$src/recommended-skills.txt"
fi

# Recommended Claude Code plugins (whole plugins, not skill folders): installed through the
# claude CLI when Claude Code is a target. <plugin@marketplace> <marketplace github repo>
rec_plugins=("ecc@ecc affaan-m/ECC")
claude_plugins() {  # install | update
  [[ $recommended -eq 1 && $custom_dir -eq 0 && " ${agents[*]} " == *" claude "* ]] || return 0
  local p id repo pscope=user
  [[ $scope == project ]] && pscope=project; [[ $local_scope -eq 1 ]] && pscope=local
  for p in "${rec_plugins[@]}"; do
    read -r id repo <<< "$p"
    wanted "${id%@*}" || continue
    if [[ $1 == install && ( $interactive -eq 1 || $select -eq 1 ) ]]; then
      menu_one "Also install the Claude Code plugin ${id%@*} ($repo: agents, skills, hooks, rules)?" "Yes" "No"
      [[ $PICK -eq 0 ]] || continue
    fi
    command -v claude >/dev/null || { echo "  -> Claude plugin ${id%@*}: run /plugin marketplace add $repo then /plugin install $id"; continue; }
    claude plugin marketplace add "$repo" >/dev/null 2>&1 || claude plugin marketplace update "${id#*@}" >/dev/null 2>&1 || true
    if [[ $1 == update ]]; then
      claude plugin update "$id" >/dev/null 2>&1 && echo "updated     plugin $id" || echo "warning: could not update plugin $id" >&2
    else
      claude plugin install "$id" --scope "$pscope" >/dev/null 2>&1 && echo "installed plugin $id ($pscope scope)" || echo "warning: could not install plugin $id; run: claude plugin install $id" >&2
    fi
  done
}

index_of() { local x=$1 i; shift; local a=("$@"); for i in "${!a[@]}"; do [[ ${a[$i]} == "$x" ]] && { echo "$i"; return 0; }; done; return 1; }

# Shallow-clone a third-party repo once per run; prints its checkout dir.
fetch() {
  local dir="$work/${1//\//__}"
  [[ -d "$dir" ]] || git clone --depth 1 --quiet "https://github.com/$1.git" "$dir" || return 1
  echo "$dir"
}

lock_get() { awk -F'\t' -v n="$2" '$1 == n { print $3 }' "$1/$LOCK" 2>/dev/null; }
lock_set() {  # dir name source commit
  local f="$1/$LOCK"
  { [[ -f $f ]] && awk -F'\t' -v n="$2" '$1 != n' "$f"; printf '%s\t%s\t%s\t%s\n' "$2" "$3" "$4" "$(date +%F)"; } > "$f.tmp"
  mv "$f.tmp" "$f"
}
lock_del() {
  local f="$1/$LOCK"
  awk -F'\t' -v n="$2" '$1 != n' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  [[ -s $f ]] || rm -f "$f"
}

# Keep a project-dir skill out of git for this clone only (local scope).
git_exclude() {  # dir name add|remove
  local top gitdir rel
  top="$(git -C "$1" rev-parse --show-toplevel 2>/dev/null)" || return 0
  gitdir="$(git -C "$1" rev-parse --absolute-git-dir)"
  local abs; abs="$(cd "$1" && pwd -P)/$2"; top="$(cd "$top" && pwd -P)"
  rel="/${abs#"$top"/}"
  mkdir -p "$gitdir/info"; touch "$gitdir/info/exclude"
  if [[ $3 == add ]]; then
    grep -qxF "$rel" "$gitdir/info/exclude" || echo "$rel" >> "$gitdir/info/exclude"
  else
    grep -vxF "$rel" "$gitdir/info/exclude" > "$gitdir/info/exclude.tmp" || true
    mv "$gitdir/info/exclude.tmp" "$gitdir/info/exclude"
  fi
}

# Copy one skill into a dir and record it in the lock. Prints the installed commit.
install_one() {  # name dir
  local name=$1 dir=$2 i co c
  mkdir -p "$dir"
  if i=$(index_of "$name" "${own_names[@]}"); then
    rm -rf "${dir:?}/${name:?}"
    cp -R "$(echo "$src"/plugins/*/skills/"$name")" "$dir/$name"
    c="$(own_version "$name")"
    lock_set "$dir" "$name" dipesh-skills "$c"
    echo "$c"
  elif i=$(index_of "$name" "${rec_names[@]}"); then
    co="$(fetch "${rec_repos[$i]}")" || { echo "warning: could not fetch ${rec_repos[$i]}, skipped $name" >&2; return 1; }
    rm -rf "${dir:?}/${name:?}"
    cp -R "$co/${rec_paths[$i]}" "$dir/$name"
    rm -rf "${dir:?}/${name:?}/.git"
    c="$(git -C "$co" rev-parse --short HEAD)"
    lock_set "$dir" "$name" "${rec_repos[$i]}" "$c"
    echo "$c"
  else
    echo "warning: unknown skill $name" >&2; return 1
  fi
}

# Skills dirs this script has installed into (those holding a lock file).
scopes() {
  local d a seen=" " dirs=()
  for a in claude codex agents; do dirs+=("$(skills_dir $a user)" "$(skills_dir $a project)"); done
  [[ $target_set -eq 1 ]] && dirs+=("${targets[@]}")
  for d in "${dirs[@]}"; do
    [[ -f "$d/$LOCK" && $seen != *" $d "* ]] && { echo "$d"; seen+="$d "; }
  done
  return 0
}
wanted() { [[ ${#names[@]} -eq 0 || " ${names[*]} " == *" $1 "* ]]; }

do_list() {
  local d any=0
  while read -r d; do
    any=1; echo "$d"
    awk -F'\t' '{ printf "  %-28s %-36s %s  (%s)\n", $1, $2, $3, $4 }' "$d/$LOCK"
  done < <(scopes)
  [[ $any -eq 1 ]] || echo "nothing installed by this script yet."
}

do_install() {
  local n c i ok=0 list=() labels=()
  [[ $own -eq 1 ]] && list+=("${own_names[@]}")
  [[ $recommended -eq 1 ]] && list+=("${rec_names[@]}")
  if [[ $select -eq 1 ]]; then
    for n in "${list[@]}"; do
      if i=$(index_of "$n" "${rec_names[@]}"); then labels+=("$(printf '%-28s %s' "$n" "${rec_repos[$i]}")")
      else labels+=("$(printf '%-28s this pack' "$n")"); fi
    done
    menu_many "Which skills should be installed?" "${labels[@]}"
    [[ ${#PICKS[@]} -gt 0 ]] || { echo "nothing selected" >&2; exit 1; }
    names=(); for i in "${PICKS[@]}"; do names+=("${list[$i]}"); done
  fi
  local setup_offered=" "
  for target in "${targets[@]}"; do
  for n in "${list[@]}"; do
    wanted "$n" || continue
    c="$(install_one "$n" "$target")" || continue
    [[ $local_scope -eq 1 ]] && { git_exclude "$target" "$n" add; git_exclude "$target" "$LOCK" add; }
    echo "installed $n ($c) -> $target/$n"; ok=$((ok + 1))
    # Skills that need a one-time setup (deps, models, choices) ship scripts/postinstall.
    # Setup state (venv, browser, model) is per user, not per agent dir: offer it once per skill.
    if [[ -x "$target/$n/scripts/postinstall" && $setup_offered != *" $n "* ]]; then
      setup_offered+="$n "
      if [[ $interactive -eq 1 || $select -eq 1 ]]; then
        menu_one "$n needs a one-time setup (browser, model, dependencies). Run it now?" "Yes" "Later"
        if [[ $PICK -eq 0 ]]; then
          scope=user; [[ $target == "$(pwd)"/* ]] && scope=project
          "$target/$n/scripts/postinstall" "$scope" < /dev/tty || echo "warning: $n setup failed; rerun $target/$n/scripts/postinstall" >&2
          continue
        fi
      fi
      echo "  -> finish with: $target/$n/scripts/postinstall"
    fi
  done
  done
  claude_plugins install
  [[ $ok -gt 0 ]] || [[ ${#names[@]} -gt 0 && " ${names[*]} " == *" ecc "* ]] || { echo "no matching skills found" >&2; exit 1; }
  [[ $local_scope -eq 1 ]] && echo "local scope: kept out of git via .git/info/exclude"
  echo "done. Restart your agent session to pick up new skills."
}

do_update() {
  local d n old new any=0
  while read -r d; do
    for n in $(cut -f1 "$d/$LOCK"); do
      wanted "$n" || continue
      any=1
      old="$(lock_get "$d" "$n")"
      new="$(install_one "$n" "$d")" || continue
      if [[ $old == "$new" ]]; then echo "up to date  $n ($new)  $d"
      else echo "updated     $n ($old -> $new)  $d"; fi
      # refresh the skill's own dependencies with the choices made at setup (no questions)
      if [[ -x "$d/$n/scripts/postinstall" && " ${refreshed:-} " != *" $n "* ]]; then  # once per skill
        "$d/$n/scripts/postinstall" --refresh < /dev/null || echo "warning: $n dependency refresh failed; run $d/$n/scripts/postinstall" >&2
        refreshed="${refreshed:-} $n"
      fi
    done
  done < <(scopes)
  claude_plugins update
  [[ $any -eq 1 ]] || { echo "nothing to update — install first."; exit 0; }
  echo "done. Restart your agent session to pick up changes."
}

# "Claude Code (user)", "Codex (project)", … for a skills dir; the path itself for custom dirs.
where_label() {
  local a nice
  for a in claude codex agents; do
    case $a in claude) nice="Claude Code" ;; codex) nice="Codex" ;; agents) nice=".agents" ;; esac
    [[ $1 == "$(skills_dir $a user)" ]] && { echo "$nice (user)"; return; }
    [[ $1 == "$(skills_dir $a project)" ]] && { echo "$nice (project)"; return; }
  done
  echo "$1"
}

# Interactive: "everything, everywhere" in one go, or pick skills + pick locations once (not per skill).
do_uninstall() {
  local d n i j skills=() places=() locs=() labels=() loc_labels=() where
  while read -r d; do
    locs+=("$d")
    for n in $(cut -f1 "$d/$LOCK"); do
      wanted "$n" || continue
      if i=$(index_of "$n" "${skills[@]}"); then places[$i]+=$'\n'"$d"
      else skills+=("$n"); places+=("$d"); fi
    done
  done < <(scopes)
  [[ ${#skills[@]} -gt 0 ]] || { echo "nothing to uninstall."; exit 0; }
  local total=0
  for i in "${!skills[@]}"; do
    where=""; while IFS= read -r d; do where+="$(where_label "$d") + "; total=$((total + 1)); done <<< "${places[$i]}"
    labels+=("$(printf '%-28s %s' "${skills[$i]}" "${where% + }")")
  done
  for d in "${locs[@]}"; do loc_labels+=("$(printf '%-22s %s' "$(where_label "$d")" "$d")"); done

  local pick_skills=() pick_locs=()
  if [[ $interactive -eq 1 || $select -eq 1 ]]; then
    menu_one "Uninstall what?" \
      "Everything, everywhere — ${#skills[@]} skills, $total copies (Claude Code, Codex, … · user + project)" \
      "Choose skills and locations"
    if [[ $PICK -eq 0 ]]; then
      for i in "${!labels[@]}"; do echo "  ${labels[$i]}"; done
      menu_one "Remove all of the above?" "No, keep them" "Yes, remove everything"
      [[ $PICK -eq 1 ]] || { echo "nothing removed."; exit 0; }
      pick_skills=("${!skills[@]}"); pick_locs=("${!locs[@]}")
    else
      MENU_DEFAULT=0 menu_many "Which skills should be removed?" "${labels[@]}"
      [[ ${#PICKS[@]} -gt 0 ]] || { echo "nothing selected"; exit 0; }
      pick_skills=("${PICKS[@]}")
      if [[ ${#locs[@]} -gt 1 ]]; then
        menu_many "Remove them from where? (applies to every skill you picked)" "${loc_labels[@]}"
        [[ ${#PICKS[@]} -gt 0 ]] || { echo "nothing selected"; exit 0; }
        pick_locs=("${PICKS[@]}")
      else
        pick_locs=(0)
      fi
    fi
  elif [[ ${#names[@]} -gt 0 || $yes -eq 1 ]]; then
    pick_skills=("${!skills[@]}"); pick_locs=("${!locs[@]}")
  else
    echo "refusing to remove every installed skill without a terminal: name the skills or pass --yes" >&2; exit 1
  fi

  local removed=0
  for i in "${pick_skills[@]}"; do
    n="${skills[$i]}"
    for j in "${pick_locs[@]}"; do
      d="${locs[$j]}"
      [[ $'\n'"${places[$i]}"$'\n' == *$'\n'"$d"$'\n'* ]] || continue   # this skill isn't installed there
      rm -rf "${d:?}/${n:?}"; lock_del "$d" "$n"; git_exclude "$d" "$n" remove
      echo "removed $n from $(where_label "$d")"; removed=$((removed + 1))
    done
  done
  echo "done: $removed $( [[ $removed -eq 1 ]] && echo copy || echo copies) removed."
}

if [[ $interactive -eq 1 ]]; then
  menu_one "What do you want to do?" \
    "Install skills" \
    "Update installed skills" \
    "Uninstall skills" \
    "Show installed skills"
  case $PICK in
    0) action=install; select=1
       MENU_ON=0 menu_many "Which agents are the skills for? (tick one or more)" \
         "Claude Code                                   .claude/skills" \
         "Codex                                         .codex/skills" \
         "Any agent using the open Agent Skills layout  .agents/skills" \
         "Custom directory"
       [[ ${#PICKS[@]} -gt 0 ]] || { echo "nothing selected" >&2; exit 1; }
       agents=(); custom=0
       for i in "${PICKS[@]}"; do case $i in 0) agents+=(claude) ;; 1) agents+=(codex) ;; 2) agents+=(agents) ;; 3) custom=1 ;; esac; done
       if [[ ${#agents[@]} -gt 0 ]]; then
         u="$(printf '~/.%s/skills + ' "${agents[@]}")"; u="${u% + }"
         p="$(printf './.%s/skills + ' "${agents[@]}")"; p="${p% + }"
         menu_one "Where should they be installed?" \
           "Install for you (user scope)                                      $u" \
           "Install for all collaborators on this repository (project scope)  $p, commit it" \
           "Install for you, in this repo only (local scope)                  $p, git-ignored"
         case $PICK in 0) scope=user ;; 1) scope=project ;; 2) scope=local; local_scope=1 ;; esac
       fi
       target=""; set_targets; [[ ${#agents[@]} -gt 0 ]] || targets=()
       if [[ $custom -eq 1 ]]; then
         printf 'Custom directory: ' > /dev/tty; read -r d < /dev/tty; d="${d/#\~/$HOME}"
         [[ -n "$d" ]] || { echo "no directory given" >&2; exit 1; }
         targets+=("$d"); target_set=1
       fi ;;
    1) action=update ;;
    2) action=uninstall ;;
    3) action=list ;;
  esac
fi

# Zip each skill folder (folder at the archive root) for upload to Claude.ai or the Claude API.
do_package() {
  local n co i list=() out="$(pwd)/dist" stage
  [[ $own -eq 1 ]] && list+=("${own_names[@]}")
  [[ $recommended -eq 1 ]] && list+=("${rec_names[@]}")
  mkdir -p "$out"
  for n in "${list[@]}"; do
    wanted "$n" || continue
    stage="$work/pkg"; rm -rf "${stage:?}"; mkdir -p "$stage"
    install_one "$n" "$stage" > /dev/null || continue
    rm -f "$stage/$LOCK" "$out/$n.zip"
    if command -v zip > /dev/null; then (cd "$stage" && zip -qr "$out/$n.zip" "$n" -x '*/.git/*' '*/__pycache__/*')
    else python3 -c 'import shutil,sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[2], sys.argv[3])' "$out/$n" "$stage" "$n"; fi
    echo "packaged $n -> $out/$n.zip"
  done
  echo "Upload in Claude.ai: Settings > Capabilities > Skills. Skills that run local programs (dipesh-browser-control) don't work in Claude.ai's sandbox."
}

case $action in
  package) do_package ;;
  install) do_install ;;
  update) do_update ;;
  uninstall) do_uninstall ;;
  list) do_list ;;
esac
