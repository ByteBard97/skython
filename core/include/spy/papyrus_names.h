#pragma once
#include <string>
#include <string_view>

namespace spy {
// The .psc spelling of an engine-bound Papyrus script class ("weapon", "WEAPON" -> "Weapon"). The VM's names come
// from its case-insensitive string pool, so GetName() can give "WEAPON" or "utility" (Task 28, S7a). A name not in
// the table (a script-defined class, a mod's class) is returned unchanged; never throws.
std::string canonical_script_name(std::string_view name);
}
