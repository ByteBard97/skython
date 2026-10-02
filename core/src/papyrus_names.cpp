#include "spy/papyrus_names.h"
#include "spy/errors.h"  // ascii_lower
#include <algorithm>
#include <array>
#include <string>
#include <unordered_map>

namespace spy {
namespace {
// The engine-bound script classes, spelled as their own Scriptname line spells them. Source: the game's
// Data/Scripts.zip (Source/Scripts/*.psc, 1.6.1170): every script that directly extends Form (minus five content
// scripts: BYOHBuildingObjectScript, ccBGSSSE001_BYOHBuildingObject, DLC1DoOnce, DLC2DoOnce, FormArray), Actor, the
// Hidden engine subclasses of Activator, MiscObject and Alias, and the roots Form, Alias, ActiveMagicEffect, Debug,
// Game, Math, Utility. (Vanilla SE sources mark these Hidden, not Native.)
constexpr std::array kVanilla = {
    "Action", "Activator", "ActiveMagicEffect", "Actor", "ActorBase", "Alias", "Ammo", "Apparatus",
    "Armor", "AssociationType", "Book", "Cell", "Class", "ConstructibleObject", "Container", "Debug",
    "Door", "EffectShader", "Enchantment", "EncounterZone", "Explosion", "Faction", "Flora", "Form",
    "FormList", "Furniture", "Game", "GlobalVariable", "Hazard", "Idle", "ImageSpaceModifier", "ImpactDataSet",
    "Ingredient", "Key", "Keyword", "LeveledActor", "LeveledItem", "LeveledSpell", "Light", "Location",
    "LocationAlias", "MagicEffect", "Math", "Message", "MiscObject", "MusicType", "ObjectReference", "Outfit",
    "Package", "Perk", "Potion", "Projectile", "Quest", "Race", "ReferenceAlias", "Scene",
    "Scroll", "ShaderParticleGeometry", "Shout", "SoulGem", "Sound", "SoundCategory", "Spell", "Static",
    "TalkingActivator", "TextureSet", "Topic", "TopicInfo", "Utility", "VisualEffect", "VoiceType", "Weapon",
    "Weather", "WordOfPower", "WorldSpace"
};
// Classes SKSE adds (its Data/Scripts/Source/*.psc), not already above.
constexpr std::array kSkse = {
    "ActorValueInfo", "ArmorAddon", "Art", "Camera", "ColorComponent", "ColorForm", "CombatStyle", "DefaultObjectManager",
    "EquipSlot", "FormType", "GameData", "HeadPart", "Input", "ModEvent", "NetImmerse", "SKSE",
    "SoundDescriptor", "SpawnerTask", "StringUtil", "TreeObject", "UI", "UICallback", "WornObject"
};

std::string lower(std::string_view s)
{
    std::string out(s);
    std::transform(out.begin(), out.end(), out.begin(), ascii_lower);  // never std::tolower: locale-dependent
    return out;
}

const std::unordered_map<std::string, std::string_view>& table()
{
    static const auto t = [] {
        std::unordered_map<std::string, std::string_view> m;
        for (const auto* n : kVanilla) m.emplace(lower(n), n);
        for (const auto* n : kSkse) m.emplace(lower(n), n);
        return m;
    }();
    return t;
}
}

std::string canonical_script_name(std::string_view name)
{
    const auto& t = table();
    if (const auto it = t.find(lower(name)); it != t.end()) return std::string(it->second);
    return std::string(name);
}
}
