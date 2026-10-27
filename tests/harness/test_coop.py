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
import struct
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fake_client import (FakeClient, MOBY_UPDATE, CIRCLE, CUTSCENE, MOVIE, PLAYER_CONTROL, UI_LIST_MENU, EVENT_ITEM_ACTIVATED,
                         STATE_PLANET, STATE_GIVE_BOLTS, STATE_COMMUNICATION_FLAGS, STATE_UNLOCK_LEVEL, STATE_SAVE_FILE_OPERATION,
                         SAVE_OPERATION_LOAD, LOADING, MOBY_CREATE, MOBY_EX, PLAYER_STATE, MOBY_HIT, API_VERSION_MIRRORING,
                         API_VERSION_ENEMY_SYNC, STATE_COLLECTED_GOLD_BOLT, STATE_BLOCK_GOLD_BOLT, SET_STATE)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
BUILD = os.environ.get('LAWRENCE_BUILD', os.path.join(REPO, 'Lawrence', 'bin', 'publish', 'win-x64'))
LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')

NOVALIS = 1
ARIDIA = 2
PLUMBER_OCLASS = 774


def free_port():
    # The server needs the port for both UDP and TCP, and Windows reserves some TCP ranges, so check both
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.bind(('127.0.0.1', 0))
            port = udp.getsockname()[1]
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp:
                tcp.bind(('127.0.0.1', port))
            return port
        except OSError:
            continue


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
        self.client_options = {}  # username -> FakeClient keyword arguments

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
        client = FakeClient(name, self.server.port, **self.client_options.get(name, {}))
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

        guest.wait_for(lambda: (0, ARIDIA) in guest.states(STATE_UNLOCK_LEVEL, guest_mark), what='Aridia unlock')
        time.sleep(1)
        self.assertNotIn(PLUMBER_OCLASS, guest.deleted_oclasses(guest_mark),
                         'Plumber was deleted while the guest was still in the cutscene')

        guest.set_game_state(PLAYER_CONTROL)
        guest.wait_for(lambda: PLUMBER_OCLASS in guest.deleted_oclasses(guest_mark), what='deferred Plumber delete')
        self.assertNotIn(PLUMBER_OCLASS, host.deleted_oclasses(host_mark),
                         "Plumber was deleted in the game of the player who's talking to him")

    def test_npc_not_deleted_between_parts_of_a_conversation(self):
        """Eudora's Fred, deleted when his conversation handed back control for a moment, left a black screen."""
        host, guest = self.start_game()
        guest.set_game_state(CUTSCENE)
        time.sleep(0.3)
        guest_mark = guest.mark()

        host.set_state(STATE_UNLOCK_LEVEL, ARIDIA)
        guest.wait_for(lambda: (0, ARIDIA) in guest.states(STATE_UNLOCK_LEVEL, guest_mark), what='Aridia unlock')

        guest.set_game_state(PLAYER_CONTROL)
        time.sleep(0.2)
        guest.set_game_state(MOVIE)
        time.sleep(1.5)
        self.assertNotIn(PLUMBER_OCLASS, guest.deleted_oclasses(guest_mark),
                         'Plumber was deleted while control was only handed back for a moment')

        guest.set_game_state(PLAYER_CONTROL)
        guest.wait_for(lambda: PLUMBER_OCLASS in guest.deleted_oclasses(guest_mark), what='deferred Plumber delete')

    def test_reliable_packet_sent_again_is_taken_in_once(self):
        """A gold bolt the game reported again, because the server's ack hadn't reached it yet, was given twice."""
        host, guest = self.start_game()
        mark = guest.mark()

        ack = host.set_state(STATE_COLLECTED_GOLD_BOLT, 1, NOVALIS)
        host.wait_for(lambda: host.got_ack(*ack), what='ack for the gold bolt')
        host.send_again(SET_STATE, struct.pack('>HHII', 0, STATE_COLLECTED_GOLD_BOLT, NOVALIS, 1), *ack)

        guest.wait_for(lambda: (NOVALIS, 1) in guest.states(STATE_BLOCK_GOLD_BOLT, mark), what='gold bolt blocked')
        time.sleep(1)
        self.assertEqual(guest.states(STATE_BLOCK_GOLD_BOLT, mark).count((NOVALIS, 1)), 1,
                         'the gold bolt was taken in twice')

    def test_gold_bolt_taken_out_of_other_levels(self):
        host, guest = self.start_game()
        mark = guest.mark()

        host.set_state(STATE_COLLECTED_GOLD_BOLT, 2, NOVALIS)

        guest.wait_for(lambda: (NOVALIS, 2) in guest.states(STATE_BLOCK_GOLD_BOLT, mark), what='gold bolt blocked')
        self.assertNotIn((NOVALIS, 2), host.states(STATE_BLOCK_GOLD_BOLT), 'gold bolt blocked for the collector')

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

    def test_stuck_menu_clears_on_level_load(self):
        """A lost UI delete left the lobby browser stuck on a player's screen over the game until they reconnected."""
        host, guest = self.client('alpha'), self.client('bravo')
        self.create_lobby(host, 'secret')
        guest.lose_ui_deletes = True
        self.join_lobby(guest, 'secret')
        guest.wait_for(lambda: guest.has_text('Options'), what='guest in lobby')
        self.start(host, guest)
        guest.wait_for(lambda: guest.states(STATE_PLANET), what='load level')
        self.assertTrue(guest.has_text('Co-op Lobbies'), 'test setup: the lobby browser should be stuck')

        guest.lose_ui_deletes = False
        host.enter_level(NOVALIS)
        guest.enter_level(NOVALIS)

        guest.wait_for(lambda: not guest.has_text('Co-op Lobbies'), timeout=5, what='stuck lobby browser to clear')
        guest.wait_for(lambda: guest.has_exact_text('alpha'), timeout=5, what="host's nametag drawn again")

    def test_guest_lobby_screen_closes_when_game_starts(self):
        """Only the host's lobby screen closed on start; the guest's stayed over their game, and Triangle left the lobby."""
        host, guest = self.lobby_with_two_players()
        self.start(host, guest)
        guest.wait_for(lambda: guest.states(STATE_PLANET), what='load level')
        guest.wait_for(lambda: not guest.has_text('Options'), timeout=3, what="guest's lobby screen to close")

    def test_other_player_movement_is_sent_at_full_rate(self):
        """Updates for players more than 10 units away were thinned out by distance, making jumps look choppy."""
        host, guest = self.start_game()
        guest.move_to(0.0, 0.0, 0.0)
        time.sleep(0.5)

        mark = guest.mark()
        frames = 120
        for frame in range(frames):  # Two seconds of jumping, 60 units away
            host.move_to(60.0, 0.0, 30.0 + frame * 0.1, animation=frame % 2)
            time.sleep(1 / 60)
        time.sleep(0.5)

        received = len(guest.packets(MOBY_UPDATE, mark))
        self.assertGreater(received, frames * 0.6, f'guest got {received} of {frames} movement updates')

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


ENEMY_STATE = 37
EFFECTS = 38
MOVERS = 40
RELAY_LAST = 63


class MirroringTests(CoopTestCase):
    def setUp(self):
        super().setUp()
        mirroring = {'api_version': API_VERSION_ENEMY_SYNC, 'confirm_creates': True}
        self.client_options = {'alpha': mirroring, 'bravo': dict(mirroring)}

    def guest_sees_host(self, host, guest):
        return guest.wait_for(lambda: guest.uuid_of(0), what="host's Ratchet created for guest")

    def test_pose_snapshots_reach_other_player_with_their_id_for_sender(self):
        host, guest = self.start_game()
        host_uuid = self.guest_sees_host(host, guest)

        mark = guest.mark()
        payload = bytes(range(116))
        for frame in range(1, 31):
            host.send_player_state(frame, payload)
            time.sleep(1 / 60)

        snapshots = guest.wait_for(lambda: guest.packets(PLAYER_STATE, mark), what='pose snapshots')
        time.sleep(0.3)
        snapshots = guest.packets(PLAYER_STATE, mark)

        self.assertGreater(len(snapshots), 20)
        for body in snapshots:
            sender, = struct.unpack_from('>H', body, 0)
            self.assertEqual(sender, host_uuid)
            self.assertEqual(body[12:], payload)

    def test_old_mod_is_told_to_update(self):
        self.client_options['bravo'] = {}
        new, old = self.client('alpha'), self.client('bravo')
        old.wait_for(lambda: old.error_messages(), what='notice about the old mod')
        self.assertIn('older than this server', old.error_messages()[0])
        time.sleep(0.5)
        self.assertEqual(new.error_messages(), [])

    def test_mod_without_enemy_sync_is_told(self):
        self.client_options['bravo'] = {'api_version': API_VERSION_MIRRORING}
        new, old = self.client('alpha'), self.client('bravo')
        old.wait_for(lambda: old.error_messages(), what='notice about the older mod')
        self.assertIn('enemies will move on their own', old.error_messages()[0])
        time.sleep(0.5)
        self.assertEqual(new.error_messages(), [])

    def test_enemy_states_and_effects_reach_other_player_with_their_id_for_sender(self):
        host, guest = self.start_game()
        host_uuid = self.guest_sees_host(host, guest)

        mark = guest.mark()
        payload = bytes(range(70))
        for packet_type in (ENEMY_STATE, EFFECTS, RELAY_LAST):
            host.send(packet_type, b'\x00\x00' + payload)
        time.sleep(0.5)

        for packet_type in (ENEMY_STATE, EFFECTS, RELAY_LAST):
            bodies = guest.wait_for(lambda: guest.packets(packet_type, mark), what=f'relayed packet {packet_type}')
            sender, = struct.unpack_from('>H', bodies[0], 0)
            self.assertEqual(sender, host_uuid)
            self.assertEqual(bodies[0][2:], payload)

    def test_reliable_relayed_packets_are_acked_and_relayed_once(self):
        """Mover changes went unacked, so the game sent each again every second for good, and every one of those went to
        the other players as a new one: 450 KB/s on Kerwan, and what queued behind them arrived a minute late."""
        host, guest = self.start_game()
        self.guest_sees_host(host, guest)

        mark = guest.mark()
        body = bytes(2) + bytes(range(40))
        ack = host.send(MOVERS, body, reliable=True)
        host.wait_for(lambda: host.got_ack(*ack), what='ack for the mover change')

        host.send_again(MOVERS, body, *ack)
        time.sleep(1)
        self.assertEqual(len(guest.packets(MOVERS, mark)), 1, 'a mover change sent again was relayed again')

    def test_mod_without_enemy_sync_gets_no_enemy_states(self):
        self.client_options['bravo'] = {'api_version': API_VERSION_MIRRORING, 'confirm_creates': True}
        host, guest = self.start_game()
        self.guest_sees_host(host, guest)

        mark = guest.mark()
        for _ in range(10):
            host.send(ENEMY_STATE, bytes(70))
            host.send(EFFECTS, bytes(70))
        time.sleep(0.5)

        self.assertEqual(guest.packets(ENEMY_STATE, mark), [])
        self.assertEqual(guest.packets(EFFECTS, mark), [])

    def test_old_mod_gets_no_snapshots(self):
        self.client_options['bravo'] = {'confirm_creates': True}
        host, guest = self.start_game()
        self.guest_sees_host(host, guest)

        mark = guest.mark()
        for frame in range(1, 31):
            host.send_player_state(frame)
            time.sleep(1 / 60)
        time.sleep(0.5)

        self.assertEqual(guest.packets(PLAYER_STATE, mark), [])

    def test_hits_reach_other_player_after_loading(self):
        host, guest = self.start_game()
        self.guest_sees_host(host, guest)

        guest.set_game_state(LOADING)
        host.send_hit(uid=321, o_class=1234, damage=2.5)
        time.sleep(1)
        self.assertEqual(guest.packets(MOBY_HIT), [], 'applied while loading')

        guest.set_game_state(PLAYER_CONTROL)
        hit = guest.wait_for(lambda: guest.packets(MOBY_HIT), what='hit after loading')[0]
        _, uid, o_class = struct.unpack_from('>HHH', hit, 0)
        damage, = struct.unpack_from('>f', hit, 16)
        self.assertEqual((uid, o_class, damage), (321, 1234, 2.5))

    def test_create_not_repeated_while_waiting_for_confirmation(self):
        # Every repeated create made the game delete and respawn the other player
        self.client_options['bravo'] = {'api_version': API_VERSION_MIRRORING, 'confirm_creates': False}
        host, guest = self.start_game()
        time.sleep(1)

        mark = guest.mark()
        time.sleep(2)
        creates = len(guest.packets(MOBY_CREATE, mark))
        self.assertLessEqual(creates, 6, f'{creates} creates in 2 seconds')

    def test_players_are_not_tinted(self):
        self.client_options['bravo'] = {'confirm_creates': True}  # An old mod still gets a plain color
        host, guest = self.start_game()
        host_uuid_for_guest = self.guest_sees_host(host, guest)
        guest_uuid_for_host = host.wait_for(lambda: host.uuid_of(0), what="guest's Ratchet created for host")
        time.sleep(0.5)

        def colors(player, uuid):
            found = set()
            for body in player.packets(MOBY_EX):
                moby, count = struct.unpack_from('>HH', body, 0)
                for index in range(count):
                    offset, value = struct.unpack_from('>HI', body, 4 + index * 6)
                    if moby == uuid and offset == 0x38:
                        found.add(value)
            return found

        self.assertEqual(colors(host, guest_uuid_for_host), set(), 'mirroring mod got a color for a player')
        self.assertEqual(colors(guest, host_uuid_for_guest), {0x7F3F3F3F}, 'old mod got a player tint')


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

    def test_save_reaches_player_joining_before_their_data_stream_opens(self):
        """A player joining a game in progress is sent the save to load right away, before their game has opened
        the data stream files go over. The server used to drop it, leaving them without the campaign."""
        host, guest = self.play_and_save()
        guest.disconnect()
        time.sleep(1)

        self.client_options['bravo'] = {'data_stream_delay': 8}
        guest = self.client('bravo')
        self.join_lobby(guest, 'secret')
        guest.wait_for(lambda: guest.has_text('Options'), what='rejoined lobby')
        guest.press(CIRCLE)
        guest.wait_for(lambda: (0, SAVE_OPERATION_LOAD) in guest.states(STATE_SAVE_FILE_OPERATION),
                       what='told to load the saved game')
        self.assertIsNone(guest.data_stream, 'data stream opened too early for this test')

        saves = (host.save_data, guest.save_data)
        guest.wait_for(lambda: any(f in saves for f in guest.files_received), timeout=20, what='saved game sent')

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
