# Upstream issues and PRs, held for review

Drafts for DiamondBond/q2-pod. None is filed. Travis reviews each one
before it goes upstream. This file lives on the `todo` branch, so it
never appears in a PR branch.

## 1. The payload's text is close to the .scratch address

Status: draft, not filed. The fix is on the fork branch `payload-room`
(2836bb3b). Not yet tested on the device.

Problem: `patch/link.ld` puts the `.scratch` state at the fixed address
`0xbd0000`, and the payload's `.text` and `.rodata` start at `0xb00000`.
They must end below `0xbd0000`, so the payload text is limited to
832 KB. Measured on sound, 2026-11-21:

- upstream/main 6ceb519a: the payload ends at `0xbcd198`. 11,880 bytes
  are free.
- The fork's 16 branches together add about 33 KB. The link fails:
  `.rodata` ends at `0xbd5297` and overlaps `.scratch`.
- The build already uses `-Oz`.

Memory map, measured from the patched `demo` (`llvm-readelf -l`):

| Segment | Start | End |
|---|---|---|
| stock RX | `0x400000` | `0x926b70` |
| stock RW | `0x936b70` | `0xa56e00` |
| payload RWE (last segment) | `0xb00000` | `0xbf79c0` |

Plausible, not measured: the heap starts after the last segment, so
nothing fixed is above the payload's segment. Moving `.scratch` up only
moves the heap's start.

Fix, 2 lines: `SCRATCH = 0xbe0000` in `tools/build.py` and
`.scratch 0xbe0000` in `patch/link.ld`, plus the address in
`docs/internals.md`. The payload text then has 896 KB. `build.py`'s
own layout checks follow `SCRATCH`.

## 2. Two test/fw tests read an unset clock

Status: draft, not filed. The fix is in `test-fw-arm64` (c00be9be).

`test_tidal.py`'s `Wake` and `test_addon.py`'s
`test_an_app_answers_when_its_socket_takes_a_connection` do not mock
`fw_clock_gettime`. Its stub writes nothing, so `now_ms()` reads
uninitialized stack, and `spot_exchange`'s `now_ms() < end` loop depends
on what was on the stack. On x86-64 the loop exits. On arm64 two tidal
tests spin forever, and the addon test fails when the code layout
changes. The fix mocks the clock at 0 in both tests.

## 3. spot_exchange is named for Spotify but is general

Status: draft, not filed. No code yet.

`spotify.c`'s `spot_exchange` is the general Q2SPOT/1 socket exchange.
`addon.c` uses it (`addon_answers`, the app worker), and so does the
fork's `addon_remote.c`. A name without "spot", and possibly its own
file, would say what it does. Keep the `Q2SPOT/1` wire name: apps
already speak it.

## 4. test/build.py fails its scrsaver_playcover.png corner check

Status: draft, not filed. Measured on upstream/main 913069a3
(2026-11-17) on sound (Debian 13, ImageMagick 7 Q16, non-HDRI).

`validate_assets` wants alpha 0 at the 60 px cover's corner and gets
2/255. Plausible fixes: an HDRI ImageMagick, or a check of < 4/255.

## 5. Feature and fix PRs

Status: each one is draft, not filed. Each branch is on the fork
(kelp/q2-pod) from upstream/main. Order and dependencies are in
`branches.txt`.

- `payload-room`: entry 1. The fork's branches need it together.
- `peq-import-all`: Import all imports every /EQ preset at once.
- `headphone-picker`: Playback's hold in Quick Settings picks a
  headphone.
- `eq-import-skip-appledouble`: Import from SD /EQ skips macOS `._`
  files.
- `test-patch-fpu-fr`: test/patch.py runs the FPU in FR=1.
- `test-seam-timescale`: test/player_seam.py pins the M4A timescale.
- `more-like-this`: More like this plays a mix of similar artists.
- `listening-stats`: Listening Stats counts plays from the scrobble
  logs.
- `opra-search`: Search OPRA finds a headphone's presets.
- `eject-card`: Eject Card makes the card safe to remove.
- `airplay-remote`: a card app can take the keys while AirPlay plays.
- `preset-name-ellipsis`: long preset names end in an ellipsis.
- `shelf-remove-dotdot`: App Store remove deletes nothing off the card.
- `peq-band-enable-keeps-channel`: a band turned off and on keeps its
  channel.
- `mp3-toc-xing-offset`: mp3_toc finds Xing in both CRC layouts.
- `test-fw-arm64`: test/fw runs on arm64 Linux. Includes entry 2.
- `airplay-np`: a card app's Now Playing page. Needs `airplay-remote`.
