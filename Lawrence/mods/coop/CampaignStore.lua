-- Keeps the co-op campaign on disk. With manual save/load enabled, players' saves only ever go to the server, so
--  without this a server restart or the lobby closing while nobody's playing loses the whole campaign.
--
-- campaign.json holds what the players share, which is given to every player that starts or rejoins, on top of the
--  save they load: a player's save is only as new as their last one, which can be long before the other player's
--  progress (or before a crash). Every write keeps the last version next to it as .bak (see Game:WriteTextFile).
local json = require 'dkjson'

CampaignStore = {
    directory = "saves/coop",
}

local function path(name)
    return CampaignStore.directory .. "/" .. name
end

local function saveFileName(username)
    return "players/" .. username:gsub("[^%w%-_]", "_") .. ".sav"
end

local function readState(name)
    local text = Game:ReadTextFile(path(name))
    if text == nil then
        return nil
    end

    local state, _, err = json.decode(text)
    if type(state) ~= "table" then
        print("Couldn't read the saved co-op campaign's " .. name .. ": " .. tostring(err))
        return nil
    end

    return state
end

function CampaignStore.Exists()
    return Game:DirectoryExists(CampaignStore.directory)
end

function CampaignStore.Load()
    local state = readState("campaign.json")
    if state == nil then
        -- Unreadable (or gone): the version before it is the campaign minus at most the last change
        state = readState("campaign.json.bak")
        if state ~= nil then
            print("Continuing the co-op campaign from the previous version of campaign.json")
        end
    end

    if state == nil then
        return nil
    end

    state.saveFiles = {}
    for _, username in ipairs(state.players or {}) do
        state.saveFiles[username] = Game:ReadDataFile(path(saveFileName(username)))
    end
    state.primarySaveFile = Game:ReadDataFile(path("primary.sav"))

    return state
end

function CampaignStore.Save(lobby)
    local players = {}
    for username, _ in pairs(lobby.saveFiles) do
        table.insert(players, username)
    end

    local gildedItems = {}
    for address, _ in pairs(lobby.gildedItems) do
        table.insert(gildedItems, address)
    end
    table.sort(gildedItems)

    local counters = {}
    for address, value in pairs(lobby.counters) do
        table.insert(counters, {address = address, value = value})
    end
    table.sort(counters, function(a, b) return a.address < b.address end)

    local levelFlags = {}
    for _, flag in pairs(lobby.levelFlags) do
        table.insert(levelFlags, flag)
    end
    table.sort(levelFlags, function(a, b)
        if a.level ~= b.level then return a.level < b.level end
        if a.type ~= b.type then return a.type < b.type end
        return a.index < b.index
    end)

    local state = {
        bolts = lobby.bolts,
        unlockedInfobots = lobby.unlockedInfobots,
        unlockedItems = lobby.unlockedItems,
        unlockedSkillpoints = lobby.unlockedSkillpoints,
        goldBolts = lobby.goldBolts,
        gildedItems = gildedItems,
        counters = counters,
        levelFlags = levelFlags,
        options = {
            friendlyFire = lobby.options.friendlyFire.value,
            deathLink = lobby.options.deathLink.value,
        },
        players = players,
        savedAt = os.date("%Y-%m-%d %H:%M:%S"),
    }

    -- If it couldn't be written (something had the file open), it's tried again within a second
    lobby.dirty = not Game:WriteTextFile(path("campaign.json"), json.encode(state, { indent = true }))
end

function CampaignStore.SaveFile(username, saveFile, isPrimary)
    Game:WriteDataFile(path(saveFileName(username)), saveFile)

    if isPrimary then
        Game:WriteDataFile(path("primary.sav"), saveFile)
    end
end

-- Moves the saved campaign aside when players start a new one, so it's never silently overwritten. Returns whether
--  there's no campaign in the way anymore.
function CampaignStore.Archive()
    if not CampaignStore.Exists() then
        return true
    end

    local backup = CampaignStore.directory .. "-backup-" .. os.date("%Y%m%d-%H%M%S")

    if Game:MoveDirectory(CampaignStore.directory, backup) then
        print("Moved the previous co-op campaign to " .. backup)
        return true
    end

    return false
end
