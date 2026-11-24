#!/usr/bin/env python3
"""Device tests for the fork's features and card apps, run on a --dev Q2 over Wi-Fi (q2.py).

    Q2_HOST=192.168.1.45 python3 devtest.py OUT_DIR [TEST...]

Each test drives the Q2 from Home, checks what it can read (the shell, files, OCR of the screen) and keeps its
screenshots; OUT_DIR/report.md lists every check. A test that changes the Q2's settings or card undoes it.
Tests that would send data off the Q2 (scrobble uploads), unmount the card (Eject Card's last step) or remove
an app (App Store) are not here."""
import re
import sys
import time
import traceback
from pathlib import Path

from q2 import Q2

TESTS = {}


def test(fn):
    TESTS[fn.__name__] = fn
    return fn


class Run:
    def __init__(self, q2):
        self.q2 = q2
        self.results = []  # (test, ok, what, screenshot)
        self.current = ''

    def check(self, ok, what, screen=None):
        self.results.append((self.current, bool(ok), what, screen.path.name if screen else ''))
        print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f'  [{screen.path.name}]' if screen else ''))
        return ok

    def note(self, what, screen=None):
        self.results.append((self.current, None, what, screen.path.name if screen else ''))
        print(f'  note {what}' + (f'  [{screen.path.name}]' if screen else ''))


TITLE_SMALL = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from', 'in', 'of', 'on', 'or', 'the', 'to', 'with'}


def title_case(label):
    """Stock's row style: every word capitalised but the short ones after the first ("Go to Folder")"""
    words = [w for w in re.split(r'\s+', label.strip()) if re.match(r'[A-Za-z]', w)]
    return all(w[0].isupper() or (i and w.lower() in TITLE_SMALL) for i, w in enumerate(words))


def demo_pid(q):
    return q.state().get('demo')


# --- the firmware: payload-room ---------------------------------------------------------------------------

@test
def payload(r):
    """payload-room: demo runs with the payload and its .scratch above it (0xbe0000), and stays up."""
    q = r.q2
    pid = demo_pid(q)
    r.check(pid and pid.isdigit(), f'demo is running (pid {pid})')
    maps = q.out_of(f'cat /proc/{pid}/maps')
    spans = [tuple(int(x, 16) for x in m) for m in re.findall(r'^([0-9a-f]+)-([0-9a-f]+) ', maps, re.M)]
    payload = [s for s in spans if s[0] <= 0xb00000 < s[1]]
    r.check(payload, 'a mapping holds the payload at 0xb00000')
    covered = any(a <= 0xbe0000 and b >= 0xc08444 for a, b in spans) or \
        (payload and max(b for a, b in spans if a < 0xc08444 and b > 0xbe0000) >= 0xc08444)
    r.check(covered, '.scratch (0xbe0000-0xc08444) is mapped')


# --- card apps -------------------------------------------------------------------------------------------

def open_app(q, name):
    q.home()
    q.tap_text('Library', exact=True)
    q.tap_text('Apps', exact=True)
    return q.tap_text(name, exact=True)


@test
def q2_top(r):
    """q2-top: starts from Library → Apps, shows its four boxes with the Q2's 2 cores and wlan0, scrolls, sorts,
    costs little, and Return closes it."""
    q = r.q2
    r.check(open_app(q, 'System Monitor'), 'Library → Apps lists System Monitor')
    time.sleep(4)
    s = q.shot('q2-top running')
    pid = q.out_of('pidof q2-top')
    r.check(pid.isdigit(), f'q2-top is running (pid {pid})', s)
    for word in ('cpu', 'mem', 'net', 'proc', 'Total', 'Used', 'Avail', 'Cache', 'Free', 'PID', 'demo'):
        r.check(s.has(word), f'shows {word!r}', s)
    cores = q.out_of('grep -c "^cpu[0-9]" /proc/stat')
    nets = ' '.join(q.out_of('ls /sys/class/net').split())
    r.note(f'the Q2 has {cores} cores and {nets}: C0, C1 and wlan0 are checked by eye (6x12 text is below OCR)', s)
    r.check(not q.out_of('cat /mnt/mmc/.top/launch-error.txt 2>/dev/null'), 'no launch error')
    # cost over 20 s, from its own /proc stat
    before = q.out_of(f'cut -d" " -f14,15 /proc/{pid}/stat; cut -d" " -f1 /proc/uptime').split()
    time.sleep(20)
    after = q.out_of(f'cut -d" " -f14,15 /proc/{pid}/stat; cut -d" " -f1 /proc/uptime; '
                     f'grep VmRSS /proc/{pid}/status').split()
    ticks = int(after[0]) + int(after[1]) - int(before[0]) - int(before[1])
    secs = float(after[2]) - float(before[2])
    share = ticks / secs  # percent of one core at 100 ticks a second
    r.check(share < 3, f'CPU {share:.2f}% of one core over {secs:.0f} s (< 3%)')
    r.check(int(after[4]) < 4096, f'RSS {after[4]} kB (< 4 MB)')
    q.wheel(3)
    time.sleep(1)
    q.shot('q2-top wheel')
    q.key('centre')
    time.sleep(1.2)
    s2 = q.shot('q2-top sorted by memory')
    r.note('wheel moved the selection; Centre sorted by memory (compare the shots)', s2)
    q.key('return')
    time.sleep(2)
    r.check(not q.out_of('pidof q2-top'), 'Return closes it')
    s3 = q.shot('after q2-top')
    r.check(s3.has('Apps') or s3.has('System Monitor'), 'back on the Apps page', s3)


# --- EQ: peq-import-all, eq-import-skip-appledouble, preset-name-ellipsis, headphone-picker, band channel ---

EQ_DIR = '/mnt/mmc/EQ'
PRESETS = '/mnt/data/peq-presets'
LONG = 'DevTest Gamma With A Very Long Preset Name For Rows'
TEST_FILES = {
    'DevTest Alpha.txt': 'Preamp: -6.0 dB\\nFilter 1: ON LSC Fc 105 Hz Gain 5.0 dB Q 0.70\\n'
                         'Filter 2: ON PK Fc 200 Hz Gain -2.0 dB Q 1.00\\nFilter 3: ON PK Fc 3000 Hz Gain 3.0 dB Q 2.00\\n',
    'DevTest Beta.txt': 'Preamp: -3.0 dB\\nFilter 1: ON PK Fc 60 Hz Gain 3.0 dB Q 0.80\\n'
                        'Filter 2: ON HSC Fc 8000 Hz Gain -2.5 dB Q 0.70\\n',
    LONG + '.txt': 'Preamp: -2.0 dB\\nFilter 1: ON PK Fc 1000 Hz Gain -2.0 dB Q 1.40\\n',
}


def eq_files(q):
    """The test presets in /EQ, and a macOS AppleDouble file beside one, which the import must not list"""
    cmd = [f'mkdir -p "{EQ_DIR}"']
    cmd += [f'printf "{body}" > "{EQ_DIR}/{name}"' for name, body in TEST_FILES.items()]
    cmd.append(f'printf "\\0\\5\\26\\7\\0\\2\\0\\0Mac OS X        " > "{EQ_DIR}/._DevTest Alpha.txt"; sync')
    q.sh('; '.join(cmd))


def open_advanced(q):
    q.home()
    q.tap_text('Settings', exact=True)
    q.tap_text('Playback', exact=True)
    q.tap_text('EQ', exact=True, below=40)
    time.sleep(1)
    s = q.shot('EQ')
    tab = s.find('Advanced', exact=True, below=30)
    if tab:
        q.tap(*tab.centre)
        time.sleep(1)
    return q.shot('Advanced')


def presets_saved(q):
    return set(q.out_of(f'ls "{PRESETS}"').splitlines())


@test
def eq_import(r):
    """Import from SD /EQ: Import all is the first row, ._ files are not listed, a long name ends in an
    ellipsis, Import all saves every file once and skips names already saved."""
    q = r.q2
    q.sh(f'rm -f "{PRESETS}"/DevTest*.peq')  # a test run before this one
    eq_files(q)
    open_advanced(q)
    q.tap_text('Presets', exact=True)
    time.sleep(1)
    q.tap_text('Import from SD /EQ')
    time.sleep(1.5)
    s = q.shot('import list')
    rows = sorted((t for t in s.texts if t.y > 80), key=lambda t: t.y)
    r.check(rows and rows[0].text.strip() == 'Import all', 'Import all is the first row', s)
    r.check(s.has('DevTest Alpha.txt') and s.has('DevTest Beta.txt'), 'the test files are listed', s)
    r.check(not s.has('._'), 'the AppleDouble file is not listed', s)
    long_row = s.find('DevTest Gamma')
    r.check(long_row and long_row.text.rstrip().endswith(('...', '…')) and long_row.x + long_row.w < 370,
            'the long name ends in an ellipsis inside the row', s)
    before = presets_saved(q)
    q.tap_text('Import all', exact=True)
    s = q.shot('imported', settle=False)
    r.check(s.has('Imported 3'), 'Import all says "Imported 3"', s)
    added = presets_saved(q) - before
    r.check(added >= {'DevTest Alpha.peq', 'DevTest Beta.peq', LONG + '.peq'}, f'saved {sorted(added)}')
    time.sleep(3)
    q.tap_text('Import all', exact=True)
    s = q.shot('imported again', settle=False)
    r.check(s.has('3 already saved'), 'a second Import all skips the 3 saved names', s)
    r.check(presets_saved(q) - before == added, 'and saves nothing more')
    q.key('return')
    time.sleep(1)
    for page in ('Rename a preset', 'Delete a preset'):
        q.tap_text(page, exact=True)
        time.sleep(1.5)
        s = q.shot(page)
        t = s.find('Gamma')  # OCR sometimes reads 'Dev Test'
        r.check(t and t.text.rstrip().endswith(('...', '…')), f'{page}: the long name ends in an ellipsis', s)
        q.key('return')
        time.sleep(1)


def headphones(q):
    q.swipe(187, 5, 187, 220)
    time.sleep(1.2)
    s = q.shot('quick settings')
    label = s.find('Playback', exact=True)
    x, y = (label.centre[0], label.y - 40) if label else (230, 145)  # the tile's icon, above its label
    q.swipe(x, y, x, y, 1200)
    time.sleep(1.5)
    return q.shot('headphones')


@test
def headphone_picker(r):
    """Quick Settings → hold Playback opens Headphones: EQ settings, EQ Off and the saved presets; a pick
    plays at once and closes the list, and the list marks it Playing. Long names must not run into the mark."""
    q = r.q2
    q.home()
    s = headphones(q)
    r.check(s.find('Headphones', exact=True), 'the hold opens Headphones', s)
    r.check(s.has('EQ settings') and s.has('EQ Off'), 'it lists EQ settings and EQ Off', s)
    r.check(s.has('DevTest Alpha') and s.has('DevTest Beta'), 'it lists the saved presets', s)
    labels = [t.text.strip() for t in sorted((t for t in s.texts if t.y > 80 and re.match(r'[A-Za-z]', t.text)),
                                             key=lambda t: t.y)[:2]]
    r.check(len({title_case(x) for x in labels}) == 1, f'its own rows share one case style: {labels}', s)
    active = q.out_of('md5sum /mnt/data/peq-active')
    q.tap_text(LONG[:20])
    time.sleep(1.5)
    s = q.shot('after the pick')
    r.check(not s.find('Headphones', exact=True), 'a pick closes the list', s)
    r.check(q.out_of('md5sum /mnt/data/peq-active') != active, 'the pick changes what plays (peq-active)')
    s = headphones(q)
    mark = [t for t in s.texts if t.text.strip() == 'Playing']
    name = s.find('DevTest Gamma')
    r.check(mark, 'the playing preset is marked Playing', s)
    clear = name and all(name.x + name.w < m.x or abs(m.y - name.y) > 10 for m in mark)
    r.check(clear and name.text.rstrip().endswith(('...', '…')),
            'the long name ends in an ellipsis before the Playing mark', s)
    q.key('return')
    time.sleep(1)


@test
def eq_band_channel(r):
    """peq-band-enable-keeps-channel: a band set to Left stays Left when its Band switch goes off and on."""
    q = r.q2
    open_advanced(q)
    q.tap_text('1 Peaking')
    time.sleep(1.2)
    s = q.shot('band 1')
    r.check(s.find('Band 1', exact=True), 'band 1 opens', s)
    for _ in range(3):  # Both → Left (→ Right → Both)
        if s.find('Left', exact=True):
            break
        q.tap(*s.find('Channels', exact=True).centre)
        time.sleep(1)
        s = q.shot('channel')
    r.check(s.find('Left', exact=True), 'Channels set to Left', s)
    switch = s.find('Band', exact=True, below=150)
    q.tap(*switch.centre)
    time.sleep(1)
    s = q.shot('band off')
    r.check(not s.find('Channels', exact=True), 'band off hides its settings', s)
    q.tap(*switch.centre)
    time.sleep(1)
    s = q.shot('band on')
    r.check(s.find('Left', exact=True), 'band on again: still Left', s)
    for _ in range(3):
        if s.find('Both', exact=True):
            break
        q.tap(*s.find('Channels', exact=True).centre)
        time.sleep(1)
        s = q.shot('channel back')
    r.check(s.find('Both', exact=True), 'Channels back to Both', s)
    q.key('return')


def t9(q, word):
    """Types word on Q2 Pod's T9 box (lower case letters only), a key's letter by repeated taps"""
    keys = {'abc': (190, 127), 'def': (260, 127), 'ghi': (115, 182), 'jkl': (190, 182), 'mno': (260, 182),
            'pqrs': (115, 237), 'tuv': (190, 237), 'wxyz': (260, 237)}
    for ch in word:
        for letters, (x, y) in keys.items():
            if ch in letters:
                for _ in range(letters.index(ch) + 1):
                    q.remote('tap', x, y)
                time.sleep(1.3)


@test
def opra_search(r):
    """Presets → Search OPRA: the card's database finds Koss headphones, a headphone lists its presets with who
    made them, and picking one saves it without changing what plays."""
    q = r.q2
    have = q.out_of('ls /mnt/mmc/.q2/opra.jsonl 2>/dev/null')
    if not r.check(have, 'the OPRA database is on the card'):
        return
    open_advanced(q)
    q.tap_text('Presets', exact=True)
    time.sleep(1)
    q.tap_text('Search OPRA', exact=True)
    time.sleep(2)
    for _ in range(16):  # the box keeps the last search's text
        q.remote('tap', 330, 127)
    t9(q, 'koss')
    q.tap(330, 237)
    time.sleep(3)
    s = q.shot('OPRA results')
    r.check(s.has('Koss SP330') and s.has('Koss Pro4AA'), 'searching "koss" finds Koss headphones', s)
    q.tap_text('Koss SP330', exact=True)
    time.sleep(2)
    s = q.shot('SP330 presets')
    r.check(s.has('AutoEQ') and s.has('Measured by'), 'a headphone lists its presets and who made them', s)
    q.sh(f'rm -f "{PRESETS}/Koss SP330 (AutoEQ, Measured by Innerfidelity).peq"')
    active, before = q.out_of('md5sum /mnt/data/peq-active'), presets_saved(q)
    q.tap_text('AutoEQ', exact=True)
    s = q.shot('saved', settle=False)
    r.check(s.has('Preset saved'), 'picking one saves it', s)
    r.check(presets_saved(q) - before, f'a preset file was added: {sorted(presets_saved(q) - before)}')
    r.check(q.out_of('md5sum /mnt/data/peq-active') == active, 'what plays is unchanged')
    q.sh(f'rm -f "{PRESETS}/Koss SP330 (AutoEQ, Measured by Innerfidelity).peq"')


# --- Library: listening-stats, more-like-this ------------------------------------------------------------

@test
def listening_stats(r):
    """Library → Listening Stats: four periods; All Time's plays, time and artists match .scrobbler.log."""
    q = r.q2
    q.home()
    q.tap_text('Library', exact=True)
    s = q.tap_text('Listening Stats', exact=True)
    r.check(s, 'Library lists Listening Stats', s)
    time.sleep(2)
    s = q.shot('Listening Stats')
    for period in ('This Week', 'This Month', 'This Year', 'All Time'):
        r.check(s.has(period), f'lists {period}', s)
    log = q.out_of("awk -F'\\t' '$6==\"L\" {n++; s+=$5; a[$1]=1} END {k=0; for (x in a) k++; print n, s, k}' "
                   '/mnt/mmc/.scrobbler.log')
    plays, secs, artists = (int(x) for x in log.split())
    q.tap_text('All Time', exact=True)
    time.sleep(2)
    s = q.shot('All Time')
    shown = s.words()
    r.check(f'{plays} plays' in shown, f'All Time shows {plays} plays', s)
    r.check(f'{secs // 3600} h {secs % 3600 // 60} min' in shown, f'and {secs // 3600} h {secs % 3600 // 60} min', s)
    r.check(f'{artists} artists' in shown, f'and {artists} artists', s)
    rows = [t.text for t in s.texts if t.y > 80 and t.h > 12 and re.match(r'[A-Z]', t.text)]
    r.check(len({title_case(x) for x in rows}) == 1, f'its rows share one case style: {rows}', s)


@test
def more_like_this(r):
    """Now Playing → hold Play/Pause → More Like This: Title Case like the menu's other rows; asks Last.fm and
    plays 50 songs, at most 5 by the playing artist."""
    q = r.q2
    q.home()
    q.tap_text('Now Playing', exact=True)
    time.sleep(2)
    first = q.shot('Now Playing').words()
    time.sleep(2.5)
    s = q.shot('Now Playing')
    if s.words() == first:  # the time stands still: paused
        q.key('play')
        time.sleep(2)
    artist = q.out_of('strings /mnt/data/ringnav-queue | grep -m1 "^/mnt/mmc"')
    q.key('play', 1200)
    time.sleep(1.5)
    row, labels = None, []
    for _ in range(8):
        s = q.shot('hold menu')
        labels += [t.text for t in s.texts if t.y > 40 and t.x < 120 and re.match(r'[A-Z]', t.text)]
        row = s.find('like this')
        if row:
            break
        q.wheel(3)
        time.sleep(0.6)
    if not r.check(row, 'the hold menu has More Like This', s):
        return
    r.check(title_case(row.text), f'its label is Title Case like the others: "{row.text}"', s)
    q.tap(*row.centre)
    toast = ''
    for _ in range(12):
        time.sleep(0.7)
        toast = q.shot('asking', settle=False).words()
        if 'Playing' in toast and 'songs' in toast:
            break
    m = re.search(r'Playing (\d+) songs by (\d+) artists', toast)
    r.check(m and m.group(1) == '50', f'toast: {m.group(0) if m else toast}')
    queue = q.out_of('strings /mnt/data/ringnav-queue | grep "^/mnt/mmc"').splitlines()
    r.check(len(queue) == 50, f'the queue holds {len(queue)} songs')
    r.note(f'the song it started from: {artist}')
    q.key('play')  # pause


# --- System: eject-card ------------------------------------------------------------------------------------

def png_pixels(path):
    """(width, height, rows of (r, g, b)) of an 8-bit RGB or RGBA PNG, with the standard library"""
    import struct
    import zlib
    data = Path(path).read_bytes()
    pos, idat = 8, b''
    w = h = kind = 0
    while pos < len(data):
        n, typ = struct.unpack('>I4s', data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b'IHDR':
            w, h, _, kind = struct.unpack('>IIBB', body[:10])
        elif typ == b'IDAT':
            idat += body
        pos += 12 + n
    bpp = {2: 3, 6: 4}[kind]
    raw, rows, prev = zlib.decompress(idat), [], bytearray(w * bpp)
    for y in range(h):
        f, line = raw[y * (w * bpp + 1)], bytearray(raw[y * (w * bpp + 1) + 1:(y + 1) * (w * bpp + 1)])
        for i in range(len(line)):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            p = a + b - c
            pred = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else (b if abs(p - b) <= abs(p - c) else c)
            line[i] = (line[i] + (0, a, b, (a + b) // 2, pred)[f]) & 0xff
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)])
        prev = line
    return w, h, rows


def icon(path, row):
    """The row's 40 px round icon left of its label, as pixels"""
    _, _, px = png_pixels(path)
    cy = row.y + row.h // 2
    return [px[y][28:68] for y in range(max(cy - 20, 0), min(cy + 20, len(px)))]


@test
def eject_card_row(r):
    """System → Eject Card: after System Update, Title Case, with an icon of its own. (Its press unmounts the
    card, which only a hand can undo, so the press itself is tested by hand.)"""
    q = r.q2
    q.home()
    q.tap_text('Settings', exact=True)
    q.tap_text('System', exact=True)
    s = None
    for _ in range(6):
        s = q.shot('System')
        if s.find('Eject Card') and s.find('System Update'):
            break
        q.wheel(3)
        time.sleep(0.5)
    eject, update = s.find('Eject Card', exact=True), s.find('System Update', exact=True)
    r.check(eject, 'System lists Eject Card', s)
    r.check(eject and update and eject.y > update.y, 'after System Update', s)
    r.check(eject and title_case(eject.text), 'Title Case', s)
    if eject and update:
        r.check(icon(s.path, eject) != icon(s.path, update), 'its icon differs from System Update\'s', s)


# --- Roon and AirPlay: q2-roon, airplay-remote, airplay-np -------------------------------------------------

@test
def roon(r):
    """Streaming → Roon starts q2-roon: it finds the core and pairs, lists Now Playing, Library, Search and
    Zones, and its cost is measured."""
    q = r.q2
    q.home()
    q.tap_text('Streaming', exact=True)
    s = q.tap_text('Roon')
    r.check(s, 'Streaming lists Roon', s)
    time.sleep(6)
    s = q.shot('Roon')
    for row in ('Now Playing', 'Library', 'Search', 'Zones'):
        r.check(s.has(row), f'lists {row}', s)
    log = q.out_of('grep -E "discovery: found|status ready|paired" /mnt/mmc/.roon/log | tail -5')
    r.check('discovery: found' in log, 'discovery finds the core: ' +
            (re.findall(r'found .*', log) or ['-'])[-1])
    paired = 'status ready' in log or 'paired' in log or q.out_of('ls /mnt/mmc/.roon/roon.json 2>/dev/null')
    r.check(paired, 'paired with the core (Roon → Settings → Extensions → Q2 Roon enabled)')
    pid = q.out_of('pidof q2-roon.bin || pidof q2-roon')
    if pid:
        before = q.out_of(f'cut -d" " -f14,15 /proc/{pid}/stat; cut -d" " -f1 /proc/uptime').split()
        time.sleep(20)
        after = q.out_of(f'cut -d" " -f14,15 /proc/{pid}/stat; cut -d" " -f1 /proc/uptime; '
                         f'grep -E "VmRSS|Threads" /proc/{pid}/status; du -k /tmp/q2-roon.bin').split()
        share = (int(after[0]) + int(after[1]) - int(before[0]) - int(before[1])) / (float(after[2]) - float(before[2]))
        r.note(f'q2-roon: CPU {share:.2f}% of a core, RSS {after[4]} kB, {after[7]} threads, '
               f'/tmp copy {after[8]} kB')


# --- restore ---------------------------------------------------------------------------------------------

BACKUP = '/mnt/mmc/.devtest/mnt-data-20261122.tar'


@test
def restore(r):
    """Undo the tests: remove the test presets and /EQ files, put back the EQ state files from the backup, and
    reboot so demo reads them."""
    q = r.q2
    test_presets = ['DevTest Alpha.peq', 'DevTest Beta.peq', LONG + '.peq',
                    'Koss SP330 (AutoEQ, Measured by Innerfidelity).peq']
    q.sh('; '.join(f'rm -f "{PRESETS}/{p}"' for p in test_presets))
    q.sh(f'cd "{EQ_DIR}" && rm -f "._DevTest Alpha.txt" ' + ' '.join(f'"{n}"' for n in TEST_FILES) +
         f'; rmdir "{EQ_DIR}" 2>/dev/null; sync')
    r.check(not [p for p in presets_saved(q) if p.startswith('DevTest')], 'test presets removed')
    q.sh(f'cd /mnt/data && tar -xf {BACKUP} ./peq-active ./peq-outputs ./userequalizer ./config.ini && sync')
    q.remote('reboot', '--wait', timeout=600)
    sums = q.out_of(f'cd /mnt/data && mkdir -p /tmp/devtest-restore && tar -xf {BACKUP} -C /tmp/devtest-restore '
                    './peq-active ./userequalizer ./config.ini && for f in peq-active userequalizer config.ini; do '
                    'cmp -s $f /tmp/devtest-restore/$f && echo "$f same" || echo "$f differs"; done')
    for line in sums.splitlines():
        r.check(line.endswith('same'), f'after the reboot, {line}')


# ---------------------------------------------------------------------------------------------------------

def main():
    out = Path(sys.argv[1])
    names = sys.argv[2:] or list(TESTS)
    q = Q2(out)
    run = Run(q)
    q.wake()
    for name in names:
        run.current = name
        print(f'{name}: {TESTS[name].__doc__.splitlines()[0]}')
        try:
            TESTS[name](run)
        except Exception:
            run.check(False, 'raised: ' + traceback.format_exc().strip().splitlines()[-1])
            traceback.print_exc()
    q.home()
    lines = ['# Device test report', '', f'demo pid at the end: {demo_pid(q)}', '',
             '| Test | Result | Check | Screenshot |', '|---|---|---|---|']
    for test_name, ok, what, shot in run.results:
        mark = 'note' if ok is None else ('ok' if ok else '**FAIL**')
        lines.append(f'| {test_name} | {mark} | {what} | {shot} |')
    (out / 'report.md').write_text('\n'.join(lines) + '\n')
    failed = sum(1 for _, ok, _, _ in run.results if ok is False)
    print(f'{len(run.results)} checks, {failed} failed; {out / "report.md"}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
