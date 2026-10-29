require 'CoopOptions'
require 'CoopUniverse'
require 'CampaignStore'

Lobby = class("Lobby")

function Lobby:initialize(host, password)
    self.host = host
    self.lobbyName = host:Username()
    self.password = password
    
    self.started = false
    
    self.players = ObservableList({})

    self.options = CoopOptions({
        password = {
            name = "Password",
            description = "Password for the lobby. Leave blank for public.",
            handler = function(self, view, item) view.passwordInput:Activate() end,
            value = password,
        },
        friendlyFire = {
            name = "Friendly fire",
            description = "When enabled lets players hurt each other with weapons and the wrench.",
            handler = function(option, view, item) option:set(not option.value) end,
            value = false,
            accessory = {"On", "Off"}
        },
        deathLink = {
            name = "Shared health",
            description = "Everyone shares health. When 1 player takes damage, everyone takes damage. When 1 player dies, everyone dies.",
            handler = function (option, view, item) option:set(not option.value) end,
            value = false,
            accessory = {"On", "Off"}
        },
        debugStart = {
            name = "Debug",
            description = "Lets you open a debug menu by pressing the left stick (L3).",
            handler = function(option, view, item) option:set(not option.value) end,
            value = false,
            accessory = {"On", "Off"}
        },
        startPlanet = {
            name = "Start Planet",
            description = "The planet to start the game on.",
            handler = function(option, view, item) option:set((option.value+1) % 19) end,
            value = 0,
            accessory = {
                "Veldin1",
                "Novalis",
                "Aridia",
                "Kerwan",
                "Eudora",
                "Rilgar",
                "BlargStation",
                "Umbris",
                "Batalia",
                "Gaspar",
                "Orxon",
                "Pokitaru",
                "Hoven",
                "GemlikStation",
                "Oltanis",
                "Quartu",
                "KaleboIII",
                "DreksFleet",
                "Veldin2",
            }
        },
        campaign = {
            name = "Campaign",
            description = "Continue the campaign saved on this server, or start a new one. The old campaign is kept " ..
                    "as a backup in the server's saves folder.",
            handler = function(option, view, item)
                if self.savedCampaign ~= nil then
                    option:set(not option.value)
                end
            end,
            value = false,
            accessory = {"Continue", "New game"}
        }
    })

    self.optionsList = {
        self.options.password,
        self.options.friendlyFire,
        self.options.deathLink,
        self.options.debugStart,
        self.options.startPlanet,
        self.options.campaign
    }

    self.savedCampaign = CampaignStore.Load()
    self.options.campaign.value = self.savedCampaign ~= nil
    if self.savedCampaign ~= nil and self.savedCampaign.options ~= nil then
        -- The settings the campaign was played with, for the host to keep or change
        self.options.friendlyFire.value = self.savedCampaign.options.friendlyFire == true
        self.options.deathLink.value = self.savedCampaign.options.deathLink == true
    end
    self.resumed = false
    self.dirty = false
    self.closed = false
    
    self.readyCallbacks = {}
    
    self.bolts = 0
    self.unlockedInfobots = {}
    self.unlockedSkillpoints = {}
    self.unlockedItems = {}
    self.goldBolts = {}      -- {planet, number} of every gold bolt taken
    self.gildedItems = {}    -- address -> true, of the gold weapons bought
    self.counters = {}       -- address -> highest value, of the kill counts skill points need
    self.levelFlags = {}     -- "level:type:index" -> {level, type, index, value}, of flags players changed

    self.primarySaveFile = nil
    self.saveFiles = {}
    
    self.universe = CoopUniverse(self)
end

function Lobby:AddReadyCallback(callback)
    table.insert(self.readyCallbacks, callback)
end

function Lobby:RemoveReadyCallback(callback)
    for i, v in ipairs(self.readyCallbacks) do
        if v == callback then
            table.remove(self.readyCallbacks, i)
            break
        end
    end
end

-- Returns why the game can't start, or nil once the campaign it's going to play is ready
function Lobby:PrepareStart()
    local running = lobbyUniverse:RunningLobby(self)
    if running ~= nil then
        -- Two games playing one campaign would each write it over the other's
        return running.lobbyName .. "'s game is already playing the campaign on this server. Leave this lobby " ..
                "(Triangle) and join theirs."
    end

    self.campaign = nil
    if self.options.campaign.value then
        -- Read again: the campaign may have moved on since this lobby opened (another game played it meanwhile)
        self.campaign = CampaignStore.Load()
        if self.campaign == nil then
            return "The saved campaign couldn't be read (see the server's log). Switch Campaign to New game to " ..
                    "start over: the old one is kept as a backup."
        end
    elseif not CampaignStore.Archive() then
        return "The old campaign couldn't be moved aside for a new one (see the server's log), so nothing was " ..
                "started. Close anything that has the server's saves folder open and try again."
    end

    self.prepared = true
    return nil
end

function Lobby:Start()
    if not self.prepared then
        local problem = self:PrepareStart()
        if problem ~= nil then
            print("Can't start " .. self.lobbyName .. "'s lobby: " .. problem)
            return
        end
    end
    self.prepared = false

    if self.campaign ~= nil then
        local campaign = self.campaign

        self.bolts = campaign.bolts or 0
        self.unlockedInfobots = campaign.unlockedInfobots or {0}
        self.unlockedItems = campaign.unlockedItems or {}
        self.unlockedSkillpoints = campaign.unlockedSkillpoints or {}
        self.goldBolts = campaign.goldBolts or {}
        for _, address in ipairs(campaign.gildedItems or {}) do
            self.gildedItems[address] = true
        end
        for _, counter in ipairs(campaign.counters or {}) do
            self.counters[counter.address] = counter.value
        end
        for _, flag in ipairs(campaign.levelFlags or {}) do
            self:LevelFlagChanged(flag.level, flag.type, flag.index, flag.value)

            local level = self.universe:GetLevelByGameID(flag.level)
            if level ~= nil then
                level:RestoreFlag(flag.type, flag.index, flag.value)
            end
        end
        self.saveFiles = campaign.saveFiles
        self.primarySaveFile = campaign.primarySaveFile
        self.resumed = true

        print("Continuing the saved co-op campaign from " .. tostring(campaign.savedAt))
    else
        self.unlockedInfobots = {self.options.startPlanet.value}
    end

    self.campaign = nil
    self.savedCampaign = nil

    print("Starting lobby for: ")
    for i, player in ipairs(self.players) do
        print("  " .. player:Username())

        player:CloseLobbyView()
        player:Start()
    end

    self.started = true
    CampaignStore.Save(self)
end

function Lobby:Join(player)
    player = player:Make(CoopPlayer)
    
    player.lobby = self
    self.players:Add(player)
    self.universe:AddEntity(player)
    
    player.lobbyView = LobbyView(player, self)
    player:AddEntity(player.lobbyView)
end

function Lobby:Leave(player, disconnecting)
    self.players:Remove(player)

    if player:GUID() == self.host:GUID() then
        if #self.players > 0 then
            self.host = self.players[1]
            self.lobbyName = self.host:Username()
            self.host:ToastMessage("You are now the host.", 300)
        end
    end

    if self.started then
        CampaignStore.Save(self)
    end

    -- A disconnected player doesn't need the lobby list, and giving it one keeps the player in memory forever
    if not disconnecting then
        lobbyUniverse:AddEntity(player)
    end

    -- Once everyone's gone the lobby closes (see LobbyUniverse:OnTick): the campaign is on disk, and whoever comes
    --  back continues it in a new lobby. An empty lobby kept open could be started again by one player while another
    --  continued the campaign in a lobby of their own, and both would write it.
end

function Lobby:PlayerReady(player)
    player.ready = not player.ready

    for i, callback in ipairs({table.unpack(self.readyCallbacks)}) do
        callback(player)
    end
end

function Lobby:AllPlayersReady()
    for i, player in ipairs(self.players) do
        if not player.ready and self.host:GUID() ~= player:GUID() then
            return false
        end
    end
    
    return true
end

local function contains(list, value)
    for _, v in ipairs(list) do
        if v == value then
            return true
        end
    end

    return false
end

function Lobby:AddUnlockedItem(item_id)
    -- The game can unlock an item it already has (it did, every time the campaign continued)
    if not contains(self.unlockedItems, item_id) then
        table.insert(self.unlockedItems, item_id)
        CampaignStore.Save(self)
    end
end

function Lobby:AddUnlockedInfobot(infobot_id)
    if not contains(self.unlockedInfobots, infobot_id) then
        table.insert(self.unlockedInfobots, infobot_id)
        CampaignStore.Save(self)
    end
end

function Lobby:AddUnlockedSkillpoint(skillpoint_id)
    table.insert(self.unlockedSkillpoints, skillpoint_id)
    CampaignStore.Save(self)
end

function Lobby:HasGoldBolt(planet, number)
    for _, bolt in ipairs(self.goldBolts) do
        if bolt.planet == planet and bolt.number == number then
            return true
        end
    end

    return false
end

-- Returns whether it's new to the campaign
function Lobby:AddGoldBolt(planet, number)
    if self:HasGoldBolt(planet, number) then
        return false
    end

    table.insert(self.goldBolts, {planet = planet, number = number})
    CampaignStore.Save(self)
    return true
end

function Lobby:AddGildedItem(address)
    if not self.gildedItems[address] then
        self.gildedItems[address] = true
        CampaignStore.Save(self)
    end
end

-- Kill counts only go up in the game, so a lower one comes from a save older than the campaign: the highest wins.
--  Returns the campaign's count.
function Lobby:CounterChanged(address, value)
    local highest = self.counters[address] or 0
    if value > highest then
        self.counters[address] = value
        self:MarkDirty()
        return value
    end

    return highest
end

function Lobby:LevelFlagChanged(level, flagType, index, value)
    self.levelFlags[level .. ":" .. flagType .. ":" .. index] = {level = level, type = flagType, index = index,
                                                                 value = value}
    self:MarkDirty()
end

-- For what changes too often to write every time (bolts, level flags): it's written within a second, see
--  LobbyUniverse:OnTick. Bolts used to be written at most every 10 seconds, and only with the next change.
function Lobby:MarkDirty()
    self.dirty = true
end

function Lobby:BoltsChanged(totalBolts)
    self.bolts = totalBolts
    self:MarkDirty()
end

function Lobby:HasSkillpoint(skillpointId)
    for _, id in ipairs(self.unlockedSkillpoints) do
        if id == skillpointId then
            return true
        end
    end
    
    return false
end

function Lobby:PlayerSentSaveFile(player, saveFile)
    self.saveFiles[player:Username()] = saveFile

    local isPrimary = self.host:Username() == player:Username()
    if isPrimary then
        print("Updated primary save file from " .. player:Username())
        self.primarySaveFile = saveFile
    end

    CampaignStore.SaveFile(player:Username(), saveFile, isPrimary)
    CampaignStore.Save(self)
end

function Lobby:Close()
    if self.closed then
        return
    end
    self.closed = true

    if self.started and self.dirty then
        CampaignStore.Save(self)
    end

    lobbyUniverse:RemoveLobby(self)
    self.universe:Delete()
end
