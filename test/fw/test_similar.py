"""patch/similar.c: More like this, Now Playing's hold menu row: Last.fm's similar artists (artist.getSimilar),
matched to the library's artists, and the mix of their songs it plays."""
import ctypes as C
import json
import re
import urllib.parse

import pytest

from conftest import ROOT
from test_nav_library_pages import Library, Songs, Ui, field
from test_onlinematch import Net

SOURCE = (ROOT/'patch/similar.c').read_text()
DEFINES = dict(re.findall(r'^#define (SIM_\w+) (\d+)\b', SOURCE, re.M))
SIM_MIX, SIM_OWN, SIM_ASK, SIM_NAME = (int(DEFINES[k]) for k in ('SIM_MIX', 'SIM_OWN', 'SIM_ASK', 'SIM_NAME'))
SIM_OFFLINE, SIM_REFUSED = -1, -2
LASTFM = 'https://ws.audioscrobbler.com/2.0/?method=artist.getsimilar'
THREAD = 0x5157
REPEAT = 8
RANDOM = C.CFUNCTYPE(C.c_uint, C.c_uint)


class Sim(C.Structure):
    _fields_ = [('names', C.c_void_p), ('thread', C.c_ulong), ('running', C.c_int), ('cancel', C.c_int), ('got', C.c_int),
                ('artist', C.c_char * SIM_NAME), ('path', C.c_char * 0x200), ('key', C.c_char * 256), ('message', C.c_char * 128)]


def image(size):
    return {'#text': f'https://lastfm.freetls.fastly.net/i/u/{size}/2a96cbd8b46e442fc41c2b86b821562f.png', 'size': size}


def similar(*names, artist='Cher'):
    """artist.getSimilar's JSON reply, shaped as Last.fm's documentation shows it."""
    return json.dumps({'similarartists': {'artist': [
        {'name': n, 'mbid': '3d6e4b6d-2700-458c-9722-9021965a8164', 'match': f'{1 - i / 100:.6f}',
         'url': 'https://www.last.fm/music/' + urllib.parse.quote_plus(n),
         'image': [image(s) for s in ('small', 'medium', 'large', 'extralarge', 'mega', '')], 'streamable': '0'}
        for i, n in enumerate(names)], '@attr': {'artist': artist}}}).encode()


NOT_FOUND = b'{"error":6,"message":"The artist you supplied could not be found","links":[]}'
BAD_KEY = b'{"error":10,"message":"Invalid API key - You must be granted a valid key by last.fm","links":[]}'


def parse(fw, body, max_=SIM_ASK):
    """sim_parse's answer and the names it kept."""
    fn = fw.fn('similar__sim_parse', C.c_int, C.c_char_p, C.c_void_p, C.c_int, C.c_void_p, C.c_uint)
    names, message = C.create_string_buffer(SIM_NAME * max_), C.create_string_buffer(128)
    got = fn(body, names, max_, message, 128)
    return got, [names.raw[i * SIM_NAME:(i + 1) * SIM_NAME].split(b'\0')[0].decode() for i in range(max(got, 0))], message.value.decode()


def test_similar_artists_come_in_lastfms_order(fw):
    Net(fw)
    assert parse(fw, similar('Sonny & Cher', 'Cyndi Lauper', 'Madonna')) == (3, ['Sonny & Cher', 'Cyndi Lauper', 'Madonna'], '')


def test_names_past_the_most_asked_for_are_left_out(fw):
    Net(fw)
    assert parse(fw, similar('A', 'B', 'C'), max_=2)[:2] == (2, ['A', 'B'])


def test_a_name_too_long_to_keep_is_passed_over(fw):
    Net(fw)
    assert parse(fw, similar('x' * SIM_NAME, 'Madonna'))[:2] == (1, ['Madonna'])


def test_one_similar_artist_may_come_as_an_object_not_a_list(fw):
    """Last.fm's JSON is its XML converted, which can make a list of one its member alone."""
    Net(fw)
    body = json.dumps({'similarartists': {'artist': {'name': 'Madonna', 'match': '1'}, '@attr': {'artist': 'Cher'}}}).encode()
    assert parse(fw, body)[:2] == (1, ['Madonna'])


def test_an_artist_with_no_similar_ones_has_none(fw):
    Net(fw)
    assert parse(fw, similar())[:2] == (0, [])
    assert parse(fw, b'{"similarartists":{"artist":"","@attr":{"artist":"Cher"}}}')[:2] == (0, [])
    assert parse(fw, b'{"similarartists":{"@attr":{"artist":"Cher"}}}')[:2] == (0, [])


@pytest.mark.parametrize('body, message', [(NOT_FOUND, 'The artist you supplied could not be found'),
                                           (BAD_KEY, 'Invalid API key - You must be granted a valid key by last.fm')])
def test_an_error_reply_is_refused_with_its_message(fw, body, message):
    Net(fw)
    assert parse(fw, body) == (SIM_REFUSED, [], message)


@pytest.mark.parametrize('body', [b'<html>503 Service Unavailable</html>', b'{"other":1}'])
def test_a_reply_that_is_no_answer_is_offline(fw, body):
    Net(fw)
    assert parse(fw, body)[0] == SIM_OFFLINE


def test_the_url_asks_for_the_artist_with_the_key_both_encoded(fw):
    out = C.create_string_buffer(2048)
    assert fw.fn('similar__sim_url', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 2048, 'Sonny & Cher/é'.encode(), b'k&y') == 1
    url = urllib.parse.urlsplit(out.value.decode())
    query = urllib.parse.parse_qs(url.query)
    assert out.value.decode().startswith(LASTFM)
    assert query['artist'] == ['Sonny & Cher/é'] and query['api_key'] == ['k&y'] and query['format'] == ['json']
    assert query['limit'] == [str(SIM_ASK)] and query['autocorrect'] == ['1']


def test_a_url_too_long_is_none(fw):
    out = C.create_string_buffer(64)
    assert fw.fn('similar__sim_url', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 64, b'a' * 64, b'k') == 0


@pytest.mark.parametrize('name, key', [('The Beatles', 'beatles'), ('beatles', 'beatles'), ('BEATLES', 'beatles'),
                                       ('  the  Cure ', 'cure'), ('THE WHO', 'who'), ('The', 'the'), ('The ', 'the'), ('The The', 'the'),
                                       ('Theatre of Tragedy', 'theatre of tragedy'), ('Björk', 'björk'), ('', '')])
def test_an_artists_key_folds_case_and_a_leading_the(fw, name, key):
    out = C.create_string_buffer(SIM_NAME)
    fw.fn('similar__sim_key', None, C.c_void_p, C.c_uint, C.c_char_p)(out, SIM_NAME, name.encode())
    assert out.value.decode() == key


def test_a_key_may_be_made_in_place(fw):
    s = C.create_string_buffer(b'The Beatles', SIM_NAME)
    fw.fn('similar__sim_key', None, C.c_void_p, C.c_uint, C.c_void_p)(s, SIM_NAME, s)
    assert s.value == b'beatles'


def test_a_key_is_cut_to_fit(fw):
    out = C.create_string_buffer(8)
    fw.fn('similar__sim_key', None, C.c_void_p, C.c_uint, C.c_char_p)(out, 4, b'Madonna')
    assert out.value == b'mad'


def mix(fw, artists, keys, own, rnd=lambda n: n - 1):
    """sim_mix over song artists (None: no song to pick): the picks as artists, in order, and the artists they're by."""
    fn = fw.fn('similar__sim_mix', C.c_uint, C.c_void_p, C.c_uint, C.c_void_p, C.c_int, C.c_char_p, RANDOM, C.c_void_p, C.c_void_p)
    table = (C.c_char_p * max(len(artists), 1))(*[a.encode() if a is not None else None for a in artists])
    flat = C.create_string_buffer(b''.join(k.encode().ljust(SIM_NAME, b'\0') for k in keys) or b'\0')
    pick, by = (C.c_uint * SIM_MIX)(), C.c_uint()
    calls = []
    def random(n):
        calls.append(n)
        return rnd(n)
    n = fn(table, len(artists), flat, len(keys), own.encode(), RANDOM(random), pick, C.byref(by))
    return [pick[i] for i in range(n)], by.value, calls


def test_a_mix_is_mostly_similar_artists_with_a_few_by_the_artist(fw):
    artists = ['Cher'] * 40 + ['Madonna'] * 40 + ['Cyndi Lauper'] * 40 + ['ABBA'] * 40
    picks, by, _ = mix(fw, artists, ['madonna', 'cyndi lauper'], 'cher')
    named = [artists[i] for i in picks]
    assert len(picks) == SIM_MIX == len(set(picks))
    assert named.count('Cher') == SIM_OWN and 'ABBA' not in named
    rest = SIM_MIX - SIM_OWN
    assert [named.count('Madonna'), named.count('Cyndi Lauper')] == [(rest + 1) // 2, rest // 2] and by == 3


def test_a_mix_shares_itself_among_the_similar_artists(fw):
    """Each similar artist adds one song a round, so a big catalogue doesn't crowd a small one out."""
    artists = ['Madonna'] * 200 + ['Cyndi Lauper'] * 2 + ['Sonny & Cher'] * 200
    picks, by, _ = mix(fw, artists, ['madonna', 'cyndi lauper', 'sonny & cher'], 'cher')
    named = [artists[i] for i in picks]
    assert len(picks) == SIM_MIX and named.count('Cyndi Lauper') == 2 and by == 3
    assert abs(named.count('Madonna') - named.count('Sonny & Cher')) <= 1


def test_a_small_library_gives_what_it_has(fw):
    artists = ['Cher'] * 2 + ['Madonna'] * 3
    picks, by, _ = mix(fw, artists, ['madonna'], 'cher')
    assert sorted(picks) == [0, 1, 2, 3, 4] and by == 2


def test_names_match_case_aside_and_without_a_leading_the(fw):
    artists = ['the beatles', 'THE BEATLES', 'Beatles', 'Beatles Revival', 'Cher']
    picks, by, _ = mix(fw, artists, ['beatles'], 'cher')
    assert sorted(picks) == [0, 1, 2, 4] and by == 2


def test_songs_with_no_artist_or_none_to_pick_are_passed_over(fw):
    artists = [None, '', 'Madonna', None]
    picks, by, _ = mix(fw, artists, ['madonna', ''], 'cher')
    assert picks == [2] and by == 1


def test_the_artist_alone_is_no_mix(fw):
    """Only the playing artist's songs: nothing like them is in the library."""
    assert mix(fw, ['Cher'] * 10 + ['ABBA'], ['madonna'], 'cher')[:2] == ([], 0)
    assert mix(fw, [], ['madonna'], 'cher')[:2] == ([], 0)


def test_the_artist_is_never_taken_for_a_similar_one(fw):
    picks, by, _ = mix(fw, ['Cher'] * 10 + ['Madonna'], ['cher', 'madonna'], 'cher')
    assert len(picks) == SIM_OWN + 1 and by == 2


def test_the_mix_is_shuffled_by_the_random_it_is_given(fw):
    artists = ['Cher'] * 10 + ['Madonna'] * 30 + ['Cyndi Lauper'] * 30
    kept, _, calls = mix(fw, artists, ['madonna', 'cyndi lauper'], 'cher')  # n - 1: every swap with itself
    assert calls and kept[:SIM_OWN] == list(range(SIM_OWN))  # the artist's first, then a round at a time
    assert [artists[i] for i in kept[SIM_OWN:SIM_OWN + 4]] == ['Madonna', 'Cyndi Lauper'] * 2
    swapped, _, _ = mix(fw, artists, ['madonna', 'cyndi lauper'], 'cher', rnd=lambda n: 0)
    assert sorted(swapped) != sorted(kept) or swapped != kept
    assert len(swapped) == SIM_MIX and [artists[i] for i in swapped].count('Cher') == SIM_OWN


# The row's press, end to end: stock's queue, its library, the card's .scrobble.ini, Wi-Fi, Last.fm.
class Ini:
    """.scrobble.ini's [LASTFM] API_KEY, as stock's toolsReadConfig reads it: what was asked is kept."""
    def __init__(self, fw, key=b''):
        self.key, self.asked = key, []
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        self.asked.append((C.string_at(path), C.string_at(section), C.string_at(key)))
        value = self.key if (C.string_at(path), C.string_at(section), C.string_at(key)) == (
            b'/mnt/mmc/.scrobble.ini', b'LASTFM', b'API_KEY') else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
        return 0


class Thread:
    """pthread_create keeps the worker, which runs when the test says or at pthread_join."""
    def __init__(self, fw, starts=True):
        self.start, self.ran = None, False
        fw.mock('fw_pthread_create', self.create if starts else lambda *_: 11)  # EAGAIN
        fw.mock('fw_pthread_join', lambda thread, *_: self.run())

    def create(self, thread, attr, start, arg, *_):
        C.c_ulong.from_address(thread).value = THREAD
        self.start, self.ran = start, False

    def run(self):
        if self.start and not self.ran:
            self.ran = True
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.start)(None)


class Q2:
    """Now Playing on Cher's song, a library of Cher, Madonna, The Beatles and ABBA, a Last.fm key and Wi-Fi."""
    def __init__(self, fw, mcl, key=b'0123abcd', wifi=3, replies=None, starts=True, songs=None):
        self.fw, self.ui, self.song = fw, Ui(fw), Songs()
        make = self.song
        self.playing = make(b'/m/cher/believe.flac', b'Believe', b'Cher')
        self.songs = songs if songs is not None else (
            [self.playing] + [make(b'/m/cher/%d.flac' % i, b'C%d' % i, b'Cher') for i in range(8)]
            + [make(b'/m/madonna/%d.flac' % i, b'M%d' % i, b'Madonna') for i in range(30)]
            + [make(b'/m/beatles/%d.flac' % i, b'B%d' % i, b'the beatles') for i in range(30)]
            + [make(b'/m/abba/%d.flac' % i, b'A%d' % i, b'ABBA') for i in range(30)])
        self.library = Library(fw, self.ui, self.songs)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.ui.deques.new([self.playing])
        mcl.pos, mcl.type = 0, 0xf001
        self.ini, self.thread = Ini(fw, key), Thread(fw, starts)
        self.net = Net(fw, {LASTFM: similar('Madonna', 'The Beatles', 'Sonny & Cher')} if replies is None else replies)
        fw.mock('get_wifisignal', lambda *_: wifi)
        self.sim = fw.var('similar__sim', Sim)

    def press(self):
        return self.fw.fn('similar_click', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def answer(self):
        """The worker runs, and the row's timer sees it end."""
        self.thread.run()
        return self.ui.fire('sim_wait')

    def played(self):
        (queue, at, cls), = self.ui.played
        return [field(r, 'artist').decode() for r in queue], queue, at, cls


def test_more_like_this_plays_the_librarys_songs_by_similar_artists(fw, mcl):
    q = Q2(fw, mcl)
    assert q.press() == 0
    assert q.ui.toasts == ['Asking Last.fm…'] and q.ui.pending('sim_wait') and not q.net.urls
    assert q.answer() == 0 and not q.ui.pending('sim_wait')
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(q.net.urls[0]).query)
    assert len(q.net.urls) == 1 and query['artist'] == ['Cher'] and query['api_key'] == ['0123abcd']
    artists, queue, at, cls = q.played()
    assert len(queue) == SIM_MIX and artists.count('Cher') == SIM_OWN and q.playing not in queue
    assert set(artists) == {'Cher', 'Madonna', 'the beatles'} and 0 <= at < SIM_MIX and cls == 0xf001
    assert q.library.asked == [3]  # Shuffle Songs' own query: every song, unsorted
    assert (2, 1) in [a[:2] for _, a in fw.calls('config_playmode')]  # stock's Shuffle on, as Shuffle Songs
    assert q.ui.toasts[-1] == f'Playing {SIM_MIX} songs by 3 artists'
    assert not q.sim.thread and not q.sim.names and not q.sim.running


class Pages:
    """The window stack: playing_page opens a page of that name over the top one; a forced close takes it out."""
    def __init__(self, fw, ui, *names):
        self.ui, self.closed = ui, []
        self.pages = [self.open(name) for name in names]
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('window_manager_close_window_force', lambda wm, w, *_: self.close(w))

    def open(self, name):
        w = self.ui.new('window_create', 0, 0, 0, 0, 0)
        self.ui.name[w] = name
        return w

    def navigate(self, name, info, *_):
        if C.string_at(name) == b'playing_page':
            self.open('playing_page')
        self.ui.navigate(name, info)

    def close(self, w):
        self.ui.windows.remove(w)
        self.closed.append(w)
        return 0

    def names(self):
        return [self.ui.name.get(w) for w in self.ui.windows]


def test_the_mix_takes_the_place_of_the_now_playing_it_was_asked_from(fw, mcl):
    """Return from the mix's Now Playing goes back where Now Playing came from: Home, not the last queue's page."""
    q = Q2(fw, mcl)
    p = Pages(fw, q.ui, 'home_page', 'playing_page')
    home, np = p.pages
    q.press()
    q.answer()
    assert len(q.ui.played) == 1 and p.closed == [np]
    assert p.names() == ['home_page', 'playing_page'] and q.ui.windows[0] == home


def test_the_mix_leaves_another_page_on_top_where_it_is(fw, mcl):
    """Asked from Now Playing, then left for a list: the mix opens over the list, which stays."""
    q = Q2(fw, mcl)
    p = Pages(fw, q.ui, 'home_page', 'playing_page')
    q.press()
    p.open('songs_page')
    q.answer()
    assert not p.closed and p.names() == ['home_page', 'playing_page', 'songs_page', 'playing_page']


def test_a_reply_is_waited_for(fw, mcl):
    q = Q2(fw, mcl)
    q.press()
    q.sim.running = 1  # the worker still asking
    assert q.ui.fire('sim_wait') == REPEAT and not q.ui.played


def test_the_mix_waits_while_a_toast_is_up(fw, mcl, st):
    q = Q2(fw, mcl)
    q.press()
    q.thread.run()
    st('toasts').value = 1
    assert q.ui.fire('sim_wait') == REPEAT and not q.ui.played
    st('toasts').value = 0
    assert q.ui.fire('sim_wait') == 0 and q.ui.played


def test_no_api_key_says_so_and_asks_nothing(fw, mcl):
    q = Q2(fw, mcl, key=b'')
    q.press()
    assert q.ui.toasts == ['No Last.fm API key in .scrobble.ini']
    assert not fw.calls('fw_pthread_create') and not q.ui.timers and not q.net.urls


def test_no_wifi_says_so_and_asks_nothing(fw, mcl):
    q = Q2(fw, mcl, wifi=0)
    q.press()
    assert q.ui.toasts == ['Connect to Wi-Fi first'] and not fw.calls('fw_pthread_create') and not q.ui.timers


def test_a_song_with_no_artist_asks_nothing(fw, mcl):
    q = Q2(fw, mcl)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = q.ui.deques.new([q.song(b'/m/x.flac', b'X')])
    q.press()
    assert q.ui.toasts == ['This song has no artist'] and not fw.calls('fw_pthread_create')


@pytest.mark.parametrize('reply, toast', [(NOT_FOUND, 'Last.fm: The artist you supplied could not be found'),
                                          (None, "Can't reach Last.fm"),
                                          (similar(), 'Last.fm knows no artists like Cher'),
                                          (similar('Sonny & Cher', 'Cyndi Lauper'), 'No similar artists in the library')])
def test_what_makes_no_mix_says_so_and_plays_nothing(fw, mcl, reply, toast):
    q = Q2(fw, mcl, replies={} if reply is None else {LASTFM: reply})
    q.press()
    assert q.answer() == 0
    assert q.ui.toasts == ['Asking Last.fm…', toast] and not q.ui.played
    assert not q.sim.thread and not q.sim.names


def test_no_library_query_without_similar_artists(fw, mcl):
    q = Q2(fw, mcl, replies={LASTFM: NOT_FOUND})
    q.press()
    q.answer()
    assert not q.library.asked


def test_a_press_while_it_asks_starts_nothing_more(fw, mcl):
    q = Q2(fw, mcl)
    q.press()
    q.press()
    assert len(fw.calls('fw_pthread_create')) == 1 and len(q.ui.pending('sim_wait')) == 1
    assert q.ui.toasts == ['Asking Last.fm…'] * 2


def test_a_second_mix_asks_afresh(fw, mcl):
    q = Q2(fw, mcl)
    q.press()
    q.answer()
    q.press()
    q.answer()
    assert len(q.net.urls) == 2 and len(q.ui.played) == 2


def test_a_thread_that_wont_start_says_so(fw, mcl):
    q = Q2(fw, mcl, starts=False)
    q.press()
    assert q.ui.toasts == ["Can't ask Last.fm"] and not q.ui.timers
    assert (q.sim.thread, q.sim.names, q.sim.running) == (0, None, 0)


def test_usb_storage_stops_it_quietly(fw, mcl):
    q = Q2(fw, mcl)
    q.press()
    fw.fn('similar_stop', None)()
    assert q.sim.cancel == 1 and [c[1][0] for c in fw.calls('fw_pthread_join')] == [THREAD]
    assert not q.sim.thread and not q.sim.names
    assert q.ui.fire('sim_wait') == 0 and q.ui.toasts == ['Asking Last.fm…'] and not q.ui.played


def test_usb_storage_with_nothing_asked_joins_nothing(fw, mcl):
    Q2(fw, mcl)
    fw.fn('similar_stop', None)()
    assert not fw.calls('fw_pthread_join')


def test_busy_while_it_asks(fw, mcl):
    q = Q2(fw, mcl)
    busy = fw.fn('similar_busy', C.c_int)
    assert busy() == 0
    q.press()
    assert busy() == 1
    q.answer()
    assert busy() == 0



def test_the_url_keeps_the_unreserved_marks_as_they_are(fw):
    out = C.create_string_buffer(2048)
    assert fw.fn('similar__sim_url', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 2048, b'a-b.c_d~e f', b'k') == 1
    assert '&artist=a-b.c_d~e%20f&' in out.value.decode()


def test_an_error_reply_without_a_message_is_refused_with_none(fw):
    Net(fw)
    assert parse(fw, b'{"error":29}') == (SIM_REFUSED, [], '')


def test_a_similar_artist_without_a_name_is_passed_over(fw):
    Net(fw)
    body = json.dumps({'similarartists': {'artist': [{'match': '1'}, {'name': 7}, {'name': ''}, {'name': 'Madonna'}]}}).encode()
    assert parse(fw, body)[:2] == (1, ['Madonna'])


XFER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_longlong, C.c_longlong, C.c_longlong, C.c_longlong)


def test_the_transfer_goes_on_until_usb_storage_stops_it(fw, mcl):
    """curl's progress callback (XFERINFOFUNCTION) answers 0 to go on; once USB storage stops the request, 1, and
    curl ends the transfer."""
    q, said = Q2(fw, mcl), []
    def reply(url):
        said.append(XFER(q.net.opts[20219])(None, 0, 0, 0, 0))
        return None if said[-1] else similar('Madonna')
    q.net.replies[LASTFM] = (reply,)
    q.press()
    q.answer()
    assert said == [0] and q.ui.played
    q.press()
    fw.fn('similar_stop', None)()  # its join runs the worker, which sees the stop at the progress call
    assert said == [0, 1] and len(q.ui.played) == 1


def test_a_reply_too_big_to_keep_is_no_answer(fw, mcl):
    q = Q2(fw, mcl, replies={LASTFM: similar('Madonna') + b' ' * (512 << 10)})  # SIM_MAX: 512 KB
    q.press()
    q.answer()
    assert q.ui.toasts == ['Asking Last.fm…', "Can't reach Last.fm"] and not q.ui.played


def test_no_curl_handle_is_no_answer(fw, mcl):
    q = Q2(fw, mcl)
    fw.mock('fw_curl_easy_init', lambda *_: 0)
    q.press()
    q.answer()
    assert q.ui.toasts == ['Asking Last.fm…', "Can't reach Last.fm"] and not q.net.urls and not q.ui.played


@pytest.mark.parametrize('queue, pos', [(None, 0), ([b'/m/cher/believe.flac'], 1)])
def test_nothing_playing_asks_nothing(fw, mcl, queue, pos):
    q = Q2(fw, mcl)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = q.ui.deques.new([q.playing]) if queue else 0
    mcl.pos = pos
    q.press()
    assert q.ui.toasts == ['This song has no artist'] and not fw.calls('fw_pthread_create')


def test_a_mix_holds_only_songs_not_other_records(fw, mcl):
    q = Q2(fw, mcl, songs=[])
    make = q.song
    songs = [q.playing, make(b'/m/cher/1.flac', b'C1', b'Cher'), make(b'/m/madonna/1.flac', b'M1', b'Madonna'),
             make(b'/m/madonna', b'Folder', b'Madonna', kind=1)]
    q.library = Library(fw, q.ui, songs)
    q.press()
    q.answer()
    artists, queue, _, _ = q.played()
    assert sorted(field(r, 'path') for r in queue) == [b'/m/cher/1.flac', b'/m/madonna/1.flac']
    assert q.ui.toasts[-1] == 'Playing 2 songs by 2 artists'
