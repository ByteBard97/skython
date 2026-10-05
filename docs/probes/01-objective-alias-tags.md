# Probe 1: three alias tags in one objective

**Question:** an objective's text holds `<Alias=SkythonTarget>`, `<Alias=SkythonItem>` and `<Alias=SkythonPlace>`. After all three aliases are filled, does the banner show all three names?

**Result: pass.** The banner reads "Skython test: Farmer, Iron Sword, Skython Test Location".

## Method

Fills and rendering are described in the [README](README.md). After the three fills, the test checks through the bridge that alias 0 holds the NPC, alias 1 holds the placed sword and alias 3 holds the location, and that each has a non-empty name (`GetDisplayName` for the references, `GetName` for the location). It then toggles objective 10's displayed flag with the force flag set and captures the screen.

## What happened

- First run: the aliases had no Stores Text flag. The fills were correct and `IsObjectiveDisplayed` was true, but every tag rendered as `[...]` on the banner and `sqo` printed the raw tags.
- After adding Stores Text to aliases 0 to 3 in the test plugin and rebuilding: all three names resolve in one line. The capture that decided it is `img/probe1-banner.png`. It was taken while the banner was fading, but the text is legible.

## Notes

- Three tags in one line is fine. The limit that bit was the flag, not the count.
- The reference aliases resolve to the reference's display name, and the location alias to the location's name.
