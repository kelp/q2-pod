"""opra.c (docs/internals.md#opra): OPRA's EQ database read from the card's copy, indexed, searched and a preset
taken from it, and its download over Wi-Fi through demo's libcurl, on a card of fopen and friends (test_peq_page's
Card). The EQ page's rows for it are test_peq_page.py's."""
import ctypes as C
import json

import pytest

from test_peq_page import ACTIVE, EVT_DESTROY, EVT_IM_ACTION, EVT_VALUE_CHANGED, PRESETS, SAVED, Card, Preset, page, state, saved
from test_radio import Curl

DB, INDEX = '/mnt/mmc/.q2/opra.jsonl', '/mnt/mmc/.q2/opra.idx'
URL = 'http://opra.roonlabs.net/database_v1.jsonl'
RET_REMOVE, RET_REPEAT = 7, 8
PK, LSC, HSC = range(3)
r = C.byref


class Op(C.Structure):
    _fields_ = [('thread', C.c_ulong), *((k, C.c_int) for k in 'running cancel done stage presets result'.split()),
                ('bytes', C.c_uint), ('timer', C.c_uint), ('changed', C.c_void_p), ('shown', C.c_char * 64)]


def band(kind, frequency, gain, q):
    return {'type': kind, 'frequency': frequency, 'gain_db': gain, 'q': q}


def eq(product, author, details, bands, preamp=-4):
    return {'type': 'eq', 'id': f'{product}::{author}', 'data': {
        'author': author, 'details': details, 'type': 'parametric_eq',
        'parameters': {'gain_db': preamp, 'bands': bands}, 'product_id': product}}


ONE = [band('low_shelf', 105, -1.4, 0.7), band('peak_dip', 170, -1.9, 1.47), band('high_shelf', 10000, 2.2, 0.7)]
# The database's order: a vendor, then each of its products after the EQ records that are its.
RECORDS = [
    {'type': 'vendor', 'id': 'acme', 'data': {'name': 'Acme'}},
    eq('acme::one', 'AutoEQ', 'Measured by X', ONE),
    eq('acme::one', 'oratory1990', 'Harman Target', [band('peak_dip', 1000, 3, 2)], preamp=-2.5),
    {'type': 'product', 'id': 'acme::one', 'data': {'name': 'One HD 600', 'type': 'headphones', 'vendor_id': 'acme'}},
    eq('acme::two', 'AutoEQ', 'Measured by Y', [{'type': 'low_pass', 'frequency': 30, 'slope': 12}]),
    {'type': 'product', 'id': 'acme::two', 'data': {'name': 'Two', 'vendor_id': 'acme'}},
    {'type': 'vendor', 'id': 'zed', 'data': {'name': 'Zed Audio'}},
    eq('zed::z1', 'AutoEQ', 'Measured by Z', [band('peak_dip', 15, -3, 12)]),
    {'type': 'product', 'id': 'zed::z1', 'data': {'name': 'Z/1', 'vendor_id': 'zed'}},
]


def jsonl(records):
    return b''.join(json.dumps(x, ensure_ascii=False, separators=(',', ':')).encode() + b'\n' for x in records)


def spans(records):
    """Each record's line: its offset and length with the newline."""
    out, at = [], 0
    for x in records:
        n = len(jsonl([x]))
        out.append((at, n))
        at += n
    return out


def index_of(records):
    """The index opra_index writes: per product with usable presets, its name and their records' spans."""
    s = spans(records)
    return (f'Acme One HD 600\t{s[1][0]} {s[1][1]}\t{s[2][0]} {s[2][1]}\n'
            f'Zed Audio Z/1\t{s[7][0]} {s[7][1]}\n').encode()


def record(fw, line):
    p, a, d, pr = Preset(), C.create_string_buffer(64), C.create_string_buffer(160), C.create_string_buffer(160)
    ok = fw.fn('opra_record', C.c_int, C.c_char_p, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_uint)(
        line if isinstance(line, bytes) else jsonl([line]).rstrip(b'\n'), r(p), a, d, pr, 64)
    return ok, p, a.value.decode(), d.value.decode(), pr.value.decode()


def card(fw, records=RECORDS, index=True):
    files = {DB: jsonl(records)}
    if index:
        files[INDEX] = index_of(records)
    return Card(fw, files)


# A record.

def test_an_eq_record_is_a_preset_with_its_own_preamp(fw):
    ok, p, author, details, product = record(fw, RECORDS[1])
    assert ok == 1 and (author, details, product) == ('AutoEQ', 'Measured by X', 'acme::one')
    assert (p.count, p.bypass, p.preamp, p.automatic, p.balance) == (3, 0, -4, 2, 0)  # as an import's own preamp
    assert [(b.enabled, b.type, b.frequency, b.gain, b.q) for b in p.bands[:3]] == [
        (1, LSC, 105, -1.4, 0.7), (1, PK, 170, -1.9, 1.47), (1, HSC, 10000, 2.2, 0.7)]


@pytest.mark.parametrize('kind', ['low_pass', 'high_pass', 'band_pass', 'band_stop', 'notch'])
def test_a_filter_peq_has_no_type_for_refuses_the_preset(fw, kind):
    x = eq('a::b', 'AutoEQ', '', [*ONE, {'type': kind, 'frequency': 30, 'slope': 12}])
    assert record(fw, x)[0] == 0


def test_a_band_below_20_hz_or_over_q_10_is_kept_at_peq_s_limit(fw):
    ok, p, *_ = record(fw, RECORDS[7])
    assert ok == 1 and (p.bands[0].frequency, p.bands[0].q) == (20, 10)


def test_twenty_bands_fit_and_thirty_one_do_not(fw):
    assert record(fw, eq('a::b', 'AutoEQ', '', [band('peak_dip', 100 + k, 1, 1) for k in range(20)]))[1].count == 20
    assert record(fw, eq('a::b', 'AutoEQ', '', [band('peak_dip', 100 + k, 1, 1) for k in range(31)]))[0] == 0


def test_a_gain_outside_peq_s_range_refuses_the_preset(fw):
    assert record(fw, eq('a::b', 'AutoEQ', '', [band('peak_dip', 100, 25, 1)]))[0] == 0
    assert record(fw, eq('a::b', 'AutoEQ', '', ONE, preamp=-61))[0] == 0


def test_escapes_and_any_member_order_are_read(fw):
    line = (b'{"id":"x","data":{"product_id":"a::b","parameters":{"bands":[{"q":1,"gain_db":2,"frequency":300,'
            b'"type":"peak_dip"}],"gain_db":-1},"details":"Tab\\there \\"q\\" \\u00e9\\/","author":"A\\\\B",'
            b'"extra":[1,{"x":[true,null]}]},"type":"eq"}')
    ok, p, author, details, product = record(fw, line)
    assert ok == 1 and author == 'A\\B' and details == 'Tab here "q" é/' and p.bands[0].frequency == 300


@pytest.mark.parametrize('line', [
    b'{"type":"vendor","id":"acme","data":{"name":"Acme"}}',
    b'{"type":"eq","data":{"author":"A","details":"","parameters":{"gain_db":-1,"bands":[',  # cut short
    b'{"type":"eq","data":{"author":"A","product_id":"a::b","parameters":{"gain_db":"x","bands":[]}}}',
    b'{"type":"eq","data":{"author":"A","product_id":"a::b","parameters":{"gain_db":-1,"bands":[{"type":"peak_dip"}]}}}',
    b'{"type":"eq","data":{"author":"A\\u12","product_id":"a::b","parameters":{"gain_db":-1,"bands":[]}}}',
    b'not json'])
def test_anything_but_a_whole_eq_record_is_refused(fw, line):
    assert record(fw, line)[0] == 0


# The index.

def build(fw, db=DB, out=INDEX):
    return fw.fn('opra_index', C.c_int, C.c_char_p, C.c_char_p)(db.encode(), out.encode())


def test_the_index_names_each_product_and_the_spans_of_its_usable_presets(fw):
    c = card(fw, index=False)
    assert build(fw) == 3  # acme::two's low pass leaves it out
    assert c.files[INDEX] == index_of(RECORDS)


def test_a_record_longer_than_a_read_or_a_last_line_without_a_newline_is_read(fw):
    long = {'type': 'vendor', 'id': 'pad', 'data': {'name': 'x' * 40000}}
    records = [long, *RECORDS]
    c = Card(fw, {DB: jsonl(records).rstrip(b'\n')})
    assert build(fw) == 3
    s = spans(records)  # the last, Z/1's product, read without its newline
    assert c.files[INDEX] == (f'Acme One HD 600\t{s[2][0]} {s[2][1]}\t{s[3][0]} {s[3][1]}\n'
                              f'Zed Audio Z/1\t{s[8][0]} {s[8][1]}\n').encode()


def test_a_product_without_its_vendor_is_named_alone(fw):
    c = Card(fw, {DB: jsonl(RECORDS[1:4])})
    assert build(fw) == 2 and c.files[INDEX].startswith(b'One HD 600\t')


def test_no_database_or_no_room_for_the_index_builds_nothing(fw):
    Card(fw, {})
    assert build(fw) == -1
    c = card(fw, index=False)
    c.fail_writes = True
    assert build(fw) == -1 and INDEX not in c.files


# Search, and a preset taken.

def find(fw, query):
    return fw.fn('opra_find', C.c_int, C.c_char_p)(query.encode())


def hit(fw, i):
    s = fw.fn('opra_hit', C.c_char_p, C.c_int)(i)
    return s and s.decode()


def hits(fw, query):
    n = find(fw, query)
    return [hit(fw, i) for i in range(n)] if n >= 0 else n


@pytest.mark.parametrize('query, found', [
    ('acme', ['Acme One HD 600']), ('HD600', ['Acme One HD 600']), ('zed z1', ['Zed Audio Z/1']),
    ('a', ['Acme One HD 600', 'Zed Audio Z/1']), ('one zed', []), ('two', []), ('  ', [])])
def test_every_word_of_the_search_is_in_the_name_ignoring_case_spaces_and_marks(fw, query, found):
    card(fw)
    assert hits(fw, query) == found and hit(fw, len(found)) is None
    assert fw.fn('opra_query', C.c_char_p)().decode() == query


def test_no_index_answers_minus_one(fw):
    card(fw, index=False)
    assert find(fw, 'acme') == -1


def test_a_search_stops_at_its_limit(fw):
    Card(fw, {INDEX: b''.join(b'Acme %d\t0 1\n' % k for k in range(300))})
    assert find(fw, 'acme') == 128 and hit(fw, 127) == 'Acme 127'


def choices(fw, i):
    n = fw.fn('opra_open', C.c_int, C.c_int)(i)
    out = []
    for k in range(max(n, 0) + 1):
        details = C.c_char_p()
        author = fw.fn('opra_choice', C.c_char_p, C.c_int, C.c_void_p)(k, r(details))
        out.append(author and (author.decode(), details.value.decode()))
    assert out.pop() is None
    return n, out


def take(fw, i):
    p, name = Preset(), C.create_string_buffer(200)
    ok = fw.fn('opra_take', C.c_int, C.c_int, C.c_void_p, C.c_char_p, C.c_uint)(i, r(p), name, 200)
    return ok, p, name.value.decode()


def test_a_product_s_presets_by_author_and_one_taken_as_vendor_product_and_both(fw):
    card(fw)
    find(fw, 'acme')
    assert choices(fw, 0) == (2, [('AutoEQ', 'Measured by X'), ('oratory1990', 'Harman Target')])
    ok, p, name = take(fw, 1)
    assert ok and name == 'Acme One HD 600 (oratory1990, Harman Target)' and (p.preamp, p.count) == (-2.5, 1)
    assert take(fw, 2)[0] == 0 and fw.fn('opra_open', C.c_int, C.c_int)(1) == -1  # past the last


def test_a_name_s_marks_the_card_refuses_become_dashes(fw):
    card(fw)
    find(fw, 'zed')
    assert choices(fw, 0)[0] == 1 and take(fw, 0)[2] == 'Zed Audio Z-1 (AutoEQ, Measured by Z)'


def test_a_preset_that_no_longer_reads_is_left_out(fw):
    c = card(fw)
    c.files[DB] = c.files[DB].replace(b'"peak_dip","frequency":1000', b'"low_pass","frequency":1000')
    find(fw, 'acme')
    assert choices(fw, 0) == (1, [('AutoEQ', 'Measured by X')])


def test_no_database_opens_nothing(fw):
    c = card(fw)
    find(fw, 'acme')
    del c.files[DB]
    assert fw.fn('opra_open', C.c_int, C.c_int)(0) == -1


# The download.

class Net:
    """opra_update's pthread and tick, and what the page was told."""
    def __init__(self, fw, curl, wifi=1):
        self.fw, self.curl, self.threads, self.timers, self.told = fw, curl, [], [], []
        fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: self.threads.append(fn) or 0)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('get_wifisignal', lambda *_: wifi)
        self.changed = C.CFUNCTYPE(None, C.c_char_p)(lambda s: self.told.append(s.decode()))
        self.op = fw.var('opra__op', Op)

    def start(self):
        return self.fw.fn('opra_update', C.c_int, C.c_void_p)(self.changed)

    def work(self):
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads[-1])(0)

    def tick(self):
        fn, ms = self.timers[-1]
        assert ms == 500
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_the_download_goes_to_the_card_and_is_indexed_there(fw):
    c = Card(fw, {})
    net = Net(fw, Curl(fw, jsonl(RECORDS), chunk=100))
    assert net.start() == 1 and net.op.running and fw.fn('opra_busy', C.c_int)() == 1
    assert net.start() == 0  # one at a time
    net.work()
    assert net.curl.opts[10002] == URL and net.curl.opts[10018] == 'Q2Pod' and net.curl.opts[52] == 1
    assert (net.curl.opts[19], net.curl.opts[20]) == (1024, 30)  # a stalled transfer ends
    assert c.files[DB] == jsonl(RECORDS) and c.files[INDEX] == index_of(RECORDS)
    assert set(c.files) == {DB, INDEX}  # the parts renamed into place
    assert net.tick() == RET_REMOVE and net.told[-1] == 'OPRA ready: 3 presets'
    assert not net.op.running and fw.fn('opra_busy', C.c_int)() == 0 and fw.calls('fw_pthread_join')


def test_the_tick_tells_the_download_s_progress_once_each_change(fw):
    Card(fw, {})
    net = Net(fw, Curl(fw))
    net.start()
    net.op.bytes = 4200000
    assert net.tick() == RET_REPEAT and net.tick() == RET_REPEAT
    net.op.stage, net.op.presets = 1, 5000
    assert net.tick() == RET_REPEAT
    assert net.told == ['Downloading OPRA… 4.2 MB', 'Indexing OPRA… 5000 presets']


@pytest.mark.parametrize('code, error, told', [(404, 0, 'OPRA download failed'), (200, 7, 'OPRA download failed'),
                                               (200, 0, 'OPRA data not recognised')])
def test_a_failed_download_keeps_the_database_there_was(fw, code, error, told):
    c = card(fw)
    before = dict(c.files)
    net = Net(fw, Curl(fw, b'<html>' if code == 200 and not error else jsonl(RECORDS), code=code, error=error))
    net.start()
    net.work()
    assert c.files == before and net.tick() == RET_REMOVE and net.told[-1] == told


def test_without_wifi_it_says_so(fw):
    Card(fw, {})
    net = Net(fw, Curl(fw, code=0, error=6), wifi=0)
    net.start()
    net.work()
    net.tick()
    assert net.told[-1] == 'No Wi-Fi'


def test_a_card_that_takes_no_file_says_so(fw):
    c = Card(fw, {})
    c.fail_writes = True
    net = Net(fw, Curl(fw, jsonl(RECORDS)))
    net.start()
    net.work()
    net.tick()
    assert net.told[-1] == 'Cannot write to the card' and not net.curl.opts


def test_closing_cancels_the_download_and_frees_the_lists(fw):
    card(fw)
    net = Net(fw, Curl(fw, jsonl(RECORDS)))
    find(fw, 'acme')
    fw.fn('opra_open', C.c_int, C.c_int)(0)
    net.start()
    fw.fn('opra_close', None)()
    assert net.op.cancel == 1 and not net.op.running and fw.calls('fw_pthread_join') and fw.calls('timer_remove')
    assert hit(fw, 0) is None and fw.fn('opra_choice', C.c_char_p, C.c_int, C.c_void_p)(0, None) is None


def test_a_cancelled_download_leaves_no_part(fw):
    c = Card(fw, {})
    net = Net(fw, Curl(fw, jsonl(RECORDS), chunk=100))
    net.start()
    net.op.cancel = 1
    net.work()
    assert sorted(c.files) == []


# The EQ page's Presets → Search OPRA.

OPRA, OPRA_PICK = 12, 13  # peq_ui.c's screens after NAMING


def eq_page(fw, files, wifi=1):
    fw.mock('get_wifisignal', lambda *_: wifi)
    wm = C.create_string_buffer(0x200)  # no window over the page: need_link runs the press at once
    fw.mock('window_manager', lambda *_: C.addressof(wm))
    p = page(fw, files=files).advanced()
    p.click('Presets')
    return p


def search(p, text):
    edit = p.edit()
    p.text[edit] = text
    p.handler(edit, EVT_IM_ACTION)()
    p.handler(edit, EVT_VALUE_CHANGED)()  # Done sends both
    p.settle()


def opra_tick(p):
    fn = C.cast(p.fw.lib.opra__tick, C.c_void_p).value
    timer = [k for k, (f, _) in enumerate(p.timers, 1) if f == fn][-1]
    return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def with_db(records=RECORDS):
    return {DB: jsonl(records), INDEX: index_of(records)}


def test_presets_offers_search_opra_after_the_card_s_imports(fw):
    p = eq_page(fw, {})
    assert p.captions()[:3] == ['Stock presets', 'Import from SD /EQ', 'Search OPRA'] and p.chevron(p.rows()[2][1])


def test_with_no_database_it_offers_the_download_and_tells_its_progress_in_the_title(fw):
    p = eq_page(fw, {})
    threads = []
    fw.mock('fw_pthread_create', lambda t, a, fn, arg, *_: threads.append(fn) or 0)
    curl = Curl(fw, jsonl(RECORDS), chunk=100)
    p.click('Search OPRA')
    assert p.ui.screen == OPRA and p.captions() == ['Download OPRA database'] and p.title() == 'Search OPRA'
    assert not [w for w in p.alive if p.kind[w] == 'edit']
    p.click('Download OPRA database')
    assert len(threads) == 1 and p.title() == 'Downloading OPRA\u2026'
    p.click('Download OPRA database')  # dim while it runs: nothing
    assert len(threads) == 1
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(threads[0])(0)
    assert curl.opts[10002] == URL
    assert opra_tick(p) == RET_REMOVE
    p.settle()
    assert p.title() == 'OPRA ready: 3 presets' and p.captions() == ['Search', 'Update OPRA database']


def test_progress_shows_only_on_the_opra_screen(fw):
    p = eq_page(fw, {})
    fw.mock('fw_pthread_create', lambda *_: 0)
    p.click('Search OPRA')
    p.click('Download OPRA database')
    p.ret()
    fw.var('opra__op', Op).bytes = 2000000
    opra_tick(p)
    assert p.ui.screen == PRESETS and 'OPRA' not in p.title() and not p.pending('render')


def test_a_search_lists_the_products_and_a_pick_saves_its_preset_by_name_and_author(fw):
    p = eq_page(fw, with_db())
    p.click('Search OPRA')
    edit = p.edit()
    assert p.captions() == ['Search', 'Update OPRA database'] and p.title() == '' and p.focused[-1] == (edit, 1)
    search(p, 'hd600')
    assert p.captions() == ['Search', 'Acme One HD 600', 'Update OPRA database'] and p.text[p.edit()] == 'hd600'
    assert p.focused[-1] == (edit, 1)  # the results take the wheel: the new edit is not focused
    p.click('Acme One HD 600')
    assert p.ui.screen == OPRA_PICK and p.title() == 'Acme One HD 600'
    assert p.captions() == ['AutoEQ Measured by X', 'oratory1990 Harman Target']
    p.click('oratory1990')
    name = f'{SAVED}/Acme One HD 600 (oratory1990, Harman Target).peq'
    assert name in p.card.files and p.title() == 'Preset saved; active EQ unchanged'
    out = Preset()
    assert fw.fn('peq_load', C.c_int, C.c_char_p, C.c_void_p)(name.encode(), r(out)) and out.preamp == -2.5
    assert p.card.state().peq.preamp != -2.5  # saved, not applied
    p.click('oratory1990')
    assert p.captions() == ['Replace existing preset? Confirm', 'Cancel replacement']
    p.click('Replace existing preset? Confirm')
    assert p.ui.screen == OPRA_PICK and p.title() == 'Preset saved; active EQ unchanged'
    p.ret()
    assert p.ui.screen == OPRA and p.captions() == ['Search', 'Acme One HD 600', 'Update OPRA database']
    p.ret()
    assert p.ui.screen == PRESETS


def test_no_match_says_so_and_search_opens_the_keyboard_again(fw):
    p = eq_page(fw, with_db())
    p.click('Search OPRA')
    search(p, 'xyz')
    assert p.title() == 'No matches for xyz' and p.captions() == ['Search', 'Update OPRA database']
    assert not [w for w in p.alive if p.kind[w] == 'edit']
    p.click('Search')
    edit = p.edit()
    assert p.text[edit] == 'xyz' and p.focused[-1] == (edit, 1) and p.title() == ''


def test_return_from_the_keyboard_keeps_the_last_search(fw):
    p = eq_page(fw, with_db())
    p.click('Search OPRA')
    search(p, 'acme')
    search(p, '')  # search_key's cancel empties the edit
    assert p.captions() == ['Search', 'Acme One HD 600', 'Update OPRA database'] and p.text[p.edit()] == 'acme'


def test_a_database_gone_since_the_search_says_so(fw):
    p = eq_page(fw, with_db())
    p.click('Search OPRA')
    search(p, 'acme')
    del p.card.files[DB]
    p.click('Acme One HD 600')
    assert p.ui.screen == OPRA and p.title() == 'Cannot read the OPRA database'


def test_update_needs_wi_fi(fw):
    p = eq_page(fw, with_db(), wifi=0)
    fw.mock('fw_pthread_create', lambda *_: 0)
    p.click('Search OPRA')
    p.click('Update OPRA database')
    assert not fw.calls('fw_pthread_create') and fw.calls('navigator_to_with_context')


def test_closing_the_page_stops_the_download(fw):
    p = eq_page(fw, with_db())
    fw.mock('fw_pthread_create', lambda *_: 0)
    p.click('Search OPRA')
    p.click('Update OPRA database')
    assert fw.var('opra__op', Op).running
    p.handler(p.page, EVT_DESTROY)()
    assert not fw.var('opra__op', Op).running and fw.calls('fw_pthread_join')
