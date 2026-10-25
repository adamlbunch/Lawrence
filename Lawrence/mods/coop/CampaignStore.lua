-- Keeps the co-op campaign on disk. With manual save/load enabled, players' saves only ever go to the server, so
--  without this a server restart or the lobby closing while nobody's playing loses the whole campaign.
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

function CampaignStore.Load()
    local text = Game:ReadTextFile(path("campaign.json"))
    if text == nil then
        return nil
    end

    local state, _, err = json.decode(text)
    if type(state) ~= "table" then
        print("Couldn't read the saved co-op campaign: " .. tostring(err))
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

    local state = {
        bolts = lobby.bolts,
        unlockedInfobots = lobby.unlockedInfobots,
        unlockedItems = lobby.unlockedItems,
        unlockedSkillpoints = lobby.unlockedSkillpoints,
        players = players,
        savedAt = os.date("%Y-%m-%d %H:%M:%S"),
    }

    Game:WriteTextFile(path("campaign.json"), json.encode(state, { indent = true }))
    lobby.lastCampaignSave = os.time()
end

function CampaignStore.SaveFile(username, saveFile, isPrimary)
    Game:WriteDataFile(path(saveFileName(username)), saveFile)

    if isPrimary then
        Game:WriteDataFile(path("primary.sav"), saveFile)
    end
end

-- Moves the saved campaign aside when players start a new one, so it's never silently overwritten
function CampaignStore.Archive()
    local backup = CampaignStore.directory .. "-backup-" .. os.date("%Y%m%d-%H%M%S")

    if Game:MoveDirectory(CampaignStore.directory, backup) then
        print("Moved the previous co-op campaign to " .. backup)
    end
end
