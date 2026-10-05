# Probe 2: SetObjectiveText with alias tags

**Question:** powerofthree's `PO3_SKSEFunctions.SetObjectiveText(quest, text, index)` replaces an objective's text at runtime. If the new text contains alias tags, do they resolve?

**Result: pass.** After the call the banner reads the new line with all three names filled in.

## Method

Same fills and rendering as probe 1 (see the [README](README.md)). The test calls

```
PO3_SKSEFunctions.SetObjectiveText(quest, "Probe two: <Alias=SkythonTarget> / <Alias=SkythonItem> / <Alias=SkythonPlace>", 10)
```

through `game.call`, then forces the banner and captures the screen. This needs powerofthree's Papyrus Extender loaded; without it the call comes back as an unknown function.

## What happened

- With Stores Text set on the aliases: the banner shows the three names in place of the tags. `img/probe2-banner.png` is a crisp capture.

## Notes

- Runtime-set text goes through the same tag resolution as text from the plugin, and has the same requirement on the aliases.
- `sqo` prints the text you set, with the raw tags, so it only confirms that the text was stored.
