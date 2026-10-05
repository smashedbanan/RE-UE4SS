-- ProbeLua: one "PROBE PASS lua:<check>" / "PROBE FAIL lua:<check>" line per Lua API the Linux port
-- has to support (tools/linux-test/run.sh). Built for a dedicated server: it needs no player.
--   at load:          StaticFindObject, ExecuteInGameThread, NotifyOnNewObject, io.open.backslash (a path
--                     joined with "\", as Windows-authored mods do)
--   once L_World is up: FindFirstOf (its GameState), RegisterHook on a native function (hooked, then
--                     called directly), RegisterHook on Blueprint functions (the event graphs and
--                     ticks of live Blueprint classes, which run on their own)
local TAG = "[ProbeLua] "
local WORLD = "/Game/Maps/World/L_World.L_World:PersistentLevel."
local BLUEPRINT_DEADLINE = 300 -- seconds after L_World is up for a hooked Blueprint function to run
local MAX_BLUEPRINT_HOOKS = 50

local results = {}
local function report(name, ok, detail)
    if results[name] ~= nil then return end
    results[name] = ok
    print(string.format("%sPROBE %s lua:%s%s\n", TAG, ok and "PASS" or "FAIL", name, detail and (" " .. detail) or ""))
end

-- At load ------------------------------------------------------------------------------------------
local actorClass = StaticFindObject("/Script/Engine.Actor")
if actorClass and actorClass:IsValid() then
    report("StaticFindObject", true, actorClass:GetFullName())
else
    report("StaticFindObject", false, "/Script/Engine.Actor not found")
end

ExecuteInGameThread(function() report("ExecuteInGameThread", true) end)

NotifyOnNewObject("/Script/Engine.Actor", function(obj)
    report("NotifyOnNewObject", true, obj:GetFullName())
    return true
end)

do -- This Scripts directory, found the way PartyHats finds its own, then joined with "\" as it does
    local src = (debug.getinfo(1, "S").source or ""):gsub("^@", "")
    local path = (src:match("^(.*)[/\\][^/\\]*$") or "") .. "\\main.lua"
    local file, err = io.open(path, "r")
    if file then file:close() end
    report("io.open.backslash", file ~= nil, file and path or err)
end

-- Once L_World is up -------------------------------------------------------------------------------
local function nativeCheck(gameState)
    local path = "/Script/Engine.Actor:K2_GetActorLocation"
    local pre, post = RegisterHook(path, function() report("RegisterHook.native", true, path) end)
    gameState:K2_GetActorLocation() -- on the game thread, through ProcessEvent: the hook runs now
    pcall(UnregisterHook, path, pre, post)
    if results["RegisterHook.native"] == nil then
        report("RegisterHook.native", false, "hook did not run on a direct call of " .. path)
    end
end

local blueprintHooks = {}
local function hookBlueprints()
    local seen = {}
    local function hookClassOf(object)
        if #blueprintHooks >= MAX_BLUEPRINT_HOOKS then return end
        local class = object:GetClass()
        local className = class:GetFullName()
        if seen[className] or not className:find("^BlueprintGeneratedClass ") then return end
        seen[className] = true
        class:ForEachFunction(function(fn)
            local name = fn:GetFName():ToString()
            if name == "ReceiveTick" or name:find("^ExecuteUbergraph") then
                local path = fn:GetFullName()
                local ok, pre, post = pcall(RegisterHook, path, function()
                    report("RegisterHook.blueprint", true, path)
                end)
                if ok then table.insert(blueprintHooks, { path = path, pre = pre, post = post }) end
            end
            -- true or nil, never false: UE4SS's ForEachFunction binding stops after the first item when
            -- the callback returns false (LuaUStruct.cpp, upstream, same on Windows).
            return #blueprintHooks >= MAX_BLUEPRINT_HOOKS or nil
        end)
    end
    for _, className in ipairs({ "Actor", "ActorComponent" }) do
        for _, object in ipairs(FindAllOf(className) or {}) do hookClassOf(object) end
    end
    return #blueprintHooks
end

local function unhookBlueprints()
    for _, h in ipairs(blueprintHooks) do pcall(UnregisterHook, h.path, h.pre, h.post) end
    blueprintHooks = {}
end

local startedAt, worldAt, done = os.time(), nil, false
local loop
loop = LoopInGameThreadWithDelay(5000, function()
    if done then return end
    if worldAt == nil then
        local gameState = FindFirstOf("GameStateBase")
        if gameState:IsValid() and gameState:GetFullName():find(WORLD, 1, true) then
            worldAt = os.time()
            report("FindFirstOf", true, gameState:GetFullName())
            nativeCheck(gameState)
            local hooked = hookBlueprints()
            print(string.format("%shooked %d Blueprint functions\n", TAG, hooked))
            if hooked == 0 then
                report("RegisterHook.blueprint", false, "no ReceiveTick/ExecuteUbergraph in any live Blueprint class")
            end
        elseif os.time() - startedAt > 900 then
            report("FindFirstOf", false, "no GameStateBase in L_World after 900 s")
            worldAt = os.time()
        end
        return
    end
    if results["RegisterHook.blueprint"] == nil and os.time() - worldAt <= BLUEPRINT_DEADLINE then return end
    if results["RegisterHook.blueprint"] == nil then
        report("RegisterHook.blueprint", false, "no hooked Blueprint function ran in " .. BLUEPRINT_DEADLINE .. " s")
    end
    unhookBlueprints()
    for _, name in ipairs({ "StaticFindObject", "ExecuteInGameThread", "NotifyOnNewObject", "FindFirstOf", "RegisterHook.native" }) do
        if results[name] == nil then report(name, false, "never ran") end
    end
    local pass, fail = 0, 0
    for _, ok in pairs(results) do if ok then pass = pass + 1 else fail = fail + 1 end end
    print(string.format("%sPROBE DONE pass=%d fail=%d\n", TAG, pass, fail))
    done = true
    CancelDelayedAction(loop)
end)
