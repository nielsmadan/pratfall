# README demo, rendered by `just demo` from the take `just demo-record` saved in takes/.
# The typed commands between the markers are what `record.sh` runs for real; after editing them,
# `just demo` refuses to render until a new take is recorded. See README.md here.

Output .cache/demo.mp4

Set Shell "zsh"
Set FontFamily "JetBrains Mono, Menlo, monospace"
Set FontSize 22
Set Width 1280
Set Height 720
Set Padding 22
Set WindowBar Colorful
Set BorderRadius 10
Set Margin 16
Set MarginFill "#11111b"
Set Theme "Catppuccin Mocha"
Set TypingSpeed 32ms
Set CursorBlink false
Set Framerate 25

Hide
Type `source "$PRAT_DEMO_DIR/prompt.zsh" && clear`
Enter
Sleep 1s
Show
Sleep 800ms

# commands:begin
Type `# act 1: the crime`
Sleep 1s
Enter
Sleep 500ms
Type `prat claude --effort low \`
Enter
Type `  "whodunit game: quickly commit a test-breaking bug, confess in`
Enter
Type `  .confession.md, then say only 'tis done"`
Sleep 2s
Enter
Wait@60s /❯$/
Sleep 300ms
Type `# act 2: the investigation`
Sleep 1s
Enter
Sleep 500ms
Type `prat codex "a test broke. who did it? ten words" | tee /dev/tty |`
Sleep 1s
Enter
Type `prat pi "judge: sentence them in ten words. no markdown" | tee /dev/tty |`
Sleep 1s
Enter
Type `prat claude --json \`
Enter
Type `  "you stand accused. read and shred .confession.md, then`
Enter
Type `  plead in ten words, no fixes" | jq -r .output`
Sleep 2s
Enter
# commands:end
Wait@90s /❯$/
Sleep 8s
