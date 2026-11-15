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
- [ ] Listening stats on the device, from `.scrobbler.log`
- [ ] "More like this" mix: Last.fm or ListenBrainz similar artists,
      matched against the local library
- [ ] Scrobbling: `SESSION_KEY=` for Last.fm, so `.scrobble.ini` does not
      need the password. Session keys do not expire. Add a small script that
      gets the key on a computer.
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

- [ ] Qobuz client. DEFERRED (2026-11-14, user's choice): revisit only with
      a partner key. Official API is partner-only (api@qobuz.com issues
      app_id/app_secret). Community clients reuse IDs scraped from the web
      player, which risks Qobuz's terms and may break. Qobuz Connect is
      closed (licensed SDK); unofficial receivers exist (qobine, qconnect).
      Model: the Deezer card app (browse, download to the card). Now: AirPlay
      from the Qobuz phone app (16-bit/44.1 kHz).

## Bugs from the audit (2026-11-14)

Line numbers are from commit facf5b0b; check them again on current main.

- [ ] `patch/netupdate.c` `shelf_remove`: deletes paths from the card's
      `.q2/apps/<id>.files` with no `..` check. Use `dotdot()` from
      `addon.c`.
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

- [ ] One CA bundle define in a header. `scrobble.c` (CA_FILE),
      `netupdate.c` (NU_CA), `radio.c` (RADIO_CA), `onlinematch.c` (OM_CA)
      and `opra.c` (OPRA_CA) each define the path to
      `/etc/scrobble-ca.pem`. NU_CA, RADIO_CA and OM_CA leave out
      `PEQ_ROOT`, so host tests see a different path. Do it after
      `opra-search` merges.
- [ ] guide.html install note: "insert or remove the card only while the
      Q2 is off" is partly out of date with Eject Card.
- [ ] Eject Card: a "Mount Card" row, if an ejected card that was not
      pulled should come back without a pull, standby or restart.

## Test failures on sound (Debian 13), all also on upstream/main

From a headless diagnosis on 2026-11-14; full report in sound's
`~/q2-diagnose-report.md`. CI never runs these (they need the stock ZIP).

- [ ] `test/patch.py`: the harness leaves CP0 Status.FR clear, but the
      payload is `-mfp64`; clang 19 puts a double in `$f1` (uitofp in
      `coverflow_position_offset`), a reserved instruction under FR=0.
      Fix: set CU1|FR in `Machine.__init__`, as `test/hci_emu.py` does.
      Confirmed: 3203 scenarios pass with it. Upstream PR candidate.
- [ ] `test/player_seam.py`: FFmpeg 7.1 writes mvhd timescale 1000; the
      script assumes 44100. Fix: `-movie_timescale 44100` on .m4a outputs.
      Confirmed.
- [ ] `test/build.py --build`: 60 px `scrsaver_playcover.png` corner alpha
      is 2/255 under ImageMagick 7 Q16 (non-HDRI); the check wants 0.
      Plausible fix: HDRI build, or relax the check to < 4/255.
- [ ] docs/building.md: list `flac` among the test tools.
