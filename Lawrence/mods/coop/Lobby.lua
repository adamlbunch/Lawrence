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
    self.resumed = false
    self.lastCampaignSave = 0
    
    self.readyCallbacks = {}
    
    self.bolts = 0
    self.unlockedInfobots = {}
    self.unlockedSkillpoints = {}
    self.unlockedItems = {}

    self.primarySaveFile = nil
    self.saveFiles = {}
    
    self.universe = CoopUniverse(self)
    
    self.inactiveTimer = 0
end

function Lobby:AddReadyCallback(callback)
    table.insert(self.readyCallbacks, callback)
end

function Lobby:Start()
    if self.options.campaign.value and self.savedCampaign ~= nil then
        local campaign = self.savedCampaign

        self.bolts = campaign.bolts or 0
        self.unlockedInfobots = campaign.unlockedInfobots or {0}
        self.unlockedItems = campaign.unlockedItems or {}
        self.unlockedSkillpoints = campaign.unlockedSkillpoints or {}
        self.saveFiles = campaign.saveFiles
        self.primarySaveFile = campaign.primarySaveFile
        self.resumed = true

        print("Continuing the saved co-op campaign from " .. tostring(campaign.savedAt))
    else
        if self.savedCampaign ~= nil then
            CampaignStore.Archive()
        end

        self.unlockedInfobots = {self.options.startPlanet.value}
    end

    self.savedCampaign = nil

    print("Starting lobby for: ")
    for i, player in ipairs(self.players) do
        print("  " .. player:Username())
        
        player:Start()
    end

    self.started = true
end

function Lobby:Join(player)
    player = player:Make(CoopPlayer)
    
    player.lobby = self
    self.players:Add(player)
    self.universe:AddEntity(player)
    
    player:AddEntity(LobbyView(player, self))
    
    self.inactiveTimer = 0
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
end

function Lobby:PlayerReady(player)
    player.ready = not player.ready
    
    for i, callback in ipairs(self.readyCallbacks) do
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

function Lobby:AddUnlockedItem(item_id)
    table.insert(self.unlockedItems, item_id)
    CampaignStore.Save(self)
end

function Lobby:AddUnlockedInfobot(infobot_id)
    table.insert(self.unlockedInfobots, infobot_id)
    CampaignStore.Save(self)
end

function Lobby:AddUnlockedSkillpoint(skillpoint_id)
    table.insert(self.unlockedSkillpoints, skillpoint_id)
    CampaignStore.Save(self)
end

-- Bolts change constantly, so they're written at most every 10 seconds (and whenever anything else is saved)
function Lobby:BoltsChanged(totalBolts)
    self.bolts = totalBolts

    if os.time() - self.lastCampaignSave >= 10 then
        CampaignStore.Save(self)
    end
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
    lobbyUniverse:RemoveLobby(self)
    self.universe:Delete()
end
