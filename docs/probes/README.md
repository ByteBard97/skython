# Engine probes

Three questions that any mod building quests at runtime ends up asking, answered by driving the engine through Skython's generic call path on a small test plugin (`testesp/`, built by `testesp/build.sh`).

| Probe | Question | Result |
|---|---|---|
| [1](01-objective-alias-tags.md) | Do three `<Alias=...>` tags in one objective line all resolve to the names of the filled aliases? | Yes, if the aliases have Stores Text set |
| [2](02-setobjectivetext-alias-tags.md) | Does text set at runtime with powerofthree's `SetObjectiveText` resolve alias tags the same way? | Yes, same condition |
| [3](03-objective-save-reload.md) | Do the objective text and the alias fills survive a save and reload? | Yes, and the tags still resolve afterwards |

## What the aliases need

On the first run every tag showed up as `[...]` on the HUD banner. The test plugin's aliases had no Stores Text flag. With the flag set on aliases 0 to 3, all three probes passed. If you build quests in code and see `[...]` where a name should be, check that flag first.

## Setup

Skyrim AE 1.6.1170.0, `SkythonTest.esp` active, a save made from the plugin's test cell. The objective (index 10) reads `Skython test: <Alias=SkythonTarget>, <Alias=SkythonItem>, <Alias=SkythonPlace>`. The fills are the same in every probe:

- `SkythonTarget` (reference alias 0) gets the plugin's NPC through `ForceRefTo`. The NPC's display name is "Farmer" (it uses a template's traits).
- `SkythonItem` (reference alias 1) gets an iron sword placed at the player.
- `SkythonPlace` (location alias 3) gets the plugin's location through `ForceLocationTo`, named "Skython Test Location".
- Alias 2 is not tagged and stays empty.

## How the text was read

The HUD banner decides. `SetObjectiveDisplayed(idx, False, False)` followed by `SetObjectiveDisplayed(idx, True, True)` makes the game show the objective banner for a few seconds, and the screenshot is taken from inside the game window through DevBench's `capture` (HUD included). A probe passes when every tag on the banner is replaced by the filled alias's name.

The `sqo` console command (`sqo SkythonTestQuest`) was run as well, but it only prints the stored text with the tags unresolved, so it can't tell you what a player sees. Treat it as a way to read the raw text, not the rendered one.

The test is `livetest/test_m15_probes.py`. Screenshots are in `img/`.
