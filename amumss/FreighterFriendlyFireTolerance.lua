-- AMUMSS script: FreighterFriendlyFireTolerance
--
-- Builds the same patch as GAMEDATA/MODS/FreighterFriendlyFireTolerance from YOUR game's
-- own GCAISPACESHIPGLOBALS.GLOBAL.MBIN. Use this instead of the pre-made .EXML if you want
-- AMUMSS to (a) print the vanilla values it replaces in its report, and (b) merge these
-- edits with other mods that touch the same globals file.
--
-- Drop this file into AMUMSS\ModScript\ and run BUILDMOD.bat.
-- Requires AMUMSS >= 5.5 (the version that outputs GAMEDATA\MODS folders, not .pak files).

-- ---------------------------------------------------------------------------------------
-- Pick ONE variant. See README.md for what each one does and how to test it.
-- ---------------------------------------------------------------------------------------
VARIANT = "A"   -- "A" = hits never escalate to hostility (default)
                -- "B" = hostility decays a few seconds after the last hit ("auto-forgive")
                -- "AB" = both A and B
                -- "D" = freighters ignore the player entirely (sledgehammer)

local ai_changes = {}

if VARIANT == "A" or VARIANT == "AB" then
    table.insert(ai_changes, {"FreighterAttackAlertThreshold", "1000000000"})
end
if VARIANT == "B" or VARIANT == "AB" then
    table.insert(ai_changes, {"FreighterAlertTimeOutMinTime", "3"})
    table.insert(ai_changes, {"FreighterAlertTimeOutRate",    "1000000"})
end
if VARIANT == "D" then
    table.insert(ai_changes, {"FreighterIgnorePlayer", "true"})
end

NMS_MOD_DEFINITION_CONTAINER =
{
    ["MOD_FILENAME"]    = "FreighterFriendlyFireTolerance",
    ["MOD_AUTHOR"]      = "DubiousLlama",
    ["LUA_AUTHOR"]      = "DubiousLlama",
    ["NMS_VERSION"]     = "7.01",
    ["MOD_DESCRIPTION"] = "Stray hits on a civilian freighter during a pirate battle no longer make it hostile / close its hangar.",
    ["MODIFICATIONS"]   =
    {
        {
            ["MBIN_CHANGE_TABLE"] =
            {
                {
                    ["MBIN_FILE_SOURCE"] = "GCAISPACESHIPGLOBALS.GLOBAL.MBIN",
                    ["MXML_CHANGE_TABLE"] =
                    {
                        {
                            ["INTEGER_TO_FLOAT"]   = "FORCE",
                            ["VALUE_CHANGE_TABLE"] = ai_changes,
                        },
                    },
                },
            },
        },
    },
}
