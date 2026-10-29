require 'TestMoby'
require 'DebugView'

CoopPlayer = class("CoopPlayer", Player)

function CoopPlayer:Made()
    self.damageCooldown = 0
    self.goldBoltCount = 0
    self.ready = false
    self.ingame = false
    
    self.lobby = null
    
    self.clanky = null
    
    self.gameState = 0
    self.controlSince = 0
    self.pendingMobyDeletions = {}

    self.skillpointCounters = {
        Player.offset.aridiaShipsKilled,
        Player.offset.eudoraShipsKilled,
        Player.offset.gasparShipsKilled,
        Player.offset.pokitaruShipsKilled,
        Player.offset.hovenShipsKilled,
        Player.offset.oltanisShipsKilled,
        Player.offset.veldin2CommandosKilled,
    }
    
    for _, counter in ipairs(self.skillpointCounters) do
        self:MonitorAddress(counter, 4)
    end

    for i = 2, 26 do
        self:MonitorAddress(Player.offset.gildedItems + i, 1)
    end
    
    self:MonitorAddress(0x96BF88, 4)
    
    self:EnableManualSaveLoad()

    -- Let the game unlock planets right away instead of waiting for the server to echo the unlock back. Scripted
    --  sequences that check the unlock straight after (likely Gemlik's bridges after the Qwark fight) would otherwise
    --  run before the echo arrives. The game still reports unlocks, so they're shared with the other player as before.
    self:SetCommunicationFlags(Player.communicationFlags.ENABLE_ON_UNLOCK_ITEM |
            Player.communicationFlags.ENABLE_ON_PICKUP_GOLD_BOLT |
            Player.communicationFlags.ENABLE_ON_GET_BOLTS)

    self.saveMessageCountdown = 0
    
    self.respawnsThisLevel = 0
    self.lastRespawnLevel = -1
    self.allowRespawn = false

    self.restoring = false
    self.restoreStartedAt = 0
    self.restoreRespawnedAt = nil

    self.debugView = nil
end

function CoopPlayer:Save()
    -- Not before the campaign is back in their game: they'd save what their old save had
    if self.ingame and not self.restoring then
        self:SaveGame()
    end
end

-- A player who continues the campaign (rejoining a game, or everyone continuing it after a restart) loads their own
--  last save, which can be much older than the campaign: the other player kept playing, or their game crashed. And
--  loading it puts back what that save had, over what the server gave them when they started: the save arrives after
--  that. Their game then reported its save's bolt count, a drop, and everyone lost the bolts collected since; and
--  their kill counts went back for everyone the same way. So until the save's level has loaded and they've been in
--  control for a moment, what their game reports from the save isn't shared, and then the campaign is given again.
local RESTORE_AFTER_CONTROL_TICKS = 30
-- In case the game never tells us it spawned
local RESTORE_GIVE_UP_TICKS = 60 * 60 * 3

function CoopPlayer:GiveCampaign()
    local lobby = self.lobby

    self:SetBolts(lobby.bolts)

    for _, item in ipairs(lobby.unlockedItems) do
        self:GiveItem(item, false)
    end

    for _, infobot in ipairs(lobby.unlockedInfobots) do
        if infobot ~= 0 then
            self:UnlockLevel(infobot)
        end
    end

    for _, skillpoint in ipairs(lobby.unlockedSkillpoints) do
        self:UnlockSkillpoint(skillpoint)
    end

    for _, bolt in ipairs(lobby.goldBolts) do
        self:SetAddressValue(Player.offset.goldBolts + bolt.planet * 4 + bolt.number, 1, 1)
        self:BlockGoldBolt(bolt.planet, bolt.number)
    end

    for address, _ in pairs(lobby.gildedItems) do
        self:SetAddressValue(address, 1, 1)
    end

    for address, value in pairs(lobby.counters) do
        self:SetAddressValue(address, value, 4)
    end
end

function CoopPlayer:FinishRestoring()
    if not self.restoring then
        return
    end

    local ticks = self:Ticks()
    local loaded = self.restoreRespawnedAt ~= nil and self.gameState == GameState.PlayerControl and
            ticks - math.max(self.restoreRespawnedAt, self.controlSince) >= RESTORE_AFTER_CONTROL_TICKS

    if not loaded and ticks - self.restoreStartedAt < RESTORE_GIVE_UP_TICKS then
        return
    end

    if not loaded then
        print(self:Username() .. "'s game didn't say it loaded its save, giving them the campaign anyway")
    end

    self.restoring = false
    self:GiveCampaign()
    -- And their save on the server is the campaign's again, not the old one they loaded
    self:Save()
end

-- Deleting an NPC or item while this player is watching a cutscene that uses it freezes their game (e.g. one player
--  skipping the Plumber cutscene while the other is still in it). Deletions wait until the player is back in control,
--  and has been for a second: a conversation hands back control for a moment between its parts, and the game's
--  report that a cutscene started can come after what happened in it.
local DELETE_AFTER_CONTROL_TICKS = 60

function CoopPlayer:DeleteWorldMoby(oClass)
    table.insert(self.pendingMobyDeletions, oClass)
    self:DeletePendingMobys()
end

function CoopPlayer:DeletePendingMobys()
    if #self.pendingMobyDeletions == 0 or self.gameState ~= GameState.PlayerControl or
            self:Ticks() - self.controlSince < DELETE_AFTER_CONTROL_TICKS then
        return
    end

    local pending = self.pendingMobyDeletions
    self.pendingMobyDeletions = {}

    for _, oClass in ipairs(pending) do
        self:DeleteAllChildrenWithOClass(oClass)
    end
end

-- Every player's lobby screen has to close when the game starts, not just the host's. Otherwise it stays on top of
--  the guest's game, and its only way out (Triangle) leaves the lobby, so nothing syncs for them anymore.
function CoopPlayer:CloseLobbyView()
    if self.lobbyView ~= nil then
        local view = self.lobbyView
        self.lobbyView = nil
        view:CloseView()
    end
end

function CoopPlayer:Start()
    self.ingame = true
    self.ready = true

    if self.lobby.options.debugStart.value then
        self.debugView = DebugView(self)
        self:AddEntity(self.debugView)
    end

    if not self.lobby.started and not self.lobby.resumed then
        self:LoadLevel(self.lobby.options.startPlanet.value)
    else
       -- Try to load an existing save file if it exists.
        local saveFile = self.lobby.saveFiles[self:Username()]

        if saveFile == nil then
            saveFile = self.lobby.primarySaveFile
        end

        if saveFile ~= nil then
            self:SendFile(saveFile)
            self:LoadSaveFile()

            self:ToastMessage("Load from existing save", 60 * 5)
        else
            -- Load last unlocked level
            self:LoadLevel(self.lobby.unlockedInfobots[#self.lobby.unlockedInfobots])
        end

        self:GiveCampaign()

        self.restoring = true
        self.restoreStartedAt = self:Ticks()
        self.restoreRespawnedAt = nil
    end
end

function CoopPlayer:OnCollectedGoldBolt(planet, number)
    print("Player collected gold bolt on " .. planet .. " number: " .. number);

    -- Already the campaign's: their save was older than when it was taken
    if not self.lobby:AddGoldBolt(planet, number) then
        return
    end

    for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
        if player:GUID() ~= self:GUID() then
            player:SetAddressValue(Player.offset.goldBolts + planet * 4 + number, 1, 1)
            -- And it's gone from their level too, instead of floating there to be picked up again
            player:BlockGoldBolt(planet, number)
            player:ToastMessage("\x0cGold Bolt\x08 acquired", 60*5)
        end
    end
    
    self:Save()
end

function CoopPlayer:OnRespawned()
    if self.restoring then
        -- Spawning in the level their save took them to, not a death
        if self.restoreRespawnedAt == nil then
            self.restoreRespawnedAt = self:Ticks()
        end
        return
    end

    if self.lobby.options.deathLink.value then
        if not self.allowRespawn then
            print("Seems like a player died. Killing everyone.")
            for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
                if player:GUID() ~= self:GUID() then
                    player.allowRespawn = true
                    player:SetAddressValue(0x96BF88, 0, 4)
                end
            end
        end
        
        self.allowRespawn = false
    end
end

function CoopPlayer:UnlockAllGoldBolts()
    for i = 0, 17 do
        for j = 0, 3 do
            self:SetAddressValue(Player.offset.goldBolts + i * 4 + j, 1, 1)
        end
    end
end

function CoopPlayer:MonitoredAddressChanged(address, oldValue, newValue)
    local addressIsSkillpointCounter = false
    for _, counter in ipairs(self.skillpointCounters) do
        if address == counter then
            addressIsSkillpointCounter = true
            break
        end
    end

    -- Before they've started, it's whatever their game had in memory, maybe from another campaign
    if not self.ingame then
        return
    end

    if address == 0x96BF88 then
        if self.lobby.options.deathLink.value and not self.restoring then
            print("" .. self:Username() .. " went from " .. oldValue .. " to " .. newValue .. " nanotech")
            for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
                if newValue == 0 then
                    player.allowRespawn = true
                end
                
                if player:GUID() ~= self:GUID() then
                    player:SetAddressValue(0x96BF88, newValue, 4)
                end
            end
        end
    end
    
    if addressIsSkillpointCounter then
        local campaignValue = self.lobby:CounterChanged(address, newValue)

        if newValue < campaignValue then
            -- From a save older than the campaign. It used to be sent to the others, lowering theirs.
            self:SetAddressValue(address, campaignValue, 4)
        elseif newValue > oldValue then
            for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
                if player:GUID() ~= self:GUID() then
                    player:SetAddressValue(address, newValue, 4)
                end
            end

            self:Save()
        end
    end

    if address >= Player.offset.gildedItems and address <= Player.offset.gildedItems + 35 then
        local itemIndex = address - Player.offset.gildedItems + 2

        if newValue ~= 0 then
            self.lobby:AddGildedItem(address)

            for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
                if player:GUID() ~= self:GUID() then
                    player:SetAddressValue(address, 1, 1)
                end
            end
        end
        
        self:Save()
    end
end

function CoopPlayer:OnAttack(moby, sourceOClass, damage)
    if moby == nil then
        return
    end
    
    if not self.lobby.options.friendlyFire.value then
        return
    end
    
    if self.damageCooldown <= 0 then
        moby:Damage(1)
        self.damageCooldown = 40
    end
end

function CoopPlayer:Unfreeze()
    self.state = 0
end

function CoopPlayer:OnTick()
    if self.forceGhostRatchet and self:Ticks() % 4 == 0 then
        self:SetGhostRatchet(150)
    end

    if self.infiniteHealth then
        self:SetAddressValue(0x96BF88, 4, 4)
    end
    
    if self.saveMessageCountdown >= 0 then
        self.saveMessageCountdown = self.saveMessageCountdown - 1

        if self.saveMessageCountdown <= 0 then
            self:ToastMessage("Game saved", 60 * 3)
        end
    end
    
    --if self.clanky == null then
    --    self.clanky = self:SpawnInstanced(TestMoby)
    --    self.clanky:SetPosition(self.x, self.y, self.z + 2)
    --end
    --
    --self.clanky:SetPosition(self.x, self.y, self.z + 2)
    
    if (self.damageCooldown > 0) then
        self.damageCooldown = self.damageCooldown - 1
    end

    self:DeletePendingMobys()
    self:FinishRestoring()
end

function CoopPlayer:OnGameStateChanged(state)
    self.gameState = state

    if state == 6 then
        self.allowRespawn = true

        -- Pending deletions were for the level we're leaving
        self.pendingMobyDeletions = {}
    end

    if state == GameState.PlayerControl then
        self.controlSince = self:Ticks()
    end
end

function CoopPlayer:OnControllerInputTapped(input)
    --if (self.debugCam or self.gameState == 3) and input & 1024 ~= 0 then
    --    if not self.debugCam then
    --        self:SetAddressValue(0x95c5d4, 1, 4)
    --        self:SetAddressValue(0x95c5c8, 6, 4)
    --        self.debugCam = true
    --    else
    --        self:SetAddressValue(0x95c5d4, 0, 4)
    --        self:SetAddressValue(0x95c5c8, 0xf, 4)
    --        self.debugCam = false
    --    end
    --end
    
    --if self.gameState == 3 and input & 0x20 ~= 0 then
    --    self:SetPosition(0, 0, -10000)
    --    --self:SetAddressValue(0x969EAC, 100, 4)
    --end
    
    --if self.gameState == 3 and input & 0x80 ~= 0 then
    --    if self:Username() == "bordplate3" then
    --        print("Moving players")
    --        local z_pos = self.z + 1
    --        for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
    --            if player:GUID() ~= self:GUID() then
    --                print("Moved player " .. player:Username() .. " to " .. self.x .. ", " .. self.y .. ", " .. z_pos)
    --                player:SetPosition(self.x, self.y, z_pos)
    --                z_pos = z_pos + 1
    --            end
    --        end
    --    end
    --end
end

function CoopPlayer:OnUnlockItem(item_id, equip)
    item = Item.GetById(item_id)
    self:Save()
    
    print("Unlocking item " .. item.name)

    self:GiveItem(item.id, equip)
    
    self.lobby:AddUnlockedItem(item.id)

    for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
        if player:Username() ~= self:Username() then
            if item.isWeapon then
                player:ToastMessage("You've purchased a \x0c" .. item.name .. "\x08!", 60*5)
            end
            
            print("Giving " .. item.name .. " to player " .. player:Username())
            player:GiveItem(item.id, false)

            if player:Level() == self:Level() then
                if item.name == "Swingshot" then
                    player:DeleteWorldMoby(890)  -- Delete Helga
                end
                if item.name == "Suck Cannon" then
                    player:DeleteWorldMoby(1120)  -- Delete Suck Cannon
                end
                if item.name == "Grindboots" then
                    player:DeleteWorldMoby(1190)  -- Delete Fred
                end
                if item.name == "Trespasser" then
                    player:DeleteWorldMoby(1005)  -- Delete Trespasser
                end
                if item.name == "Hydrodisplacer" then
                    player:DeleteWorldMoby(1016)  -- Delete Hydrodisplacer
                end
                if item.name == "Metal Detector" then
                    player:DeleteWorldMoby(1283)  -- Delete Plumber
                end
                if item.name == "Pilot's Helmet" then
                    player:DeleteWorldMoby(1290)  -- Delete Pilot's Helmet
                end
                if item.name == "Magneboots" then
                    player:DeleteWorldMoby(18)  -- Delete Magneboots
                end
                if item.name == "Morph-o-Ray" then
                    player:DeleteWorldMoby(1354)  -- Delete Morph-o-Ray
                end
                if item.name == "Codebot" then
                    player:DeleteWorldMoby(1428)  -- Delete Codebot
                end
            end
        end
    end
end

function CoopPlayer:OnUnlockLevel(level)
    Player.OnUnlockLevel(self, level)
    self:Save()
    
    self.lobby:AddUnlockedInfobot(level)
    
    for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
        player:UnlockLevel(level)

        -- Not in the game of the player who got it: that's still going through it, the game takes care of its own
        --  (Eudora's Fred, deleted between the parts of his conversation, left that player on a black screen)
        if player:Level() == self:Level() and player:GUID() ~= self:GUID() then
            if level == 2 then  -- Aridia
                player:DeleteWorldMoby(774)  -- Delete Plumber
            end
            if level == 6 then  -- Blarg
                player:DeleteWorldMoby(1190)  -- Delete Lietuenant
            end
            if level == 5 then  -- Rilgar
                player:DeleteWorldMoby(750)  -- Delete infobot
            end
            if level == 7 then  -- Umbris
                player:DeleteWorldMoby(919)  -- Delete Bouncer
            end
            if level == 8 then  -- Batalia
                player:DeleteWorldMoby(750)  -- Delete infobot
            end
            if level == 9 then  -- Gaspar
                player:DeleteWorldMoby(1144)  -- Delete Deserter
            end
            if level == 10 then  -- Orxon
                player:DeleteWorldMoby(1130)  -- Delete Commando
            end
        end
    end
end

function CoopPlayer:OnUnlockSkillpoint(skillpoint)
    self:UnlockSkillpoint(skillpoint)
    
    if self.lobby:HasSkillpoint(skillpoint) then
        return
    end
    
    self.lobby:AddUnlockedSkillpoint(skillpoint)
    self:Save()
    
    for _, player in ipairs(self.lobby.players) do
        player:UnlockSkillpoint(skillpoint)
    end
end


function CoopPlayer:OnGiveBolts(boltDiff, totalBolts)
    -- Their save's bolt count, not bolts they picked up: they get the campaign's count once it's loaded
    if self.restoring then
        return
    end

    self.totalBolts = totalBolts
    self.lobby:BoltsChanged(totalBolts)
    for _, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
        if player ~= self then
            player:GiveBolts(boltDiff)
        end
    end
end

function CoopPlayer:OnDisconnect()
    if self.lobby ~= null then
        self.lobby:Leave(self, true)
    end
end

function CoopPlayer:OnLevelFlagChanged(flagType, level, size, index, value)
    if self.ingame then
        self.lobby:LevelFlagChanged(level, flagType, index, value)
    end
end

function CoopPlayer:DownloadedFile(file)
    if self.restoring then
        -- What they loaded, saved before the campaign is back in their game (the game saves when it lands on a
        --  planet). If their save didn't load, it'd be an empty game over their real save.
        print("Not keeping the save " .. self:Username() .. " sent while their game was still loading theirs")
        return
    end

    self.saveMessageCountdown = 60 * 8

    self.lobby:PlayerSentSaveFile(self, file)

    for i, player in ipairs(self:Universe():LuaEntity():FindChildren("Player")) do
        -- Not to players loading their own save: whichever arrived last is the one their game would load
        if player:GUID() == self:GUID() or (player.ingame and not player.restoring) then
            player:SendFile(file)
        end
    end
end
