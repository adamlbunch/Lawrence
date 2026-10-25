"""
End-to-end tests for the co-op mod, run against a real Lawrence build with two fake clients.

    python -m unittest discover -s tests/harness -v

LAWRENCE_BUILD points at a Lawrence build folder (Lawrence.exe + mods/ + runtime/); defaults to the local publish output.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fake_client import (FakeClient, CIRCLE, CUTSCENE, PLAYER_CONTROL, UI_LIST_MENU, EVENT_ITEM_ACTIVATED,
                         STATE_PLANET, STATE_GIVE_BOLTS, STATE_COMMUNICATION_FLAGS, STATE_UNLOCK_LEVEL, STATE_SAVE_FILE_OPERATION,
                         SAVE_OPERATION_LOAD)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BUILD = os.environ.get('LAWRENCE_BUILD', os.path.join(REPO, 'Lawrence', 'bin', 'publish', 'win-x64'))
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')

NOVALIS = 1
ARIDIA = 2
PLUMBER_OCLASS = 774


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(('127.0.0.1', 0))
        return udp.getsockname()[1]


class LawrenceServer:
    def __init__(self, directory=None):
        self.port = free_port()
        self.dir = directory
        if self.dir is None:
            self.dir = tempfile.mkdtemp(prefix='lawrence-test-')
            for folder in ('mods', 'runtime'):
                shutil.copytree(os.path.join(BUILD, folder), os.path.join(self.dir, folder))

        with open(os.path.join(self.dir, 'settings.toml'), 'w') as settings:
            settings.write(f'''[Server]
name = "test"
port = {self.port}
max_players = 4
address = "127.0.0.1"
advertise = false
interactive = false

[Logger]
path = "lawrence.log"

[Mod.coop]
enabled = true
''')

        executable = os.path.join(BUILD, 'Lawrence.exe' if os.name == 'nt' else 'Lawrence')
        self.output_path = os.path.join(self.dir, 'stdout.log')
        self.output = open(self.output_path, 'w')
        self.process = subprocess.Popen([executable], cwd=self.dir, stdout=self.output, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL)

        deadline = time.time() + 30
        while 'Started Lawrence' not in self.log():
            if self.process.poll() is not None or time.time() > deadline:
                raise RuntimeError(f'Lawrence failed to start:\n{self.log()}')
            time.sleep(0.2)

    def log(self):
        self.output.flush()
        with open(self.output_path, errors='replace') as output:
            return output.read()

    def stop(self, keep_files=False):
        self.process.kill()
        self.process.wait()
        self.output.close()
        if not keep_files:
            shutil.rmtree(self.dir, ignore_errors=True)


class CoopTestCase(unittest.TestCase):
    def setUp(self):
        self.server = LawrenceServer()
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            if client.running:
                client.close()
        # Keep the server output of the last run of every test for debugging
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, f'{self.id()}.log'), 'w') as log:
            log.write(self.server.log())
        self.server.stop()

    def client(self, name):
        client = FakeClient(name, self.server.port)
        self.clients.append(client)
        client.connect()
        return client

    def create_lobby(self, host, password):
        host.input_responses['blank for public'] = password
        host.wait_for(lambda: host.has_text('Co-op Lobbies'), what='lobby list')
        host.press(CIRCLE)
        host.wait_for(lambda: host.has_text('Options'), what='lobby view')

    def join_lobby(self, player, password):
        player.input_responses['lobby password'] = password
        player.wait_for(lambda: player.has_text('Co-op Lobbies'), what='lobby list')
        lobby_list = player.wait_for(lambda: player.latest_element(UI_LIST_MENU), what='lobby list menu')
        player.ui_event(EVENT_ITEM_ACTIVATED, lobby_list, 0)

    def lobby_with_two_players(self, password='secret'):
        host, guest = self.client('alpha'), self.client('bravo')
        self.create_lobby(host, password)
        self.join_lobby(guest, password)
        guest.wait_for(lambda: guest.has_text('Options'), what='guest in lobby')
        return host, guest

    def start(self, host, guest):
        guest.press(CIRCLE)  # Ready
        time.sleep(0.3)
        host.press(CIRCLE)  # Start

    def start_game(self, password='secret'):
        host, guest = self.lobby_with_two_players(password)
        self.start(host, guest)

        for player in (host, guest):
            player.wait_for(lambda p=player: p.states(STATE_PLANET), what='load level')
            player.enter_level(NOVALIS)
        time.sleep(0.5)
        return host, guest

    def restart_server(self):
        for client in self.clients:
            client.close()
        self.clients = []
        directory = self.server.dir
        self.server.stop(keep_files=True)
        self.server = LawrenceServer(directory)


class LobbyTests(CoopTestCase):
    def test_password_protected_lobby(self):
        host, guest = self.client('alpha'), self.client('bravo')
        self.create_lobby(host, 'secret')

        self.join_lobby(guest, 'wrong')
        guest.wait_for(lambda: any('Incorrect password' in m for m in guest.error_messages()), what='rejection')
        self.assertFalse(guest.has_text('Options'), 'guest got into the lobby with a wrong password')

        self.join_lobby(guest, 'secret')
        guest.wait_for(lambda: guest.has_text('Options'), what='guest in lobby')

    def test_both_players_start_campaign(self):
        host, guest = self.start_game()
        self.assertEqual(host.states(STATE_PLANET)[-1][1], 0)
        self.assertEqual(guest.states(STATE_PLANET)[-1][1], 0)


class CampaignSyncTests(CoopTestCase):
    def test_game_unlocks_planets_without_waiting_for_server(self):
        host, guest = self.start_game()
        for player in (host, guest):
            # ENABLE_ON_UNLOCK_ITEM | ENABLE_ON_PICKUP_GOLD_BOLT | ENABLE_ON_GET_BOLTS, without ENABLE_ON_UNLOCK_LEVEL
            self.assertEqual(player.states(STATE_COMMUNICATION_FLAGS)[-1][1], 0b1101)

    def test_unlocks_reach_other_player(self):
        host, guest = self.start_game()
        mark = guest.mark()

        host.set_state(STATE_UNLOCK_LEVEL, ARIDIA)

        guest.wait_for(lambda: (0, ARIDIA) in guest.states(STATE_UNLOCK_LEVEL, mark), what='Aridia unlock')

    def test_npc_not_deleted_during_cutscene(self):
        """Lawrence#38: one player finishing the Plumber cutscene froze the other player still watching it."""
        host, guest = self.start_game()
        guest.set_game_state(CUTSCENE)
        time.sleep(0.3)
        host_mark, guest_mark = host.mark(), guest.mark()

        host.set_state(STATE_UNLOCK_LEVEL, ARIDIA)

        host.wait_for(lambda: PLUMBER_OCLASS in host.deleted_oclasses(host_mark), what='Plumber deleted for host')
        guest.wait_for(lambda: (0, ARIDIA) in guest.states(STATE_UNLOCK_LEVEL, guest_mark), what='Aridia unlock')
        time.sleep(1)
        self.assertNotIn(PLUMBER_OCLASS, guest.deleted_oclasses(guest_mark),
                         'Plumber was deleted while the guest was still in the cutscene')

        guest.set_game_state(PLAYER_CONTROL)
        guest.wait_for(lambda: PLUMBER_OCLASS in guest.deleted_oclasses(guest_mark), what='deferred Plumber delete')

    def test_no_lost_or_duplicated_packets_after_stall(self):
        """Packets piling up while a client isn't acking (movies, loading, lag spikes) were dropped past 256."""
        host, guest = self.start_game()
        mark = guest.mark()
        guest.frozen = True

        payouts = 600
        for i in range(payouts):
            host.set_state(STATE_GIVE_BOLTS, 1, offset=i + 1)
            if i % 25 == 24:
                time.sleep(0.05)
        time.sleep(3)

        guest.frozen = False

        def received_all():
            return len([value for offset, value in guest.states(STATE_GIVE_BOLTS, mark) if offset == 0]) >= payouts

        try:
            guest.wait_for(received_all, timeout=60, what='all bolt payouts')
        except TimeoutError:
            pass
        time.sleep(2)  # Give duplicates a chance to show up

        got = [value for offset, value in guest.states(STATE_GIVE_BOLTS, mark) if offset == 0]
        self.assertEqual(sum(got), payouts, f'guest got {len(got)} payouts totalling {sum(got)} bolts, expected '
                                            f'{payouts}')

    def test_level_flags_reach_late_arrivals_without_clobbering(self):
        """Level flags were sent to every index-0 slot, overwriting a player's first 8 flags on each level entry."""
        host, guest = self.start_game()
        guest.enter_level(ARIDIA)
        time.sleep(0.5)

        host.change_level_flag(NOVALIS, 2, 113, 3)
        time.sleep(0.5)
        mark = guest.mark()
        guest.enter_level(NOVALIS)

        guest.wait_for(lambda: (NOVALIS, 2, 113, 3) in guest.level_flags(mark), what='flag sent on entering Novalis')
        time.sleep(0.5)
        self.assertEqual(guest.level_flags(mark), [(NOVALIS, 2, 113, 3)])

    def test_revisiting_levels_does_not_duplicate_ui(self):
        """Every level visit left a handler on the level's view, so later UI was sent once per past visit."""
        host, guest = self.start_game()
        for _ in range(5):
            guest.enter_level(ARIDIA)
            time.sleep(0.3)
            guest.enter_level(NOVALIS)
            time.sleep(0.3)

        host.enter_level(ARIDIA)
        time.sleep(0.5)
        mark = guest.mark()
        host.enter_level(NOVALIS)  # The host's nametag is added to Novalis' view again
        time.sleep(1.5)

        self.assertEqual(guest.ui_creates_with_text('alpha', mark), 1)

    def test_rejoin_started_game(self):
        host, guest = self.start_game()
        guest.disconnect()
        time.sleep(1)

        guest = self.client('bravo')
        self.join_lobby(guest, 'secret')
        guest.wait_for(lambda: guest.has_text('Options'), what='rejoined lobby')
        mark = guest.mark()
        guest.press(CIRCLE)
        guest.wait_for(lambda: guest.states(STATE_PLANET, mark), what='load level after rejoin')


class CampaignPersistenceTests(CoopTestCase):
    def play_and_save(self):
        host, guest = self.start_game()
        host.set_state(STATE_UNLOCK_LEVEL, ARIDIA)  # Unlocks make the player save

        campaign = os.path.join(self.server.dir, 'saves', 'coop')
        primary = os.path.join(campaign, 'primary.sav')

        def saved():
            if not os.path.exists(primary):
                return False
            with open(primary, 'rb') as save:
                return save.read() == host.save_data

        host.wait_for(saved, what='save file written to disk')
        with open(os.path.join(campaign, 'campaign.json')) as state_file:
            state = json.load(state_file)
        self.assertIn(ARIDIA, state['unlockedInfobots'])
        return host, guest

    def test_campaign_continues_after_server_restart(self):
        original_host, _ = self.play_and_save()
        self.restart_server()

        host, guest = self.lobby_with_two_players()
        self.start(host, guest)

        for player in (host, guest):
            player.wait_for(lambda p=player: original_host.save_data in p.files_received, what='saved game sent')
            player.wait_for(lambda p=player: (0, SAVE_OPERATION_LOAD) in p.states(STATE_SAVE_FILE_OPERATION),
                            what='load saved game')
            player.wait_for(lambda p=player: (0, ARIDIA) in p.states(STATE_UNLOCK_LEVEL), what='unlocks restored')
            self.assertEqual(player.states(STATE_PLANET), [], 'started a new game instead of continuing')

    def test_new_game_keeps_backup_of_old_campaign(self):
        self.play_and_save()
        self.restart_server()

        host, guest = self.lobby_with_two_players()
        options = host.wait_for(lambda: host.latest_element(UI_LIST_MENU), what='options menu')
        host.ui_event(EVENT_ITEM_ACTIVATED, options, 5)  # Campaign: Continue -> New game
        time.sleep(0.5)
        self.start(host, guest)

        host.wait_for(lambda: host.states(STATE_PLANET), what='new game started')
        self.assertEqual(host.states(STATE_PLANET)[-1][1], 0)
        self.assertEqual(host.files_received, [])

        backups = glob.glob(os.path.join(self.server.dir, 'saves', 'coop-backup-*', 'primary.sav'))
        self.assertEqual(len(backups), 1, 'old campaign was not backed up')
        self.assertFalse(os.path.exists(os.path.join(self.server.dir, 'saves', 'coop', 'primary.sav')))


if __name__ == '__main__':
    unittest.main()
