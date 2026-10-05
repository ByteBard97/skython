# Probe 3: save and reload

**Question:** after saving and loading, do the alias fills, the displayed flag and the objective text (as changed by `SetObjectiveText`) survive, and do the tags still resolve?

**Result: pass.** Everything survives, and the banner after the reload resolves the tags the same way.

## Method

Fills and rendering are described in the [README](README.md). The test records the three fills, saves with `game.save`, waits for the `.ess` to appear, loads it with `game.load(..., wait=True)` (the epoch goes from 1 to 2), and reads the fills and `IsObjectiveDisplayed` again. It reads `sqo` before anything re-displays the objective, then forces the banner and captures the screen.

## What happened

- Alias 0, alias 1 and alias 3 hold the same forms after the reload as before it.
- Objective 10 is still displayed.
- The text after the reload is the text probe 2 set with `SetObjectiveText`, not the text from the plugin. Runtime-set text is saved with the game.
- The banner after the reload resolves all three tags: `img/probe3-after-reload.png`.

## Notes

- `sqo` after the reload prints the stored text with the raw tags. It never resolves them, before or after a reload, so it doesn't indicate anything broke.
- The sword is a placed reference, so it comes back as the same reference. Nothing was done to keep it alive beyond the alias fill.
