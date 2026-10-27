# Lawrence: the Ratchet & Clank Co-op server (guide for AI agents)

**People:** give your AI agent https://github.com/adamlbunch/rac1-multiplayer instead. That's the setup guide, and it
builds this server for you.

**Agents:** this is the game server for [Ratchet & Clank Co-op](https://github.com/adamlbunch/rac1-multiplayer). Only
the **host** player runs it. The guest just connects to it. To set up co-op for a user, follow that repository's
README: its `setup/setup-server.ps1` clones this repository's `main`, builds it and writes everything below. Use this
page to check, fix or run the server by hand. If your web tool shortens long pages, read the plain text instead:
https://raw.githubusercontent.com/adamlbunch/Lawrence/main/README.md

## What it is

Lawrence is a game server for the rac1-multiplayer mod, written in C# (.NET 8) and scripted in Lua. Game modes are
Lua mods in `Lawrence/mods/`, and co-op is `mods/coop`. Players reach it with the mod's Direct IP over UDP and TCP,
port 2407 by default. There's no public co-op server: each co-op game runs on its host's PC, and the guest reaches it
over Tailscale.

## Build

Needs Git and a .NET SDK 8 or newer (`dotnet --list-sdks`). Ask the user before installing either.

```powershell
git clone https://github.com/adamlbunch/Lawrence "$HOME\src\Lawrence"
dotnet publish "$HOME\src\Lawrence\Lawrence" -c Release -f net8.0 -r win-x64 --self-contained -p:PublishSingleFile=true -o "$HOME\Emulators\Lawrence"
```

Check: `Lawrence.exe`, `lua54.dll`, `mods\coop` and `runtime` are in the output folder. This repository publishes no
releases, so always build it from source.

## Settings

`settings.toml` sits next to `Lawrence.exe`. Write it **before the first start**: without a server name, the server
stops at a setup prompt. Write it with `[IO.File]::WriteAllText` (UTF-8 without a byte order mark). For co-op:

```toml
[Server]
name = "Ratchet Co-op"
port = 2407
max_players = 4
address = "0.0.0.0"
# Keep this server out of the public server list. Friends join with Direct IP.
advertise = false
# Plain console log instead of the full-screen UI
interactive = false

[Logger]
path = "lawrence.log"

[Mod.coop]
enabled = true

# Only co-op is used. Listing the rest stops the server from rewriting this file.
[Mod.hide-seek]
enabled = false

[Mod.mgb]
enabled = false

[Mod.race]
enabled = false

[Mod.randomizer]
enabled = false

[Mod.sandbox]
enabled = false

[Mod.tag]
enabled = false
```

## Running

- Start `Lawrence.exe` from its own folder, or the `Start Co-op Server.cmd` that the setup script writes. Close its
  window to stop it.
- Check: `Select-String -Path lawrence.log -Pattern 'Started Lawrence'` shows `Started Lawrence on 0.0.0.0:2407`.
- Firewall: the first start makes Windows ask whether to allow `Lawrence.exe`, and the user clicks Allow. Or, with the
  user's OK, from an administrator PowerShell:
  `New-NetFirewallRule -DisplayName "Ratchet Co-op Server" -Direction Inbound -Program "<folder>\Lawrence.exe" -Action Allow`
- The co-op campaign is saved in `saves\coop` and continues across restarts. Copy it somewhere before anything that
  could lose it.
- Updating: stop the server, `git pull` in the clone, and run the same `dotnet publish` into the same folder.
  `settings.toml` and `saves` stay. A clone still on the old `coop-fixes` branch (it's gone) needs
  `git fetch --prune` and `git switch main` first. Both players must also be on the same release of the mod.

## Troubleshooting

| Symptom | Cause |
|---|---|
| The server waits for "Server Name" | `settings.toml` is missing or has no `name` |
| Players can't connect | The server isn't running; the firewall; wrong IP or port; RPCS3's networking is off (see the mod's guide) |
| The server says a player's mod is out of date | That player has the official mod or an older release: update their mod |

## About this repository

It started as a fork of [bordplate/Lawrence](https://github.com/bordplate/Lawrence) and is now the co-op project's
server. The original as it was when forked is on the `upstream-main` branch. To change the server's code, read
[AGENTS.md](AGENTS.md). Neither the original nor this fork has a license, so this fork doesn't publish builds.
