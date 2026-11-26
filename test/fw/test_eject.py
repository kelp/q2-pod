"""eject.c: System's Eject Card. A press stops playback and every job that holds the card, syncs, unmounts
/mnt/mmc and tells stock the card is gone, as its mdev script does on a pull; a card something keeps busy
stays mounted, and the toast says what holds it."""
import ctypes as C

from conftest import O
from test_addon import World, ap
from test_nav_display_library import Config, Settings, Ui, button_of, titles
from test_playlist_art import Pl

MOUNTED = b'/dev/mmcblk4p1 /mnt/mmc exfat rw,relatime 0 0\n'
EBUSY, EINVAL = 16, 22
ME = 500  # demo's pid


class Card:
    """The card mounted, stock's card flag (/tmp/mmc_add) set, a running card app and playlist art's worker, each
    effect in world.log in order; umount answers from umounts, the last again."""
    def __init__(self, fw, mounts=MOUNTED, umounts=(0,), proc=None, links=None):
        files = {b'/proc/mounts': mounts, b'/tmp/mmc_add': b'', b'/tmp/q2-a': b'1'}
        files.update((k, v) for k, v in (proc or {}).items() if isinstance(v, bytes))
        dirs = {k: v for k, v in (proc or {}).items() if isinstance(v, list)}
        self.world = World(fw, files, dirs)
        self.log, self.umounts, self.links = self.world.log, list(umounts), links or {}
        a = ap(fw)
        a.n, a.a[0].pid, a.a[0].launched = 1, b'/tmp/q2-a', 1
        fw.mock('sleep_ms', self.sleep)
        self.pl = fw.var('playlist_art__pl', Pl)
        self.pl.running, self.pl.thread = 1, 0x77
        fw.mock('fw_pthread_join', lambda t, *_: self.log.append(('join', t)) and 0)
        fw.mock('player_stop', lambda *_: self.log.append(('player_stop',)) and 0)
        fw.mock('eject_sync', lambda *_: self.log.append(('sync',)))
        fw.mock('eject_umount2', self.umount)
        fw.mock('fw_readlink', self.readlink)
        fw.mock('fw_getpid', lambda *_: ME)
        fw.mock('stock_cardmount_trampoline', self.cardmount)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.log.append(('toast', C.string_at(info + 8).decode())))
        self.flag = C.c_ubyte.from_address(O['CARD_TOAST'])

    def sleep(self, ms, *_):
        self.world.files.pop(b'/tmp/q2-a', None)  # the app's loop ends on its TERM
        self.log.append(('sleep', ms))

    def umount(self, path, flags, *_):
        self.log.append(('umount', C.string_at(path), flags))
        return -self.umounts.pop(0) if len(self.umounts) > 1 else -self.umounts[0]

    def readlink(self, path, out, size, *_):
        target = self.links.get(C.string_at(path))
        if target is None:
            return -1
        C.memmove(out, target, min(len(target), size))
        return min(len(target), size)

    def cardmount(self, *_):
        """Stock's check (check_cardmount_status): the flag gone, it asks for its TF Card Removed toast."""
        self.log.append(('cardmount', b'/tmp/mmc_add' in self.world.files))
        self.flag.value = 1
        return 0

    def effects(self):
        """What the press did, but the waits."""
        return [e for e in self.log if e[0] != 'sleep']

    def toasts(self):
        return [e[1] for e in self.log if e[0] == 'toast']


def eject(fw):
    """System settings, then its Eject Card row pressed."""
    ui = Ui(fw)
    Config(fw)
    page = Settings(fw, ui)
    return ui.click(button_of(page.view, 'Eject Card'))


def test_system_settings_has_eject_card_after_update(fw):
    ui = Ui(fw)
    Config(fw)
    page = Settings(fw, ui)
    found = titles(page.view)
    assert found[found.index('system_update') + 1:found.index('system_reset')] == ['Eject Card']
    image = [c for c in button_of(page.view, 'Eject Card').children if c.kind == 'image']
    assert image[0].props['image'] == b'system_eject'  # its own icon, not System Update's


def test_eject_stops_every_card_job_then_syncs_unmounts_and_says_safe(fw):
    card = Card(fw)
    eject(fw)
    spot_kill = [e for e in card.log if e[0] == 'system' and b'killall -9 librespot' in e[1]]
    app_stop = [e for e in card.log if e[0] == 'system' and b'/tmp/q2-a' in e[1]]
    assert spot_kill and app_stop
    assert card.effects() == [('player_stop',), *spot_kill, *app_stop, ('join', 0x77), ('sync',),
                              ('umount', b'/mnt/mmc', 0), ('unlink', b'/tmp/mmc_add'), ('cardmount', False),
                              ('toast', 'Safe to remove the card')]
    assert card.flag.value == 0  # stock's TF Card Removed toast dropped: the card is still in the slot
    assert ap(fw).a[0].relaunch == 1  # the app starts again once the card is back (addon_poll)


def test_a_card_held_a_moment_unmounts_on_a_later_try(fw):
    card = Card(fw, umounts=(EBUSY, EBUSY, 0))
    eject(fw)
    assert [e for e in card.log if e[0] == 'umount'] == [('umount', b'/mnt/mmc', 0)] * 3
    assert card.toasts() == ['Safe to remove the card'] and ('unlink', b'/tmp/mmc_add') in card.log


def test_a_card_another_program_keeps_busy_stays_mounted_and_says_which(fw):
    proc = {b'/proc': [b'self', b'1', b'321', b'net'], b'/proc/1/fd': [b'0'], b'/proc/321/fd': [b'0', b'4'],
            b'/proc/321/comm': b'ffmpeg\n'}
    links = {b'/proc/1/fd/0': b'/dev/console', b'/proc/321/cwd': b'/', b'/proc/321/fd/4': b'/mnt/mmc/Music/a.flac'}
    card = Card(fw, umounts=(EBUSY,), proc=proc, links=links)
    eject(fw)
    tries = [e for e in card.log if e[0] == 'umount']
    waits = card.log[card.log.index(tries[0]):]
    assert len(tries) == 21 and [e for e in waits if e[0] == 'sleep'] == [('sleep', 100)] * 20  # 2 s of tries
    assert card.toasts() == ['Card is busy: ffmpeg']
    assert ('unlink', b'/tmp/mmc_add') not in card.log and not [e for e in card.log if e[0] == 'cardmount']


def test_a_file_demo_itself_holds_is_named(fw):
    proc = {b'/proc': [b'%d' % ME], b'/proc/%d/fd' % ME: [b'7']}
    links = {b'/proc/%d/fd/7' % ME: b'/mnt/mmc/.scrobbler.log'}
    card = Card(fw, umounts=(EBUSY,), proc=proc, links=links)
    eject(fw)
    assert card.toasts() == ['Card is busy: .scrobbler.log']


def test_a_busy_card_with_no_holder_found_says_busy(fw):
    card = Card(fw, umounts=(EBUSY,), proc={b'/proc': []})
    eject(fw)
    assert card.toasts() == ['Card is busy']


def test_another_unmount_failure_says_it_could_not(fw):
    card = Card(fw, umounts=(EINVAL,))
    eject(fw)
    assert len([e for e in card.log if e[0] == 'umount']) == 1
    assert card.toasts() == ['Could not eject the card'] and ('unlink', b'/tmp/mmc_add') not in card.log


def test_no_card_mounted_stops_nothing(fw):
    card = Card(fw, mounts=b'ubi0:data /mnt/data ubifs rw 0 0\n')
    eject(fw)
    assert card.effects() == [('toast', 'No card to eject')]


def busy(fw, links, proc=None):
    """A press on a card that stays busy, pid 321's links as given: the toasts."""
    card = Card(fw, umounts=(EBUSY,), proc={b'/proc': [b'321'], b'/proc/321/comm': b'sh\n', **(proc or {})},
                links=links)
    eject(fw)
    return card.toasts()


def test_a_program_working_in_the_card_s_top_folder_is_named(fw):
    assert busy(fw, {b'/proc/321/cwd': b'/mnt/mmc'}) == ['Card is busy: sh']


def test_a_program_run_from_the_card_is_named(fw):
    assert busy(fw, {b'/proc/321/cwd': b'/', b'/proc/321/exe': b'/mnt/mmc/apps/x/run'}) == ['Card is busy: sh']


def test_a_file_whose_path_only_starts_like_the_card_s_is_not_on_it(fw):
    links = {b'/proc/321/cwd': b'/mnt/mmcx', b'/proc/321/fd/3': b'/mnt/mmc2/a.flac'}
    assert busy(fw, links, {b'/proc/321/fd': [b'3']}) == ['Card is busy']


def test_a_program_gone_before_its_files_are_read_is_passed_over(fw):
    links = {b'/proc/322/fd/3': b'/mnt/mmc/a.flac'}
    proc = {b'/proc': [b'321', b'322'], b'/proc/322/fd': [b'3'], b'/proc/322/comm': b'cp\n'}
    assert busy(fw, links, proc) == ['Card is busy: cp']  # 321 has no fd folder


def test_a_holder_whose_name_cannot_be_read_is_another_program(fw):
    card = Card(fw, umounts=(EBUSY,), proc={b'/proc': [b'321'], b'/proc/321/fd': [b'3']},
                links={b'/proc/321/fd/3': b'/mnt/mmc/a.flac'})
    eject(fw)
    assert card.toasts() == ['Card is busy: another program']


def test_a_holder_s_empty_name_is_another_program(fw):
    links = {b'/proc/321/fd/3': b'/mnt/mmc/a.flac'}
    assert busy(fw, links, {b'/proc/321/fd': [b'3'], b'/proc/321/comm': b'\n'}) == ['Card is busy: another program']


def test_a_holder_s_name_without_a_newline_is_whole(fw):
    links = {b'/proc/321/fd/3': b'/mnt/mmc/a.flac'}
    assert busy(fw, links, {b'/proc/321/fd': [b'3'], b'/proc/321/comm': b'rsync'}) == ['Card is busy: rsync']


def test_a_busy_card_with_proc_unreadable_says_busy(fw):
    card = Card(fw, umounts=(EBUSY,))
    eject(fw)
    assert card.toasts() == ['Card is busy']
