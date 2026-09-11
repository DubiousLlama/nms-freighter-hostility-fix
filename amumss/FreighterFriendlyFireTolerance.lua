-- AMUMSS script: FreighterFriendlyFireTolerance
--
-- Builds the freighter friendly-fire mod from YOUR game's own files. Preferred over the pre-made
-- .EXML files when you want AMUMSS to print the vanilla values it replaces, when you want the
-- calibrated variants (they are defined relative to vanilla), or when other scripts touch the
-- same globals. Drop into AMUMSS\ModScript\ and run BUILDMOD.bat (AMUMSS >= 5.5).

-- ---------------------------------------------------------------------------------------
-- Pick ONE variant. See README.md for behaviour and the exploit analysis of each.
-- ---------------------------------------------------------------------------------------
VARIANT = "F"   -- "A" = hits can never make a freighter hostile (personal use; removes the deterrent)
                -- "B" = vanilla thresholds, hostility drains a few seconds after the last hit
                -- "D" = freighters ignore the player entirely (sledgehammer)
                -- "E" = FORGIVE: vanilla thresholds, alert bar drains in ~15 s once you stop firing
                -- "F" = STRICT (recommended for publishing): both alert thresholds x THRESHOLD_MULT,
                --       everything else vanilla. Stray hits must add up to 5x the vanilla budget;
                --       a deliberate attack on a pod still gets there fast and stays hostile as vanilla.
                -- "EF" = both E and F

THRESHOLD_MULT   = 5      -- F/EF: multiplier applied to FreighterAlertThreshold and FreighterAttackAlertThreshold
DRAIN_RATE_MULT  = 25     -- E/EF: multiplier applied to the vanilla FreighterAlertTimeOutRate
DRAIN_MIN_TIME   = 2      -- E/EF: seconds after the last registered hit before the bar starts draining

-- Keep the cargo pods' own consequences (sentinel wanted level, standing loss) active even while a
-- pirate battle is running, so "loot the freighter under cover of the battle" is still punished.
-- Edits the destructible component of the capital-freighter cargo containers. Safe to leave on.
PODS_KEEP_CONSEQUENCES = true

-- ---------------------------------------------------------------------------------------

local globals_mxml = {}

local function add(tbl) table.insert(globals_mxml, tbl) end

if VARIANT == "A" then
    add({ ["INTEGER_TO_FLOAT"] = "FORCE", ["VALUE_CHANGE_TABLE"] = { {"FreighterAttackAlertThreshold", "1000000000"} } })
elseif VARIANT == "B" then
    add({ ["INTEGER_TO_FLOAT"] = "FORCE", ["VALUE_CHANGE_TABLE"] = { {"FreighterAlertTimeOutMinTime", "3"}, {"FreighterAlertTimeOutRate", "1000000"} } })
elseif VARIANT == "D" then
    add({ ["VALUE_CHANGE_TABLE"] = { {"FreighterIgnorePlayer", "true"} } })
end

if VARIANT == "F" or VARIANT == "EF" then
    add({
        ["MATH_OPERATION"]   = "*",
        ["INTEGER_TO_FLOAT"] = "FORCE",
        ["VALUE_CHANGE_TABLE"] = {
            {"FreighterAlertThreshold",       THRESHOLD_MULT},
            {"FreighterAttackAlertThreshold", THRESHOLD_MULT},
        },
    })
end
if VARIANT == "E" or VARIANT == "EF" then
    add({
        ["MATH_OPERATION"]   = "*",
        ["INTEGER_TO_FLOAT"] = "FORCE",
        ["VALUE_CHANGE_TABLE"] = { {"FreighterAlertTimeOutRate", DRAIN_RATE_MULT} },
    })
    add({
        ["INTEGER_TO_FLOAT"] = "FORCE",
        ["VALUE_CHANGE_TABLE"] = { {"FreighterAlertTimeOutMinTime", DRAIN_MIN_TIME} },
    })
end

local mbin_changes = {
    {
        ["MBIN_FILE_SOURCE"]  = "GCAISPACESHIPGLOBALS.GLOBAL.MBIN",
        ["MXML_CHANGE_TABLE"] = globals_mxml,
    },
}

if PODS_KEEP_CONSEQUENCES then
    table.insert(mbin_changes, {
        ["MBIN_FILE_SOURCE"] = {
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERA\\ENTITIES\\CONTAINER_A.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERB\\ENTITIES\\CONTAINER_B.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERC\\ENTITIES\\CONTAINER_C.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERE\\ENTITIES\\CONTAINER_E.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERF\\ENTITIES\\CONTAINER_F.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERG\\ENTITIES\\CONTAINER_G.ENTITY.MBIN",
            "MODELS\\COMMON\\SPACECRAFT\\INDUSTRIAL\\CONTAINER\\CONTAINERH\\ENTITIES\\CONTAINER_H.ENTITY.MBIN",
        },
        ["MXML_CHANGE_TABLE"] = {
            {
                ["SPECIAL_KEY_WORDS"] = {"Components", "GcDestructableComponentData"},
                ["VALUE_CHANGE_TABLE"] = { {"NoConsequencesDuringPirateBattle", "false"} },
            },
        },
    })
end

NMS_MOD_DEFINITION_CONTAINER =
{
    ["MOD_FILENAME"]    = "FreighterFriendlyFireTolerance",
    ["MOD_AUTHOR"]      = "DubiousLlama",
    ["LUA_AUTHOR"]      = "DubiousLlama",
    ["NMS_VERSION"]     = "7.01",
    ["MOD_DESCRIPTION"] = "Civilian freighters forgive stray hits during pirate battles but still retaliate against deliberate attacks.",
    ["MODIFICATIONS"]   = { { ["MBIN_CHANGE_TABLE"] = mbin_changes } },
}
