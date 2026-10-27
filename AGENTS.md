# Working on this repository: notes for AI agents

Setting up or running the server for a user? Follow [README.md](README.md) instead. This file is for changing the
server's code.

## Branches

- `main` is the co-op server. Every push and pull request runs the build check in
  [build.yml](.github/workflows/build.yml), which builds the way the setup script does and publishes nothing.
- The mod's setup script builds this repository's `main`, so whatever is pushed there reaches every host on their next
  setup or update.
- `upstream-main` is [bordplate/Lawrence](https://github.com/bordplate/Lawrence) as it was when this was forked. The
  other branches (`coop`, `coop-puzzles`, `wip`) are the original's.
- The mod is [adamlbunch/rac1-multiplayer](https://github.com/adamlbunch/rac1-multiplayer). The packets the two
  exchange are defined on both sides (`Lawrence/Core/Packet.cs` here, `prx/rc1/multiplayer/network/Packet.h` there),
  so a change to one needs the same change in the other.

## Layout

- `Lawrence/Core/`: networking. The server, clients, packets and their handlers, settings, and the public server
  directory.
- `Lawrence/Game/`: the game model the Lua mods drive: universe, levels, players, mobys, entities and menus.
- `Lawrence/UI/`: the full-screen console UI, off with `interactive = false`.
- `Lawrence/mods/`: the game modes, in Lua. Each has a `config.toml` naming its entry script. Co-op is `mods/coop`.
- `Lawrence/runtime/`: Lua the mods share: `lib/` wraps the game model, `levels/` has per-planet scripts.
- `tests/harness/`: end-to-end co-op tests that run a real build with two fake clients.
- `scripts/wireshark-dissector/`: a Wireshark dissector for the protocol.

## Building and testing

```powershell
dotnet publish Lawrence -c Release -f net8.0 -r win-x64 --self-contained -p:PublishSingleFile=true -o Lawrence/bin/publish/win-x64
python -m unittest discover -s tests/harness -v
```

The tests use the build in `Lawrence/bin/publish/win-x64`, or the folder in `LAWRENCE_BUILD`. A change that touches
syncing also needs a real test: two RPCS3 games with the mod, connected to the new build.
