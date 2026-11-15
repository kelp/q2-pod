"""addon_remote.c (docs/librespot.md, Card apps): while stock's AirPlay receiver plays, Play/Pause, the side buttons
and a Bluetooth headset's keys go to a running card app whose q2app holds remote=<op>, as a request on its browse
socket; with no such app, they do what they did before."""
import ctypes as C

import pytest

from conftest import O
from test_addon import Sleep, card, sl
from test_headset import NEXT, PAUSE, PAUSECD, PLAYCD, PLAYING, PLAYKEY, PLAYPAUSE, PREV, STOP, STOPPED, Player, hs, link
from test_nav_loop import STOP as RET_STOP, Keys, Ui, list_page, stock_bytes  # noqa: F401 (a fixture)
from test_radio import Rd

KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN, KEY_CENTER = (O[k] for k in ('KEY_PLAY', 'KEY_FWD_BTN', 'KEY_BACK_BTN', 'KEY_CENTER'))
SOCK = b'/tmp/q2-roon.browse.sock'


class Remote:
    """A card with q2-roon on it, running (its loop's pid file) or not, stock's AirPlay flag, the requests the
    workers would send and the toasts."""
    def __init__(self, fw, manifest=b'name=Roon\nremote=ctl\n', running=True, airplay=2):
        self.fw, self.flag, self.jobs, self.toasts = fw, airplay, [], []
        self.world = card(fw, roon=manifest)
        if running:
            self.world.files[b'/tmp/q2-roon'] = b'4242\n'
        fw.mock('airplayGetFlag', lambda *_: self.flag)
        fw.mock('fw_pthread_create', lambda thread, attr, fn, arg, *_: self.jobs.append(arg) or 0)
        fw.mock('navigator_to_with_context', lambda path, info, *_: self.toasts.append(C.string_at(info + 8)))

    def key(self, key):
        return self.fw.fn('addon_remote_key', C.c_int, C.c_uint)(key)

    def run(self, reply=b'', connect=0):
        """Each request waiting run by its worker, the app answering reply; what each sent, and to where."""
        sent, to = [], []
        script = [reply]

        def recv(fd, buf, n, *_):
            piece = script.pop(0) if script else b''
            C.memmove(buf, piece, min(len(piece), n))
            return min(len(piece), n)

        def conn(fd, addr, n, *_):
            to.append(C.string_at(addr + 2))
            return connect
        self.fw.mock('fw_socket', lambda *_: 5)
        self.fw.mock('fw_connect', conn)
        self.fw.mock('fw_select', lambda *_: 1)
        self.fw.mock('fw_send', lambda fd, buf, n, *_: sent.append(C.string_at(buf, n)) or n)
        self.fw.mock('fw_recv', recv)
        worker = self.fw.fn('addon_remote__worker', C.c_void_p, C.c_void_p)
        while self.jobs:
            worker(self.jobs.pop(0))
        return sent, to

    def poll(self):
        self.fw.fn('addon_remote_poll', None)()
        return self.toasts


@pytest.mark.parametrize('key,word', [(KEY_PLAY, b'playpause'), (KEY_FWD_BTN, b'next'), (KEY_BACK_BTN, b'previous')])
def test_each_key_is_a_request_to_the_app(fw, key, word):
    r = Remote(fw)
    assert r.key(key) == 1
    sent, to = r.run()
    assert sent == [b'Q2SPOT/1\n1\tctl\t' + word + b'\n'] and to == [SOCK]


def test_each_request_has_a_new_id(fw):
    r = Remote(fw)
    r.key(KEY_PLAY), r.key(KEY_FWD_BTN)
    sent, _ = r.run()
    assert sent == [b'Q2SPOT/1\n1\tctl\tplaypause\n', b'Q2SPOT/1\n2\tctl\tnext\n']


def test_other_keys_are_left_alone(fw):
    r = Remote(fw)
    assert r.key(KEY_CENTER) == 0 and not r.jobs


@pytest.mark.parametrize('flag', [0, 1, 3])
def test_only_while_airplay_plays(fw, flag):
    r = Remote(fw, airplay=flag)
    assert r.key(KEY_PLAY) == 0 and not r.jobs


def test_only_an_app_that_runs(fw):
    r = Remote(fw, running=False)
    assert r.key(KEY_PLAY) == 0 and not r.jobs


@pytest.mark.parametrize('manifest', [b'name=Roon\n', b'remote=\n', b'remote=a b\n', b'remote=' + b'a' * 16 + b'\n',
                                      b'remote=a%b\n', b'remote=a\tb\n'])
def test_only_an_app_that_asks_with_an_op_that_fits(fw, manifest):
    r = Remote(fw, manifest)
    assert r.key(KEY_PLAY) == 0 and not r.jobs


@pytest.mark.parametrize('manifest,op', [(b'remote=ctl\r\n', b'ctl'), (b'remote=' + b'a' * 15 + b'\n', b'a' * 15)])
def test_a_windows_line_end_and_the_longest_op(fw, manifest, op):
    r = Remote(fw, manifest)
    assert r.key(KEY_PLAY) == 1
    assert r.run()[0] == [b'Q2SPOT/1\n1\t' + op + b'\tplaypause\n']


def test_another_source_keeps_the_keys(fw):
    r = Remote(fw)
    fw.var('radio__rd', Rd).active = 1
    assert r.key(KEY_PLAY) == 0 and not r.jobs


def test_no_worker_means_no_key_taken(fw):
    r = Remote(fw)
    fw.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN
    assert r.key(KEY_PLAY) == 0


def test_a_refusal_s_message_is_toasted_on_the_ui_loop(fw):
    r = Remote(fw)
    r.key(KEY_PLAY)
    r.run(b'Q2SPOT/1\n1\terror\tNo Roon core\t\n')
    assert r.toasts == [] and r.poll() == [b'No Roon core']
    assert r.poll() == [b'No Roon core']  # once


@pytest.mark.parametrize('reply', [b'Q2SPOT/1\n1\tok\tPaused\t\n', b'Q2SPOT/1\n1\terror\t\t\n', b'', b'garbage'])
def test_anything_else_is_not_toasted(fw, reply):
    r = Remote(fw)
    r.key(KEY_PLAY)
    r.run(reply)
    assert r.poll() == []


def test_no_socket_is_not_toasted(fw):
    r = Remote(fw)
    r.key(KEY_PLAY)
    r.run(b'Q2SPOT/1\n1\terror\tx\t\n', connect=-1)
    assert r.poll() == []


# The Play/Pause key and the side buttons (navigation.c ringnav).

@pytest.fixture
def keys(fw, stock_bytes):
    return Keys(fw, stock_bytes)


@pytest.mark.parametrize('key,word', [(KEY_PLAY, b'playpause'), (KEY_FWD_BTN, b'next'), (KEY_BACK_BTN, b'previous')])
def test_the_q2_s_keys_reach_the_app_under_airplay(fw, keys, key, word):
    list_page(Ui(fw))
    r = Remote(fw)
    assert keys(key) == RET_STOP
    assert r.run()[0] == [b'Q2SPOT/1\n1\tctl\t' + word + b'\n']
    assert not fw.calls('player_play_pause') and not fw.calls('player_next_music')


def test_the_q2_s_keys_without_the_app_are_stock_s(fw, keys):
    list_page(Ui(fw))
    r = Remote(fw, running=False)
    keys(KEY_PLAY)
    assert not r.jobs and not fw.calls('player_play_pause')  # stock's on_wm_keyup_fun drops it, as before


# A Bluetooth headset's keys (headset.c headset_command), and its ears.

def command(fw, player, code, step=200):
    player.now += step
    fw.fn('headset_command', None, C.c_int)(code)


@pytest.mark.parametrize('code,word', [(PLAYPAUSE, b'playpause'), (NEXT, b'next'), (PREV, b'previous'),
                                       (PLAYCD, b'play'), (PLAYKEY, b'play'), (PAUSECD, b'pause'), (PAUSE, b'pause'),
                                       (STOP, b'pause')])
@pytest.mark.parametrize('status', [PLAYING, STOPPED])  # the local player's, which means nothing under AirPlay
def test_headset_keys_reach_the_app_and_never_stop_airplay(fw, code, word, status):
    r = Remote(fw)
    player = Player(fw, status=status)
    command(fw, player, code)
    assert r.run()[0] == [b'Q2SPOT/1\n1\tctl\t' + word + b'\n']
    assert player.toggles() == 0 and not fw.calls('player_next_music') and not fw.calls('player_prev_music')


@pytest.mark.parametrize('code', [PLAYPAUSE, PAUSE])
def test_headset_keys_without_the_app_are_as_before(fw, code):
    r = Remote(fw, running=False)
    player = Player(fw, status=PLAYING)
    command(fw, player, code)
    assert not r.jobs and player.toggles() == 1


# The firmware's own pauses and plays (media_set_playing): the ears, the link, the sleep timer.

def sent(r):
    return [req.split(b'\t')[-1].rstrip(b'\n') for req in r.run()[0]]


@pytest.mark.parametrize('playing,word', [(0, b'pause'), (1, b'play')])
def test_a_pause_or_play_of_the_firmware_s_own_is_the_app_s(fw, playing, word):
    r = Remote(fw)
    player = Player(fw, status=STOPPED)
    fw.fn('media_set_playing', None, C.c_int)(playing)
    assert sent(r) == [word] and player.toggles() == 0 and hs(fw).automatic == 0


def test_without_the_app_it_is_the_local_player_s_as_before(fw):
    r = Remote(fw, running=False)
    player = Player(fw, status=PLAYING)
    fw.fn('media_set_playing', None, C.c_int)(0)
    assert not r.jobs and player.toggles() == 1


def test_a_bud_out_pauses_the_app_and_back_in_plays_it(fw):
    r = Remote(fw)
    player = Player(fw, status=STOPPED)
    for report in (3, 1):
        player.now += 200
        fw.fn('headset_ears', None, C.c_uint)(4 | report)
    assert sent(r) == [b'pause']
    player.now += 200
    fw.fn('headset_ears', None, C.c_uint)(4 | 3)
    assert sent(r) == [b'play'] and player.toggles() == 0


def test_both_buds_out_pause_the_app_without_asking_again_each_poll(fw):
    r = Remote(fw)
    player = Player(fw, status=STOPPED)
    for report in (3, 0, 0, 0):
        player.now += 100
        fw.fn('headset_ears', None, C.c_uint)(4 | report)
    assert sent(r) == [b'pause'] and player.toggles() == 0


def test_the_link_dropping_pauses_the_app(fw):
    r = Remote(fw)
    player = Player(fw, status=STOPPED)  # after the card: its clock
    link(fw, player)
    fw.mock('fw_pthread_create', lambda thread, attr, fn, arg, *_: r.jobs.append(arg) or 0)  # link() took it over
    fw.var('bt_linkstatus', C.c_ubyte).value = 0
    player.now += 200
    fw.fn('headset_poll', None)()
    assert sent(r) == [b'pause'] and player.toggles() == 0


def test_a_sleep_timer_under_airplay_pauses_the_app(fw):
    s = Sleep(fw)
    s.read(b'until %d' % (s.world.now + 600))
    r = Remote(fw)

    def clock(which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))
        t[0], t[1] = s.boot + 600, 0
    fw.mock('fw_clock_gettime', clock)
    fw.fn('addon__sleep_check', None)()
    assert sent(r) == [b'pause'] and s.paused() == 0 and sl(fw).at == 0


def test_an_app_that_starts_is_seen_within_a_second(fw):
    r = Remote(fw, running=False)
    player = Player(fw, status=STOPPED)
    fw.fn('media_set_playing', None, C.c_int)(0)
    r.world.files[b'/tmp/q2-roon'] = b'4242\n'
    player.now += 999
    fw.fn('media_set_playing', None, C.c_int)(0)
    assert not r.jobs
    player.now += 1
    fw.fn('media_set_playing', None, C.c_int)(0)
    assert sent(r) == [b'pause']
