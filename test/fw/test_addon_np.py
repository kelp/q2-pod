"""addon_np.c (docs/librespot.md, Card apps): while stock's AirPlay receiver plays and the card app that takes AirPlay's
keys (addon_remote.c) writes a fresh /tmp/q2-<id>.np, its Now Playing opens over stock's airplay_page: the app's name,
the cover, title, artist and album, the times and bar moving on, the play state; a stale or missing file shows a
message; Return closes it alone, and it stays shut till the next AirPlay session; AirPlay stopping closes it."""
import ctypes as C

import pytest

from conftest import O
from test_addon import card
from test_spotify import Canvas, Thumbs, Widgets, classic, handler, key_event, minutes, width

KEY_RETURN, KEY_CENTER, EVT_DESTROY, EVT_KEY_UP = O['KEY_RETURN'], O['KEY_CENTER'], O['EVT_DESTROY'], O['EVT_KEY_UP']
SPOT_BAR_W = O['SPOT_BAR_W']
RET_STOP = 11
NP, COVER = b'/tmp/q2-roon.np', b'/tmp/q2-roon.np.jpg'
SONG = dict(state='playing', track='t1', title='Title', artist='Artist', album='Album', zone='Kitchen',
            duration=200000, position=5000, cover=0)


class Ui(C.Structure):
    _fields_ = [*((n, C.c_void_p) for n in
                  'page info msg name art bar glyph title artist album elapsed remain note sub'.split()),
                *((n, C.c_uint) for n in 'polled leave shown playing bar_drawn art_cover art_tried'.split()),
                ('ipod', C.c_int), ('art_tries', C.c_int), ('app', C.c_char * 64)]


class Air:
    """A card with q2-roon on it, running, stock's AirPlay flag and its page on top, the app's .np file (written
    now, its at the clock's now), the widgets and timers the page makes, and the windows closed."""
    def __init__(self, fw, manifest=b'name=Roon\nremote=ctl\n', airplay=2, top=b'airplay_page', **song):
        self.fw, self.flag, self.top, self.timers = fw, airplay, top, []
        self.world = card(fw, roon=manifest)
        self.world.files[b'/tmp/q2-roon'] = b'4242\n'
        self.w, self.ui = Widgets(fw), fw.var('addon_np__ui', Ui)
        self.light = fw.var('g_backlight_status', C.c_ubyte)
        self.light.value = 1
        self.names = {}
        self.air = C.create_string_buffer(0x200)  # stock's airplay_page
        fw.mock('airplayGetFlag', lambda *_: self.flag)
        fw.mock('window_manager_get_top_window', lambda *_: self.window())
        fw.mock('widget_get_prop_str', lambda w, key, default, *_: self.names.get(w, default))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        fw.mock('toolsTimeItoa', minutes)
        fw.mock('fw_fork', lambda *_: -1)  # no ffmpeg for a cover stock's thumbnailer refused
        if song is not None:
            self.write(**(SONG | song))

    def window(self):
        if self.top is None:
            return 0
        self.name = C.create_string_buffer(self.top)
        self.names[C.addressof(self.air)] = C.addressof(self.name)
        return C.addressof(self.air)

    def write(self, **song):
        """The app's file, written now: its at the clock's now unless given."""
        song.setdefault('at', self.world.now * 1000)
        self.world.files[NP] = b''.join(b'%s=%s\n' % (k.encode(), str(v).encode()) for k, v in song.items())
        self.world.mtime[NP] = self.world.now

    def poll(self, ms=250, s=0):
        self.world.ms += ms
        self.world.now += s
        self.fw.fn('addon_np_poll', None)()

    def labels(self):
        return [self.w.text.get(w) for w in (self.ui.title, self.ui.artist, self.ui.album)]

    def times(self):
        return self.w.text.get(self.ui.elapsed), self.w.text.get(self.ui.remain)

    def note(self):
        return self.w.text.get(self.ui.note), self.w.text.get(self.ui.sub)

    def song_shown(self):
        return self.w.shown[self.ui.info] == 1 and self.w.shown[self.ui.msg] == 0

    def closed(self):
        return [a[0] for _, a in self.fw.calls('window_close')]

    def paint(self, w, canvas):
        self.fw.fn('addon_np_paint', None, C.c_void_p, C.c_void_p)(w, canvas.address)

    def images(self, w):
        return [C.string_at(a[1]).decode() for _, a in self.fw.calls('image_base_set_image') if a[0] == w]


def opened(fw):
    return [C.string_at(a[1]) for _, a in fw.calls('widget_set_name')].count(b'appnp_page')


def test_the_page_opens_over_airplay_page_with_the_song(fw):
    a = Air(fw)
    a.poll()
    assert opened(fw) == 1 and a.ui.page and a.song_shown()
    assert a.labels() == ['Title', 'Artist', 'Album'] and a.times() == ('0:05', '-3:15')
    assert a.w.text[a.ui.name] == 'Roon'  # the q2app's name=
    a.poll()
    assert opened(fw) == 1  # once


def test_without_a_name_the_page_shows_the_zone(fw):
    a = Air(fw, manifest=b'remote=ctl\n')
    a.poll()
    assert a.w.text[a.ui.name] == 'Kitchen'


@pytest.mark.parametrize('why', ['flag 0', 'flag 1', 'flag 3', 'not running', 'no remote', 'no file', 'stale',
                                 'another page', 'no window'])
def test_the_page_stays_shut(fw, why):
    a = Air(fw, manifest=b'name=Roon\n' if why == 'no remote' else b'name=Roon\nremote=ctl\n',
            airplay=int(why[-1]) if why.startswith('flag') else 2,
            top={'another page': b'home_page', 'no window': None}.get(why, b'airplay_page'))
    if why == 'not running':
        del a.world.files[b'/tmp/q2-roon']
    if why == 'no file':
        del a.world.files[NP]
    if why == 'stale':
        a.world.mtime[NP] = a.world.now - 16
    a.poll()
    assert not opened(fw) and not a.ui.page


def test_a_file_15_s_old_is_still_fresh(fw):
    a = Air(fw)
    a.world.mtime[NP] = a.world.now - 15
    a.poll()
    assert a.ui.page


def test_polls_are_paced(fw):
    a = Air(fw, top=b'home_page')
    a.poll()
    a.top = b'airplay_page'
    a.poll(ms=249)
    assert not a.ui.page
    a.poll(ms=1)
    assert a.ui.page


def test_the_time_runs_on_while_playing_to_the_end_and_stands_paused(fw):
    a = Air(fw)
    a.poll()
    a.poll(s=2)
    assert a.times() == ('0:07', '-3:13')
    a.world.now += 500
    a.write(**SONG, at=(a.world.now - 502) * 1000)  # a heartbeat of the same play
    a.poll()
    assert a.times() == ('3:20', '-0:00')
    a.write(**SONG | dict(state='paused', position=9000))
    a.poll(s=3)
    assert a.times() == ('0:09', '-3:11') and a.song_shown()


def test_a_stream_of_no_length_has_no_bar(fw):
    a = Air(fw, duration=0)
    a.poll()
    assert a.w.shown[a.ui.bar] == 0 and a.w.shown[a.ui.remain] == 0 and a.times()[0] == '0:05'
    a.write(**SONG)
    a.poll()
    assert a.w.shown[a.ui.bar] == 1 and a.w.shown[a.ui.remain] == 1


@pytest.mark.parametrize('why', ['stale', 'gone', 'none', 'not running'])
def test_without_a_fresh_song_the_open_page_says_nothing_plays(fw, why):
    a = Air(fw)
    a.poll()
    if why == 'stale':
        a.poll(s=16)
    elif why == 'gone':
        del a.world.files[NP]
        a.poll()
    elif why == 'none':
        a.write(state='none')
        a.poll()
    else:
        del a.world.files[b'/tmp/q2-roon']
        a.poll(ms=1000)
    assert not a.song_shown() and a.note() == ('Nothing playing', 'Choose music in Roon')
    a.write(**SONG)
    a.world.files[b'/tmp/q2-roon'] = b'4242\n'
    a.poll(ms=1000)
    assert a.song_shown()


def test_the_page_follows_the_file_only_with_the_screen_on(fw):
    a = Air(fw)
    a.poll()
    a.light.value = 0
    a.write(**SONG | dict(title='Next'))
    a.poll()
    assert a.labels()[0] == 'Title'
    a.light.value = 1
    a.poll()
    assert a.labels()[0] == 'Next'


def test_the_cover_is_fitted_to_the_art_once_a_cover(fw):
    thumbs = Thumbs(fw)
    a = Air(fw)
    a.poll()
    placeholder = a.images(a.ui.art)
    width(a.ui.art, 166)
    a.write(**SONG | dict(cover=1))
    a.poll()
    a.poll()
    (src, dst, w, h), = thumbs.asked
    assert src == COVER and w == h == 166
    assert a.images(a.ui.art)[-1] == 'file://' + dst.decode()
    a.write(**SONG | dict(cover=2))
    a.poll()
    assert len(thumbs.asked) == 2
    a.write(**SONG | dict(cover=0))
    a.poll()
    assert a.images(a.ui.art)[-1] == placeholder[-1] and a.ui.art_cover == 0


def test_a_cover_that_fails_is_tried_on_the_next_3_passes_then_left(fw):
    thumbs = Thumbs(fw, ok=0)
    a = Air(fw, cover=1)
    for _ in range(6):
        a.poll()
    assert len(thumbs.asked) == 4
    a.write(**SONG | dict(cover=2))
    a.poll()
    assert len(thumbs.asked) == 5


def test_the_bar_and_glyph_are_painted_as_spotify_s(fw):
    a = Air(fw, position=100000, state='paused')
    a.poll()
    canvas = Canvas(fw)
    a.paint(a.ui.bar, canvas)
    track, played = canvas.drawn
    assert track[:4] == ('round', 0, 0, SPOT_BAR_W) and played[3] == 100000 * SPOT_BAR_W // 200000
    canvas = Canvas(fw)
    a.paint(a.ui.glyph, canvas)
    assert len(canvas.drawn) == 2  # paused: two bars
    a.write(**SONG)
    a.poll()
    canvas = Canvas(fw)
    a.paint(a.ui.glyph, canvas)
    assert len(canvas.drawn) == 7 and canvas.colour == canvas.fill  # a triangle


def test_paint_draws_on_the_open_page_s_bar_and_glyph_only(fw):
    a = Air(fw)
    a.poll()
    canvas = Canvas(fw)
    a.paint(a.ui.title, canvas)
    a.paint(0, canvas)
    bar = a.ui.bar
    handler(fw, a.ui.page, EVT_DESTROY)()
    a.paint(bar, canvas)
    assert canvas.drawn == []


def test_classic_s_page_takes_the_local_now_playing_s_places(fw):
    classic(fw)
    a = Air(fw)
    a.poll()
    named = [C.string_at(n) for _, (w, n, *_) in fw.calls('widget_set_name') if w == a.ui.name]
    assert a.ui.ipod == 1 and a.w.shown[a.ui.name] == 0 and a.w.shown[a.ui.glyph] == 0
    assert named == [b'_page_title']  # the label Classic's title bar reads


def returned(fw, a):
    """Return on the page, its timer run, and the page destroyed as navigator_back would."""
    keyup = handler(fw, a.ui.page, EVT_KEY_UP)
    assert keyup(C.addressof(key_event(KEY_CENTER))) == 0
    assert keyup(C.addressof(key_event(KEY_RETURN))) == RET_STOP and not fw.calls('navigator_back')
    C.CFUNCTYPE(C.c_int, C.c_void_p)(a.timers[-1])(None)
    handler(fw, a.ui.page, EVT_DESTROY)()


def test_return_closes_the_page_alone_and_leaves_airplay_page(fw):
    a = Air(fw)
    a.poll()
    returned(fw, a)
    assert len(fw.calls('navigator_back')) == 1 and a.closed() == []  # airplay_page's close would end AirPlay


def test_after_return_the_page_stays_shut_till_the_next_airplay_session(fw):
    a = Air(fw)
    a.poll()
    returned(fw, a)
    for _ in range(4):
        a.poll(s=1)
        a.write(**SONG)
    assert opened(fw) == 1
    a.flag = 1  # the session ends (stock's Exit AirPlay, local music)
    a.poll()
    a.flag = 2
    a.poll()
    assert opened(fw) == 2


def test_airplay_stopping_closes_the_page(fw):
    a = Air(fw)
    a.poll()
    page = a.ui.page
    a.flag = 0
    a.poll()
    assert a.closed() == [page]


def test_closing_forgets_the_page_and_it_opens_again_over_airplay_page(fw):
    a = Air(fw)
    a.poll()
    handler(fw, a.ui.page, EVT_DESTROY)()
    assert bytes(a.ui) == bytes(C.sizeof(Ui))
    a.top = b'home_page'
    a.poll()
    assert opened(fw) == 1
    a.top = b'airplay_page'
    a.poll()
    assert opened(fw) == 2
