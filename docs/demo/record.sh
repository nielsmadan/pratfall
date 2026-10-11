#!/usr/bin/env bash
# Runs the demo tape's typed commands with real agents in a throwaway clone and saves each prat
# stage's output under docs/demo/takes/, keyed by a hash of its arguments, for the replay shim.
set -euo pipefail

repo=$(git rev-parse --show-toplevel)
demo=$repo/docs/demo
base=${TMPDIR:-/tmp}/pratfall-demo
work=$base/scene
takes=$base/takes
log=$base/record.log

for tool in prat zsh uv jq shasum; do
    command -v "$tool" >/dev/null || { echo "record: $tool not found" >&2; exit 1; }
done

block=$(sed -n '/^# commands:begin$/,/^# commands:end$/p' "$demo/demo.tape")
if grep '^Type' <<<"$block" | grep -qv '^Type `.*`$'; then
    echo 'record: write every typed command between the markers as Type `...`' >&2
    exit 1
fi
script=$(sed -n 's/^Type `\(.*\)`$/\1/p' <<<"$block")
[ -n "$script" ] || { echo "record: no typed commands between the markers in demo.tape" >&2; exit 1; }

rm -rf "$work" "$takes" "$log" "$base/final.out"
mkdir -p "$work" "$takes"
git clone -q --no-checkout "$repo" "$work/crime-scene"
git -C "$work/crime-scene" sparse-checkout set --no-cone '/*' '!/docs/demo/' '!/assets/demo.gif'
git -C "$work/crime-scene" checkout -q
git -C "$work/crime-scene" remote remove origin
git -C "$work/crime-scene" config user.name claude
git -C "$work/crime-scene" config user.email claude@crime-scene.invalid
(cd "$work/crime-scene" && uv sync -q)
printf 'version = 1\n\n[defaults]\ntimeout = 900\neffort = "low"\n' >"$work/crime-scene/.pratfile"
printf '.pratfile\n.confession.md\n' >>"$work/crime-scene/.git/info/exclude"
start=$(git -C "$work/crime-scene" rev-parse HEAD)

{
    echo "date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit: $start"
    echo "prat: $(command -v prat) $(prat --version)"
    printf 'script:\n%s\n' "$script"
} >"$log"

echo "Recording in $work/crime-scene (log: $log); this takes several minutes." >&2
cd "$work/crime-scene"
status=0
PRAT_DEMO_TAKES=$takes zsh -f -c '
setopt pipefail
prat() {
    local key=$(printf "%s\0" "$@" | shasum -a 256 | cut -c1-16)
    command prat "$@" >"$PRAT_DEMO_TAKES/$key.out" 2>"$PRAT_DEMO_TAKES/$key.err"
    local rc=$?
    print -r -- "stage: prat $1 key=$key exit=$rc" >&2
    (( rc )) && cat "$PRAT_DEMO_TAKES/$key.err" >&2
    cat "$PRAT_DEMO_TAKES/$key.out"
    return $rc
}
tee() { command tee /dev/stderr; }
'"$script" </dev/null >"$base/final.out" 2>>"$log" || status=$?

fail() { echo "record: $1; see $log and $takes" >&2; exit 1; }
stages=$(grep -c '^stage: ' "$log" || true)
failed=$(grep '^stage: ' "$log" | grep -vc 'exit=0$' || true)
if [ "$status" -ne 0 ] || [ "$failed" -ne 0 ] || [ "$stages" -eq 0 ]; then
    fail "take failed (status $status, $failed of $stages stages failed)"
fi
[ "$(git -C "$work/crime-scene" rev-parse HEAD)" != "$start" ] || fail "no bug was committed"
[ "$(git -C "$work/crime-scene" log -1 --format=%an)" = claude ] || fail "the last commit is not claude's"

while read -r agent key; do
    case $agent in
        claude) label='Claude Code' ;;
        codex) label=Codex ;;
        pi) label=Pi ;;
        *) label=$agent ;;
    esac
    grep -q "^prat: launching $label\$" "$takes/$key.err" || fail "stage $key did not launch $label"
    [ -s "$takes/$key.out" ] || fail "empty output from prat $agent ($key)"
    if LC_ALL=C grep -q $'\033' "$takes/$key.out"; then fail "escape sequence in $key.out"; fi
    sed -n 's/.* finished with status .* in \([0-9]*\)ms$/\1/p' "$takes/$key.err" | tail -1 >"$takes/$key.ms"
done < <(sed -n 's/^stage: prat \([^ ]*\) key=\([0-9a-f]*\) exit=0$/\1 \2/p' "$log")

rm -rf "$demo/takes"
mkdir -p "$demo/takes"
cp "$takes"/*.out "$takes"/*.ms "$demo/takes/"
printf '%s' "$script" | shasum -a 256 | cut -c1-64 >"$demo/takes/script.sha256"
echo "Saved $stages stages to docs/demo/takes. Final output:" >&2
cat "$base/final.out" >&2
