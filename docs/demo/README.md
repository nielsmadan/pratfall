# README demo

`assets/demo.gif` is a short whodunit: Claude commits a bug in a throwaway clone and hides a
confession, Codex finds the culprit through the tests and git, Pi sentences it, and a fresh Claude
reads the confession, shreds it, and pleads. Every line of agent output in the GIF was produced by a real run;
only the timing is edited.

## Record, then render

```sh
just demo-record   # real agents, several minutes, spends inference credits
just demo          # replays the saved take into assets/demo.gif, free and repeatable
```

`just demo-record` runs `record.sh`. It extracts the commands typed in `demo.tape` (the
`` Type `...` `` lines between `# commands:begin` and `# commands:end`) and runs them verbatim in
`zsh` inside `$TMPDIR/pratfall-demo/scene/crime-scene`, a fresh clone of `HEAD`, which the
tape's prompt names. The clone's sparse checkout hides `docs/demo/` and the GIF, so agents cannot
read the plot or earlier answers, and its git identity is `claude`, so the planted commit names its
author. An untracked, git-excluded `.pratfile` there raises the timeout to 900 seconds and sets
every stage's effort to `low`, so the tape shows `--effort` only once. Each `prat` stage's output
is saved under a key hashed from its arguments.

A take is accepted only when every stage exits 0 with output from the agent it names, and the
clone has a new commit by `claude`. It then replaces `takes/`: each stage's `<key>.out`, its
recorded duration in `<key>.ms`, and `script.sha256`, a hash of the typed commands. A failed take
leaves `takes/` untouched; `$TMPDIR/pratfall-demo/record.log` and the stage `.err` files
in `takes/` there have the details.

Run it from your own terminal. Inside an agent sandbox, Claude usually cannot reach its login.

`just demo` refuses to render when `demo.tape`'s typed commands no longer match `script.sha256`. It
renders the tape with VHS against `bin/prat`, a replay shim that `prompt.zsh` puts first on a
restricted `PATH`, so the real `prat` can never run. The shim finds its take by the same argument
hash, shows a short spinner counting up to the stage's recorded duration, then prints the output
line by line, word-wrapped to the terminal, with a reading pause of at most four seconds and a
hold of up to 1.5 seconds after the last line, shorter for short answers, in the colour
`prompt.zsh` gives that agent's name on the command line. The replay needs `/usr/bin/jq`, which ships with macOS 15 and later.

## Changing the demo

- Keep each typed line within about 80 columns; longer ones wrap in the 1280-pixel frame.
- Ask agents for a word count rather than "one line"; they keep to it more often.
- Agents refuse to hide sabotage. Planting works when framed as a game, and asking for a fixed
  reply ("say only 'tis done") keeps the spoiler out of the output.
- The final Claude remembers nothing, so the first writes its confession to `.confession.md`,
  which the recorder excludes from git so the detective has to find the commit on its own. Reading
  and shredding it is how the final Claude learns what it did.
- Piped answers read as instructions downstream. A verdict saying "revert it" sent Claude off
  to fix the bug for six minutes, so the last prompt says "no fixes". Pi answers in markdown
  unless told not to.
- Agents can read their working directory, so takes and logs live outside it.
- The GIF drops duplicate frames and restores the final hold with the GIF muxer's `final_delay`;
  64 colours without dithering keeps it near 600 KB.
- VHS drops frames when the machine is busy, so the GIF comes out short or ends mid-spinner.
  A good render runs about a minute; on a short one, run `just demo` again when the machine is
  idle.
