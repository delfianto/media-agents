#!/usr/bin/env bash
# Checkout bootstrap for psammophis. Humans: ./run.sh … ; skills: .agents/run.sh …
# This script does not route commands or construct PYTHONPATH — uv + the
# installed package own that. Signals and exit status pass through via exec.

set -euo pipefail

launcher_caller_pwd=$PWD
launcher_path=${BASH_SOURCE[0]}
if [[ $launcher_path != /* ]]; then
    launcher_path=$PWD/$launcher_path
fi

# Prefer the logical path so a media-root `.agents` symlink survives.
repo_root=$(cd -L -- "$(dirname -- "$launcher_path")" && pwd -L)
if [[ -d $PWD/.agents && $PWD/.agents -ef $repo_root ]]; then
    repo_root=$PWD/.agents
fi

envrc=$repo_root/.envrc
if [[ -f $envrc ]]; then
    allexport_was_set=0
    [[ $- == *a* ]] && allexport_was_set=1
    set -a
    # shellcheck source=/dev/null
    source "$envrc"
    ((allexport_was_set)) || set +a
    cd -L -- "$launcher_caller_pwd"
fi

if [[ -z ${MEDIALIB_ROOT:-} ]]; then
    if [[ -d $PWD/.agents && $PWD/.agents -ef $repo_root ]]; then
        MEDIALIB_ROOT=$PWD
    elif [[ ${repo_root##*/} == .agents ]]; then
        MEDIALIB_ROOT=${repo_root%/*}
    fi
fi
export MEDIALIB_ROOT

# Preserve the caller's cwd; only the project path is absolute.
exec uv run --project "$repo_root" psammophis "$@"
