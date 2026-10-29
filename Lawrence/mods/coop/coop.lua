require 'CoopPlayer'
require 'LobbyListView'

local LobbyUniverse = class("LobbyUniverse", Universe)
function LobbyUniverse:initialize()
    Universe.initialize(self)
    
    self.lobbies = ObservableList({})
end
 
function LobbyUniverse:OnPlayerJoin(player)
    local lobby = LobbyListView(player, self)
    player:AddEntity(lobby)
end

function LobbyUniverse:NewLobby(host, password)
    local lobby = Lobby(host, password)
    self.lobbies:Add(lobby)
    
    lobby:Join(host)
    
    return lobby
end

function LobbyUniverse:RemoveLobby(lobby)
    self.lobbies:Remove(lobby)
end

-- The started lobby other than `except` that's playing the campaign, if any
function LobbyUniverse:RunningLobby(except)
    for _, lobby in ipairs(self.lobbies) do
        if lobby ~= except and lobby.started and #lobby.players > 0 then
            return lobby
        end
    end

    return nil
end

function LobbyUniverse:OnTick()
    for _, lobby in ipairs(self.lobbies) do
        -- About once a second while it changes, see Lobby:MarkDirty
        if lobby.started and lobby.dirty and Game:Ticks() % 60 == 0 then
            CampaignStore.Save(lobby)
        end
    end

    -- Everything the players share is on disk by now (Lobby:Leave), so a lobby closes as soon as everyone's left
    local empty = {}
    for _, lobby in ipairs(self.lobbies) do
        if #lobby.players <= 0 then
            table.insert(empty, lobby)
        end
    end

    for _, lobby in ipairs(empty) do
        print("Closing lobby " .. lobby.lobbyName .. ": everyone left" ..
                (lobby.started and ", the campaign is saved" or ""))
        lobby:Close()
    end
end

lobbyUniverse = LobbyUniverse:new()
lobbyUniverse:Start(true)
