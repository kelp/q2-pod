# q2-pod TODO

Personal tracking for work on kelp/q2-pod. This file lives on the orphan
`todo` branch, so it never appears in PR branches cut from upstream/main.

## Features

- [x] PEQ: Import all presets from `/EQ` in one action (`patch/peq_ui.c`)
- [x] PEQ: headphone picker: Quick Settings' Playback hold opens Headphones,
      the saved presets (branch `headphone-picker`)
- [x] PEQ: OPRA search over Wi-Fi (branch `opra-search`, verified HTTPS)
  - Data: `http://opra.roonlabs.net/database_v1.jsonl` (12.6 MB, about
    12,600 EQ presets for 6,200 products). Download it to the card; it does
    not fit in RAM.
  - Index: after each download, one pass writes a compact index (vendor,
    model, byte offsets of its EQ lines; about 250 KB). Search the index in
    memory as the user types; on pick, read only those EQ lines at their
    offsets and convert to APO text for the existing importer.
  - Estimates (not measured): full-file scan about 1 s per search (SD High
    Speed, about 15-20 MB/s); index build 1-2 s once; index search < 10 ms.
  - Supported now: `peak_dip`, `low_shelf`, `high_shelf`. Skip presets with
    `low_pass`, `high_pass`, `band_pass` or `band_stop` (5 presets).
  - Largest preset has 20 bands, within `PEQ_BANDS` (30).
  - CC BY-SA 4.0: credit the preset author and link to
    https://github.com/opra-project/OPRA
- [x] Listening stats on the device, from `.scrobbler.log` (branch
      `listening-stats`)
- [x] "More like this" mix: Last.fm or ListenBrainz similar artists,
      matched against the local library (branch `more-like-this`)
- [x] Scrobbling: `SESSION_KEY=` for Last.fm. Done upstream
      (`tools/lastfm_key.py`, changelog).
- [x] Eject Card on the device (branch `eject-card`): stop playback and the card's background
      jobs, then unmount (the USB mode path already does the stops)
- [ ] Roon, started and controlled from the Q2 (user's requirement, as the
      FiiO M21 does): Roon Extension API for browse/transport plus a local
      zone (squeezelite or AirPlay), in one app. Design: phase 1 remote + stock AirPlay zone (no
      firmware change), phase 2 squeezelite + general `sound=1` card-app hook
      (patch/addon_sound.c), phase 3 BT/USB. Volume on the wheel (Roon zone
      Fixed); app controls other zones too. App repo: ~/code/q2-roon (local).
      Earlier note: Roon zone through squeezelite (enable Squeezebox Support in Roon).
      RAAT and Roon Bridge are not possible: closed protocol, no MIPS build.
      First test the existing AirPlay receiver as a Roon AirPlay zone.
  - [x] Phase 1 works (2026-11-15): q2-roon remote + stock AirPlay zone.
  - [x] Firmware: under AirPlay, forward Play/Pause, the side buttons and
        headset keys to a running card app whose `q2app` has
        `remote=<op>` (stock drops keys 171/222/223 while
        `airplayGetFlag()==2`). Branch `airplay-remote`. Verified on the
        device (2026-11-15): playpause, next and previous reach Roon.
  - [ ] Firmware: card-app Now Playing page (copy of `spot_np`, new file),
        fed by a state file the app writes; replaces stock's
        `airplay_page` (cover only, stock volume popup) while the app runs.
        Branch `airplay-np`; app side q2-roon `now-playing-file`.
    - [ ] Follow-up: leave the page while AirPlay keeps playing. Closing
          stock's `airplay_page` stops the stream (user, 2026-11-16), so
          Return now only shows stock's page, and a second Return stops
          the music. Find a way to go Home with the stream on.
  - [ ] Check on device: does a BT headset Play during AirPlay stop the
        stream and start the local queue (inferred from disassembly)?
  - [x] Squeezelite rate test (2026-11-15): Roon sends Squeezebox
        players up to 24/192 bit-perfect; a 24/384 file is downsampled
        to 24/192.
  - [ ] Phase 2 squeezelite zone (on hold).
  - Go memory measured (2026-11-15): RSS 11 MB, heap 3 MB, GC pause
    3 ms total in 10 min. No rewrite needed.

- [ ] Qobuz client. DEFERRED (2026-11-14, user's choice): revisit only with
      a partner key. Official API is partner-only (api@qobuz.com issues
      app_id/app_secret). Community clients reuse IDs scraped from the web
      player, which risks Qobuz's terms and may break. Qobuz Connect is
      closed (licensed SDK); unofficial receivers exist (qobine, qconnect).
      Model: the Deezer card app (browse, download to the card). Now: AirPlay
      from the Qobuz phone app (16-bit/44.1 kHz).

## Bugs from the audit (2026-11-14)

Line numbers are from commit facf5b0b; check them again on current main.

- [x] `patch/netupdate.c` `shelf_remove`: deletes paths from the card's
      `.q2/apps/<id>.files` with no `..` check. Fixed with `card_path()`
      on branch `shelf-remove-dotdot` (434d6f59, pushed). An ordinary
      bug, not a security issue: only card access can write the list,
      and card access already runs card apps as root (SECURITY.md).
- [ ] Open the upstream PR for `shelf-remove-dotdot`.
- [ ] `patch/peq_ui.c:545`: band ENABLE off then on resets an L-only or
      R-only band to Both.
- [ ] `patch/peq_player.c` `mp3_toc`: looks for Xing only at the
      CRC-shifted offset; `mp3_gapless.c` accepts both layouts.
- [ ] `patch/library_lists.c` `ls_worker`: a failed probe (card gone, no
      memory) saves 0 s, and the song is never probed again.
- [ ] `patch/scrobble.c` `scrobble_append`: short `fwrite` leaves a partial
      line; the next listen joins it and both are lost.
- [ ] `patch/radio.c` `fetch_run`: leaks the 256 KB buffer when
      `pthread_create` fails.
- [ ] Unverified: `navigation.c` `bt_volume_poll` rewrites config.ini
      several times a second while the headset volume changes. A risk only
      if stock `toolsWriteConfig` writes in place.

## Cleanups (follow-ups from feature branches)

- [ ] Branch coverage on new C at least upstream's (87.1% branches, 98.3%
      lines, CI gcovr 2026-11-15). Below it: opra.c 78.6% br / 97.6% ln,
      listening_stats.c 80.0% / 96.4%, eject.c 81.5%, similar.c 85.1%;
      peq_ui.c lines added by headphone-picker 70% br, opra-search 76% br.

- [ ] One CA bundle define in a header. `scrobble.c` (CA_FILE),
      `netupdate.c` (NU_CA), `radio.c` (RADIO_CA), `onlinematch.c` (OM_CA)
      and `opra.c` (OPRA_CA) each define the path to
      `/etc/scrobble-ca.pem`. NU_CA, RADIO_CA and OM_CA leave out
      `PEQ_ROOT`, so host tests see a different path. Do it after
      `opra-search` merges.
- [ ] Upstream PR order: whichever of `more-like-this` and `eject-card`
      lands second must call `similar_stop()` from `card_away()`
      (`fixups/0001-*.patch`).
- [ ] Open the upstream PR for `preset-name-ellipsis` (35e925a2, pushed
      to origin). Long names in Presets and Import from SD /EQ end in an
      ellipsis and scroll while selected. Verified on the Q2 in dev 13
      (V00Di, 2026-11-15). Merging after `opra-search` needs the
      `WIDE | NAME` resolution that rerere holds.
- [ ] guide.html install note: "insert or remove the card only while the
      Q2 is off" is partly out of date with Eject Card.
- [ ] Eject Card: a "Mount Card" row, if an ejected card that was not
      pulled should come back without a pull, standby or restart.

## Test failures on sound (Debian 13), all also on upstream/main

From a headless diagnosis on 2026-11-14; full report in sound's
`~/q2-diagnose-report.md`. CI never runs these (they need the stock ZIP).

- [x] `test/patch.py`: the harness leaves CP0 Status.FR clear, but the
      payload is `-mfp64`; clang 19 puts a double in `$f1` (uitofp in
      `coverflow_position_offset`), a reserved instruction under FR=0.
      Fix: set CU1|FR in `Machine.__init__`, as `test/hci_emu.py` does.
      Confirmed: 3203 scenarios pass with it. Branch `test-patch-fpu-fr`;
      upstream PR candidate.
- [x] `test/player_seam.py`: FFmpeg 7.1 writes mvhd timescale 1000; the
      script assumes 44100. Fix: `-movie_timescale 44100` on .m4a outputs.
      Confirmed. Branch `test-seam-timescale`.
- [ ] `test/build.py --build`: 60 px `scrsaver_playcover.png` corner alpha
      is 2/255 under ImageMagick 7 Q16 (non-HDRI); the check wants 0.
      Plausible fix: HDRI build, or relax the check to < 4/255.
- [ ] docs/building.md: list `flac` among the test tools.
