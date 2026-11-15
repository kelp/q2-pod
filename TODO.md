# q2-pod TODO

Personal tracking for work on kelp/q2-pod. This file lives on the orphan
`todo` branch, so it never appears in PR branches cut from upstream/main.

## Features

- [ ] PEQ: Import all presets from `/EQ` in one action (`patch/peq_ui.c`)
- [ ] PEQ: headphone picker in Quick Settings, so one output can switch
      between presets for different headphones
- [ ] PEQ: OPRA search over Wi-Fi
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
- [ ] Eject Card on the device: stop playback and the card's background
      jobs, then unmount (the USB mode path already does the stops)
- [ ] Roon zone through squeezelite (enable Squeezebox Support in Roon).
      RAAT and Roon Bridge are not possible: closed protocol, no MIPS build.
      First test the existing AirPlay receiver as a Roon AirPlay zone.

- [ ] Qobuz client. Official API is partner-only (api@qobuz.com issues
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
