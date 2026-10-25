"""
Headless stand-in for the Ratchet & Clank multiplayer client (bordplate/rac1-multiplayer), speaking Lawrence's UDP
protocol in big endian like a PS3. Packet handling mirrors prx/rc1/multiplayer/network/Client.cpp and GameClient.cpp
closely enough to exercise the server: acks, duplicate filtering via the acked table, packets deferred while in a movie
or loading screen, the remote UI used for lobbies, and keep-alives.
"""
import random
import socket
import struct
import threading
import time

HEADER = struct.Struct('>HHIqBB')  # type, flags, size, time sent, requires ack, ack cycle

# Packet types
CONNECT = 1
SYN = 2
ACK = 3
MOBY_UPDATE = 4
IDKU = 5
MOBY_CREATE = 6
DISCONNECTED = 7
MOBY_DELETE = 8
SET_STATE = 10
SET_HUD_TEXT = 11
CONTROLLER_INPUT = 13
TIME_SYNC = 14
PLAYER_RESPAWNED = 15
TOAST_MESSAGE = 17
MOBY_EX = 18
ERROR_MESSAGE = 21
REGISTER_HYBRID_MOBY = 22
CHANGE_MOBY_VALUE = 24
LEVEL_FLAG_CHANGED = 25
UI = 29
UI_EVENT = 30
OPEN_DATA_STREAM = 31
UPLOAD_FILE = 32

FLAG_RPC = 1

# State types
STATE_PLANET = 4
STATE_GAME = 5
STATE_GIVE_BOLTS = 13
STATE_UNLOCK_LEVEL = 14
STATE_LEVEL_FLAG = 15
STATE_COMMUNICATION_FLAGS = 17
STATE_SAVE_FILE_OPERATION = 19

SAVE_OPERATION_SAVE = 0
SAVE_OPERATION_LOAD = 1

# Game states
PLAYER_CONTROL = 0
MOVIE = 1
CUTSCENE = 2
LOADING = 6

# UI
UI_LIST_MENU = 3
UI_INPUT = 4
UI_OP_DELETE = 4
UI_OP_CLEAR_ALL = 8
ATTR_TEXT = 9
ATTR_INPUT_PROMPT = 23
EVENT_ITEM_ACTIVATED = 1
EVENT_ACTIVATE = 4
EVENT_INPUT_CALLBACK = 5

CIRCLE = 32
TRIANGLE = 16

API_VERSION = 13

# GameClient::update returns early without acking these while in a movie or loading, so the server resends them later
DEFERRED_WHILE_BUSY = {MOBY_CREATE, MOBY_UPDATE, MOBY_EX, CHANGE_MOBY_VALUE, MOBY_DELETE, REGISTER_HYBRID_MOBY}


class FakeClient:
    def __init__(self, username, port, host='127.0.0.1'):
        self.username = username
        self.user_id = random.randint(1, 2**31 - 1)
        self.addr = (host, port)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.05)

        self.lock = threading.RLock()
        self.ack_id = 0
        self.ack_cycle = 0
        self.acked = [0] * 256
        self.last_time = 0

        self.game_state = PLAYER_CONTROL
        self.frozen = False      # Simulates a stalled client: incoming packets are neither processed nor acked
        self.handshake = False
        self.received = []       # (type, body) of every processed (non-ack) packet, in order
        self.rpc_responses = {}  # (ack id, ack cycle) -> body
        self.ui_elements = {}    # element id -> {'type': int, 'attrs': {attribute: bytes}, 'order': int}
        self.input_responses = {}  # substring of an input prompt -> text to answer with
        self._ui_order = 0

        # The TCP data stream the game uses for save files
        self.save_data = f'save file of {username}'.encode() * 2000
        self.files_received = []
        self.data_stream = None

        self.running = True
        self._threads = [
            threading.Thread(target=self._receive_loop, daemon=True),
            threading.Thread(target=self._keepalive_loop, daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    # ---- sending ----

    def _now(self):
        self.last_time = max(self.last_time + 1, int(time.time() * 1000))
        return self.last_time

    def _next_ack(self):
        # Same sequence as Client::make_ack: ack IDs 1-255, the cycle bumps when the ID wraps through 0
        while True:
            previous = self.ack_id
            self.ack_id = (self.ack_id + 1) & 0xFF
            if previous == 0:
                self.ack_cycle = (self.ack_cycle + 1) & 0xFF
            if self.ack_id:
                return self.ack_id, self.ack_cycle

    def send(self, packet_type, body=b'', reliable=False, rpc=False):
        with self.lock:
            requires_ack, ack_cycle = self._next_ack() if (reliable or rpc) else (0, 0)
            header = HEADER.pack(packet_type, FLAG_RPC if rpc else 0, len(body), self._now(), requires_ack, ack_cycle)
            self.sock.sendto(header + body, self.addr)
            return requires_ack, ack_cycle

    def _send_ack(self, ack_id, ack_cycle):
        with self.lock:
            self.sock.sendto(HEADER.pack(ACK, 0, 0, self._now(), ack_id, ack_cycle), self.addr)

    # ---- receiving ----

    def _receive_loop(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                return

            index = 0
            while len(data) - index >= HEADER.size:
                packet_type, flags, size, _, requires_ack, ack_cycle = HEADER.unpack_from(data, index)
                body = data[index + HEADER.size:index + HEADER.size + size]
                index += HEADER.size + size
                with self.lock:
                    self._handle(packet_type, requires_ack, ack_cycle, body)

    def _handle(self, packet_type, requires_ack, ack_cycle, body):
        if self.frozen:
            return

        if packet_type == ACK:
            if requires_ack == 0:
                self.handshake = True
            else:
                self.rpc_responses[(requires_ack, ack_cycle)] = body
            return

        # Already processed in this ack cycle: just ack again (Client::update)
        if requires_ack > 0 and self.acked[requires_ack] == ack_cycle:
            self._send_ack(requires_ack, ack_cycle)
            return

        if self.game_state in (MOVIE, LOADING) and packet_type in DEFERRED_WHILE_BUSY:
            return

        self._process(packet_type, body)

        if requires_ack > 0:
            self._send_ack(requires_ack, ack_cycle)
            self.acked[requires_ack] = ack_cycle

    def _process(self, packet_type, body):
        self.received.append((packet_type, body))

        if packet_type == UI:
            self._process_ui(body)
        elif packet_type == UI_EVENT:
            event_type, element_id, _, _ = struct.unpack_from('>HHIH', body, 0)
            if event_type == EVENT_ACTIVATE:
                self._answer_input(element_id)
        elif packet_type == OPEN_DATA_STREAM:
            key, = struct.unpack_from('>I', body, 0)
            threading.Thread(target=self._data_stream_loop, args=(key,), daemon=True).start()
        elif packet_type == SET_STATE:
            for index in range(0, len(body) - 11, 12):
                _, kind, _, value = struct.unpack_from('>HHII', body, index)
                if kind == STATE_SAVE_FILE_OPERATION and value == SAVE_OPERATION_SAVE:
                    threading.Thread(target=self.upload_save, daemon=True).start()

    def _data_stream_loop(self, key):
        stream = socket.create_connection(self.addr)
        stream.sendall(struct.pack('>I', key))
        self.data_stream = stream

        def read(length):
            data = b''
            while len(data) < length:
                chunk = stream.recv(length - len(data))
                if not chunk:
                    raise ConnectionError('data stream closed')
                data += chunk
            return data

        try:
            while self.running:
                packet_type, _, size, _, _, _ = HEADER.unpack(read(HEADER.size))
                body = read(size)
                if packet_type == UPLOAD_FILE:
                    _, file_size = struct.unpack('>BI', body[:5])
                    with self.lock:
                        self.files_received.append(read(file_size))
        except (ConnectionError, OSError):
            pass

    def upload_save(self):
        while self.data_stream is None:
            time.sleep(0.05)
        body = struct.pack('>BI', 0, len(self.save_data))
        header = HEADER.pack(UPLOAD_FILE, 0, len(body), self._now(), 0, 0)
        # Split the upload into odd pieces like a real network can
        payload = header + body + self.save_data
        for start in range(0, len(payload), 7000):
            self.data_stream.sendall(payload[start:start + 7000])
            time.sleep(0.01)

    def _process_ui(self, body):
        element_id, element_type, operations, items = struct.unpack_from('>HHBB', body, 0)
        if operations & UI_OP_CLEAR_ALL:
            self.ui_elements.clear()
        if operations & UI_OP_DELETE:
            self.ui_elements.pop(element_id, None)

        offset = 6
        for _ in range(items):
            item_id, _, _, attribute, length = struct.unpack_from('>HBBHH', body, offset)
            offset += 8
            data = body[offset:offset + length]
            offset += length

            if item_id not in self.ui_elements:
                self._ui_order += 1
                self.ui_elements[item_id] = {'type': element_type, 'attrs': {}, 'order': self._ui_order}
            self.ui_elements[item_id]['attrs'][attribute] = data

    def _answer_input(self, element_id):
        element = self.ui_elements.get(element_id)
        if element is None:
            return
        prompt = text_of(element['attrs'].get(ATTR_INPUT_PROMPT, b''))
        for needle, answer in self.input_responses.items():
            if needle in prompt:
                self.ui_event(EVENT_INPUT_CALLBACK, element_id, 0, answer.encode())
                return

    def _keepalive_loop(self):
        while self.running:
            if self.handshake:
                self.send(CONTROLLER_INPUT, struct.pack('>HH', 0, 0))
            time.sleep(0.5)

    # ---- actions ----

    def connect(self, timeout=10):
        deadline = time.time() + timeout
        while not self.handshake:
            self.send(SYN)
            if time.time() > deadline:
                raise TimeoutError(f'{self.username}: no handshake')
            time.sleep(0.1)

        name = self.username.encode()
        key = self.send(CONNECT, struct.pack('>iI8sH', self.user_id, API_VERSION, b'\0' * 8, len(name)) + name,
                        rpc=True)
        response = self.wait_for(lambda: self.rpc_responses.get(key), timeout, 'connect response')
        status, = struct.unpack('>i', response[:4])
        if status != 1:
            raise RuntimeError(f'{self.username}: connect failed with status {status}')

    def disconnect(self):
        self.send(DISCONNECTED)
        self.close()

    def close(self):
        self.running = False
        time.sleep(0.1)
        self.sock.close()
        if self.data_stream is not None:
            self.data_stream.close()

    def press(self, button):
        self.send(CONTROLLER_INPUT, struct.pack('>HH', button, 1), reliable=True)

    def ui_event(self, event_type, element_id, data=0, extra=b''):
        self.send(UI_EVENT, struct.pack('>HHIH', event_type, element_id, data, len(extra)) + extra, reliable=True)

    def set_state(self, state_type, value, offset=0):
        self.send(SET_STATE, struct.pack('>HHII', 0, state_type, offset & 0xFFFFFFFF, value & 0xFFFFFFFF),
                  reliable=True)

    def set_game_state(self, state):
        with self.lock:
            self.game_state = state
        self.set_state(STATE_GAME, state)

    def change_level_flag(self, level, flag_type, index, value):
        body = struct.pack('>HBB', flag_type, level, 1) + struct.pack('>BHI', 1, index, value)
        self.send(LEVEL_FLAG_CHANGED, body, reliable=True)

    def level_flags(self, since=0):
        """(level, type, index, value) of every level flag the server wrote."""
        return [(offset >> 24, (offset >> 16) & 0xFF, offset & 0xFFFF, value)
                for offset, value in self.states(STATE_LEVEL_FLAG, since)]

    def respawn(self, level, spawn_id=1):
        self.send(PLAYER_RESPAWNED, struct.pack('>BH', spawn_id, level), reliable=True)

    def enter_level(self, level, spawn_id=1):
        self.set_game_state(LOADING)
        time.sleep(0.1)
        self.respawn(level, spawn_id)
        time.sleep(0.1)
        self.set_game_state(PLAYER_CONTROL)

    # ---- inspection ----

    def mark(self):
        with self.lock:
            return len(self.received)

    def packets(self, packet_type, since=0):
        with self.lock:
            return [body for kind, body in self.received[since:] if kind == packet_type]

    def states(self, state_type, since=0):
        """All (offset, value) pairs of SET_STATE parts with the given state type."""
        found = []
        for body in self.packets(SET_STATE, since):
            for index in range(0, len(body) - 11, 12):
                _, kind, offset, value = struct.unpack_from('>HHII', body, index)
                if kind == state_type:
                    found.append((offset, value))
        return found

    def deleted_oclasses(self, since=0):
        result = []
        for body in self.packets(MOBY_DELETE, since):
            uuid, flags = struct.unpack_from('>II', body, 0)
            if flags == 2:
                result.append(uuid)
        return result

    def latest_element(self, element_type, prompt_contains=None):
        with self.lock:
            candidates = [
                (element['order'], element_id) for element_id, element in self.ui_elements.items()
                if element['type'] == element_type and (
                    prompt_contains is None or
                    prompt_contains in text_of(element['attrs'].get(ATTR_INPUT_PROMPT, b'')))
            ]
        return max(candidates)[1] if candidates else None

    def has_text(self, needle):
        with self.lock:
            return any(needle in text_of(element['attrs'].get(ATTR_TEXT, b''))
                       for element in self.ui_elements.values())

    def ui_creates_with_text(self, needle, since=0):
        """How many UI create packets carried an element whose text is exactly `needle`."""
        count = 0
        for body in self.packets(UI, since):
            _, _, operations, items = struct.unpack_from('>HHBB', body, 0)
            if not operations & 1:
                continue
            offset = 6
            for _ in range(items):
                _, _, _, attribute, length = struct.unpack_from('>HBBHH', body, offset)
                offset += 8
                if attribute == ATTR_TEXT and text_of(body[offset:offset + length]) == needle:
                    count += 1
                offset += length
        return count

    def error_messages(self):
        return [text_of(body) for body in self.packets(ERROR_MESSAGE)]

    def wait_for(self, condition, timeout=10, what='condition'):
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = condition()
            if result:
                return result
            time.sleep(0.05)
        raise TimeoutError(f'{self.username}: timed out waiting for {what}')


def text_of(data):
    return data.decode('utf-8', errors='replace').replace('\x00', '')
