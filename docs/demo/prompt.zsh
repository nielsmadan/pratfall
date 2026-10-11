# Shell setup for the demo tape: replay shim first on PATH, a coloured prompt, and command-line
# highlighting that gives each agent the colour its output is printed in.
PATH="$PRAT_DEMO_DIR/bin:/usr/bin:/bin"
hash -r
export LC_ALL=en_US.UTF-8
setopt no_bang_hist interactive_comments no_prompt_sp
PS1='%F{#7f849c}crime-scene%f %F{#cba6f7}❯%f '
PS2='  '

_demo_highlight() {
    region_highlight=()
    if [[ $BUFFER == \#* ]]; then
        region_highlight=("0 ${#BUFFER} fg=#7f849c,italic")
        return
    fi
    local rest=$BUFFER offset=0 style quotes=${PREBUFFER//[^\"]/}
    if (( ${#quotes} % 2 )); then
        [[ $BUFFER == *\"* ]] || { region_highlight=("0 ${#BUFFER} fg=#f9e2af"); return; }
        local close=${BUFFER%%\"*}
        offset=$(( ${#close} + 1 ))
        region_highlight+=("0 $offset fg=#f9e2af")
        rest=${BUFFER[offset+1,-1]}
    fi
    while [[ $rest =~ "\"[^\"]*\"?|'[^']*'?|[|>\\\\]|[^ \"'|>\\\\]+" ]]; do
        case $MATCH in
            [\"\']*) style='fg=#f9e2af' ;;
            -*) style='fg=#89b4fa' ;;
            prat) style='fg=#cba6f7,bold' ;;
            claude) style='fg=#fab387,bold' ;;
            codex) style='fg=#a6e3a1,bold' ;;
            pi) style='fg=#f5c2e7,bold' ;;
            tee|jq) style='fg=#94e2d5' ;;
            [\|\>\\]) style='fg=#9399b2' ;;
            *) style= ;;
        esac
        [[ -n $style ]] && region_highlight+=("$((offset + MBEGIN - 1)) $((offset + MEND)) $style")
        offset=$((offset + MEND))
        rest=${rest[MEND+1,-1]}
    done
}
zle -N zle-line-pre-redraw _demo_highlight
