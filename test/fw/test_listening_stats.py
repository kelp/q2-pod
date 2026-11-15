"""listening_stats.c: Library's Listening Stats, counted on a worker from the card's scrobble logs (the unsent
.scrobbler.log and the uploaded .scrobbler.log.sent): the periods by local time, the time played, and the top
artists, albums and songs of each, most played first."""
import calendar
import ctypes as C
import time

import pytest

from conftest import Files
from test_nav_library_pages import DOT, Ui, call

LOG, SENT = '/mnt/mmc/.scrobbler.log', '/mnt/mmc/.scrobbler.log.sent'
HEADER = '#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Q2 Pod\n'
NOW = calendar.timegm((2026, 11, 11, 12, 0, 0))  # a Wednesday, 12:00 UTC
ZONE = -5 * 3600  # the local time: 07:00 that Wednesday
# Each period's first second, local midnight: Monday the 9th, the 1st, and January 1st.
WEEK = calendar.timegm((2026, 11, 9, 0, 0, 0)) - ZONE
MONTH = calendar.timegm((2026, 11, 1, 0, 0, 0)) - ZONE
YEAR = calendar.timegm((2026, 1, 1, 0, 0, 0)) - ZONE
PERIODS = ['This Week', 'This Month', 'This Year', 'All Time']
COUNTING = 'Counting listens…'


def listen(artist, title, at=NOW - 60, album='', length=200, rating='L'):
    """One line as navigation.c's scrobble writes it: artist, album, title, track, length, rating, time, an empty
    MusicBrainz ID."""
    return f'{artist}\t{album}\t{title}\t1\t{length}\t{rating}\t{at}\t\n'


class Stats:
    """The card's two logs, the clock in ZONE, and the worker's thread, run when the test says."""
    def __init__(self, fw, log='', sent=None):
        self.fw, self.ui, self.threads = fw, Ui(fw), []
        files = {LOG: (HEADER + log).encode()} if log is not None else {}
        if sent is not None:
            files[SENT] = sent.encode()
        self.files = Files(fw, files)
        fw.mock('fw_time', lambda *_: NOW)
        fw.mock('fw_localtime_r', self.local)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append((fn, arg)) or 0)

    def local(self, at, out, *_):
        tm = time.gmtime(C.c_long.from_address(at).value + ZONE)
        fields = [tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900,
                  (tm.tm_wday + 1) % 7, tm.tm_yday - 1]
        for k, v in enumerate(fields):
            C.c_int.from_address(out + 4 * k).value = v
        return out

    def open(self):
        """The Library row pressed, the worker run to its end, the page shown."""
        call(self.fw, 'listening_stats')
        page = self.ui.top()
        assert self.ui.rows(page) == [(COUNTING, None)]
        fn, arg = self.threads[-1]
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(fn)(arg)
        self.ui.fire('stats_poll')
        return page

    def period(self, name):
        """The period's page, opened from the stats page: its rows."""
        page = self.open() if not self.ui.windows else self.ui.windows[0]
        self.ui.click(page, PERIODS.index(name))
        return self.ui.rows(self.ui.top())

    def top(self, name, kind):
        """A period's Top Artists (0), Top Albums (1) or Top Songs (2) rows."""
        self.period(name)
        self.ui.click(self.ui.top(), kind + 1)
        return self.ui.rows(self.ui.top())

    def back(self):
        self.ui.close(self.ui.top())


def test_the_page_lists_the_periods_once_the_listens_are_counted(fw):
    s = Stats(fw, listen('A', 'x'))
    page = s.open()
    assert s.ui.name[page] == 'stats_page' and s.ui.captions(page) == PERIODS
    assert not s.ui.pending('stats_poll')
    call(fw, 'listening_stats')  # pressed again while it is open
    assert s.ui.windows == [page]


def test_the_poll_waits_for_the_worker(fw):
    s = Stats(fw, listen('A', 'x'))
    call(fw, 'listening_stats')
    assert s.ui.fire('stats_poll') == 8  # RET_REPEAT
    assert s.ui.rows(s.ui.top()) == [(COUNTING, None)]


def test_a_period_shows_its_time_played_and_how_many_of_each(fw):
    log = (listen('A', 'one', album='Hits', length=3000) + listen('A', 'two', album='Hits', length=600) +
           listen('B', 'one', length=125))
    s = Stats(fw, log)
    assert s.period('All Time') == [('Time played', f'1 h 2 min {DOT} 3 plays'), ('Top Artists', '2 artists'),
                                    ('Top Albums', '1 album'), ('Top Songs', '3 songs')]
    assert s.ui.name[s.ui.top()] == 'stats_period_page' and 'All Time' in s.ui.shown(s.ui.top())


def test_under_an_hour_shows_minutes_alone(fw):
    s = Stats(fw, listen('A', 'one', length=59) + listen('A', 'one', length=61))
    assert s.period('All Time')[0] == ('Time played', f'2 min {DOT} 2 plays')
    s.back()
    assert s.period('This Week')[0] == ('Time played', f'2 min {DOT} 2 plays')


def test_one_listen_is_one_play(fw):
    s = Stats(fw, listen('A', 'one', length=60))
    assert s.period('All Time')[0] == ('Time played', f'1 min {DOT} 1 play')
    s.ui.click(s.ui.top(), 1)
    assert s.ui.rows(s.ui.top()) == [('A', '1 play')]


def test_the_top_lists_count_plays_most_played_first(fw):
    log = (listen('A', 'one', album='Hits') * 2 + listen('B', 'two', album='Live') * 3 +
           listen('A', 'three', album='Hits'))
    s = Stats(fw, log)
    assert s.top('All Time', 0) == [('A', '3 plays'), ('B', '3 plays')]  # a tie: by name
    assert s.ui.name[s.ui.top()] == 'stats_top_page' and 'Top Artists' in s.ui.shown(s.ui.top())
    s.back()
    s.back()
    assert s.top('All Time', 1) == [('Hits', f'A {DOT} 3 plays'), ('Live', f'B {DOT} 3 plays')]
    s.back()
    s.back()
    assert s.top('All Time', 2) == [('two', f'B {DOT} 3 plays'), ('one', f'A {DOT} 2 plays'),
                                    ('three', f'A {DOT} 1 play')]


def test_ties_go_by_name_and_25_show(fw):
    log = ''.join(listen(f'a{k:02}', 'x') for k in range(29, -1, -1)) + listen('z', 'y') * 2
    s = Stats(fw, log)
    rows = s.top('All Time', 0)
    assert rows == [('z', '2 plays')] + [(f'a{k:02}', '1 play') for k in range(24)]
    s.back()
    s.back()
    assert s.period('All Time')[1] == ('Top Artists', '31 artists')


def test_an_album_is_its_artist_s_and_a_listen_without_one_has_none(fw):
    log = listen('X', 'one', album='Hits') + listen('Y', 'two', album='Hits') + listen('Y', 'three')
    s = Stats(fw, log)
    assert s.top('All Time', 1) == [('Hits', f'X {DOT} 1 play'), ('Hits', f'Y {DOT} 1 play')]
    s.back()
    s.back()
    assert s.top('All Time', 2) == [('one', f'X {DOT} 1 play'), ('three', f'Y {DOT} 1 play'),
                                    ('two', f'Y {DOT} 1 play')]


def test_uploaded_listens_count_with_the_unsent(fw):
    s = Stats(fw, listen('A', 'one'), sent=listen('A', 'one') + listen('B', 'two'))
    assert s.top('All Time', 0) == [('A', '2 plays'), ('B', '1 play')]


def test_either_log_alone_counts(fw):
    s = Stats(fw, None, sent=listen('A', 'one'))
    assert s.period('All Time')[0] == ('Time played', f'3 min {DOT} 1 play')


def test_skips_headers_and_damaged_lines_count_nothing(fw):
    good = listen('A', 'one', length=60)
    log = (listen('A', 'skipped', rating='S') + 'A\tB\tC\n' + listen('', 'no artist') + listen('A', '') +
           listen('A', 'bad time', at='12x') + listen('A', 'zero', at=0) + listen('A', 'big', at='12345678901') +
           '#TZ/UTC\n' + 'x' * 10000 + '\n' + good + listen('A', 'cut')[:-1])
    s = Stats(fw, log, sent='\n' + good)
    assert s.period('All Time')[0] == ('Time played', f'2 min {DOT} 2 plays')
    s.ui.click(s.ui.top(), 3)
    assert s.ui.rows(s.ui.top()) == [('one', f'A {DOT} 2 plays')]


def test_the_periods_start_at_local_midnight_monday_the_1st_and_january(fw):
    log = ''.join(listen('A', 'x', at=t, length=60) for t in (WEEK, WEEK - 1, MONTH, MONTH - 1, YEAR, YEAR - 1))
    s = Stats(fw, log)
    plays = {}
    for name in PERIODS:
        plays[name] = s.period(name)[0][1]
        s.back()
    assert plays == {'This Week': f'1 min {DOT} 1 play', 'This Month': f'3 min {DOT} 3 plays',
                     'This Year': f'5 min {DOT} 5 plays', 'All Time': f'6 min {DOT} 6 plays'}


def test_a_period_with_no_listens_says_so(fw):
    s = Stats(fw, listen('A', 'x', at=YEAR - 1))
    assert s.period('This Week')[0] == ('Time played', f'0 min {DOT} 0 plays')
    s.ui.click(s.ui.top(), 1)
    assert 'No listens' in s.ui.shown(s.ui.top())


def test_no_logs_say_no_listens_yet(fw):
    s = Stats(fw, None)
    page = s.open()
    assert 'No listens yet' in s.ui.shown(page) and len(s.ui.windows) == 1


def test_closing_while_counting_stops_the_worker(fw):
    s = Stats(fw, listen('A', 'x'))
    call(fw, 'listening_stats')
    s.back()
    assert len(fw.calls('fw_pthread_join')) == 1 and not s.ui.pending('stats_poll')
    call(fw, 'listening_stats')  # and it opens again
    assert s.ui.rows(s.ui.top()) == [(COUNTING, None)]


def test_no_thread_counts_on_the_ui_thread(fw):
    s = Stats(fw, listen('A', 'x'))
    fw.mock('fw_pthread_create', lambda *_: 11)  # EAGAIN
    call(fw, 'listening_stats')
    s.ui.fire('stats_poll')
    assert s.ui.captions(s.ui.top()) == PERIODS and not fw.calls('fw_pthread_join')
