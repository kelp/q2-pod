/* A card app's Now Playing over stock's AirPlay page (docs/librespot.md, Card apps). The app is
 * the one addon_remote.c finds: it runs and its q2app holds remote=<op>. While stock's receiver
 * plays (airplayGetFlag 2), the app writes what plays to /tmp/q2-<id>.np, in the key=value lines
 * of Spotify's state file (spotify.c field, number), and the cover to /tmp/q2-<id>.np.jpg; each
 * written aside and renamed in, the cover first. "np" in the caps tells the app to write them. A
 * file whose mtime is NP_STALE_S old, or none, is no app. Each UI loop pass, paced to NP_POLL_MS,
 * addon_np_poll opens the page over airplay_page when that is the top window and the file is
 * fresh. The page is spot_np's layout (Classic's through stream_ipod): the app's name, the cover,
 * title, artist and album, the bar, the times and the play state. Without a fresh file it says
 * "Nothing playing". Return closes it alone: airplay_page shows under it, whose own Return asks to
 * exit AirPlay. Closing airplay_page ends AirPlay (its EVT_WINDOW_CLOSE handler, 0x52c3a4), so
 * this page never does. Once Returned from, it stays shut until the flag leaves 2 and comes back.
 * When AirPlay stops it closes.
 * The keys are as before: Play/Pause and the side buttons are the app's (addon_remote.c), the
 * wheel is the Q2's volume, as on Spotify's page. ponytail: no scrub, as the file has no seek op;
 * a seek word for remote= would add it. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define CARD "/mnt/mmc"
#define NP_POLL_MS 250
#define NP_STALE_S 15             /* the app writes the file every 5 s at least */
#define NP_ART "/tmp/q2appnp.jpg" /* the cover at the page's size */
#define NP_PAGE "appnp_page"

/* The file as last read: fresh, or 0 for no app. left: Return closed the page in this AirPlay
 * session. */
static struct {
    int fresh, playing, left;
    unsigned duration, position, cover;
    unsigned long long at;
    char id[16], state[12], title[256], artist[256], album[256], zone[64];
} nf __attribute__((section(".scratch")));

/* The page, while open. */
static struct {
    void *page, *info, *msg, *name, *art, *bar, *glyph, *title, *artist, *album, *elapsed, *remain,
        *note, *sub;
    unsigned polled, leave, shown, playing, bar_drawn;
    unsigned art_cover, art_tried; /* the cover shown; the one that failed, tried art_tries times */
    int ipod, art_tries;           /* ipod: Classic's layout (stream_ipod) */
    char app[64];                  /* its q2app's name= */
} ui __attribute__((section(".scratch")));

/* The app's file into nf; 1 when it is fresh. */
static int np_read(void) {
    const char *id = addon_remote_app();
    char path[48], raw[1536]; /* raw: behind a '\n', as field() reads */
    unsigned st[36];          /* o32 stat: st_mtime word 16 */
    nf.fresh = 0;
    if (!id) return 0;
    tk_snprintf(nf.id, sizeof nf.id, "%s", id);
    tk_snprintf(path, sizeof path, "/tmp/q2-%s.np", id);
    if (__xstat(3, path, st) || (long)time((void *)0) - (long)st[16] > NP_STALE_S) return 0;
    if (!state_read(path, raw, sizeof raw)) return 0;
    field(raw, "state", nf.state, sizeof nf.state);
    field(raw, "title", nf.title, sizeof nf.title);
    field(raw, "artist", nf.artist, sizeof nf.artist);
    field(raw, "album", nf.album, sizeof nf.album);
    field(raw, "zone", nf.zone, sizeof nf.zone);
    nf.duration = (unsigned)number(raw, "duration");
    nf.position = (unsigned)number(raw, "position");
    nf.at = number(raw, "at");
    nf.cover = (unsigned)number(raw, "cover");
    nf.playing = !strcmp(nf.state, "playing");
    return nf.fresh = 1;
}

/* The position now: the one read, moved on by the time since while playing, at most the length. */
static unsigned np_position(void) {
    unsigned long long now = now_ms();
    unsigned p = nf.position;
    if (nf.playing && now > nf.at) p += (unsigned)(now - nf.at);
    return nf.duration && p > nf.duration ? nf.duration : p;
}

static void times(unsigned ms) {
    char t[24];
    toolsTimeItoa(t, (int)(ms / 1000));
    widget_set_text_utf8(ui.elapsed, t);
    t[0] = '-'; /* Now Playing's remaining time: whole seconds less whole seconds */
    toolsTimeItoa(t + 1, (int)(nf.duration / 1000 - ms / 1000));
    widget_set_text_utf8(ui.remain, t);
}

static void set(void *w, const char *s) {
    if (text_differs(w, s)) widget_set_text_utf8(w, s), widget_set_prop_int(w, "xoffset", 0);
}

/* The page from nf: the song, or the message. Labels are written only when they change. */
static void np_refresh(void) {
    int song = nf.fresh && strcmp(nf.state, "none") && nf.state[0];
    char sub[96] = "";
    widget_set_visible(ui.info, song, 0);
    widget_set_visible(ui.msg, !song, 0);
    set(ui.name, ui.app[0] ? ui.app : nf.zone);
    if (!song) {
        if (ui.app[0]) tk_snprintf(sub, sizeof sub, "Choose music in %s", ui.app);
        set(ui.note, "Nothing playing");
        set(ui.sub, sub);
        return;
    }
    void *fields[] = { ui.title, ui.artist, ui.album };
    const char *values[] = { nf.title, nf.artist, nf.album };
    for (unsigned i = 0; i < 3; ++i)
        if (text_differs(fields[i], values[i])) {
            widget_set_text_utf8(fields[i], values[i]);
            widget_set_prop_int(fields[i], "xoffset", 0);
            if (ui.ipod && !i)
                theme_font(fields[i], ROLE_NP, FONT_ROW); /* the local page's title */
        }
    /* ponytail: the cover is sized on the UI thread, one decode a cover; a worker if it stutters.
     * One that fails is tried on the next 3 passes, then left till the cover number changes. */
    if (nf.cover != ui.art_tried) ui.art_tries = 0;
    if (nf.cover && nf.cover != ui.art_cover && ui.art_tries < 4) {
        char src[48];
        int px = I(ui.art, W_W); /* Classic's slanted art is wider */
        tk_snprintf(src, sizeof src, "/tmp/q2-%s.np.jpg", nf.id);
        if (thumb(src, NP_ART, px, px) && image_show(ui.art, "file://" NP_ART, 0)) {
            ui.art_cover = nf.cover;
        } else {
            ui.art_tried = nf.cover, ui.art_tries++, ui.art_cover = 0;
            image_base_set_image(ui.art, PLACEHOLDER);
        }
    } else if (!nf.cover && ui.art_cover) {
        ui.art_cover = 0;
        image_base_set_image(ui.art, PLACEHOLDER);
    }
    widget_set_visible(ui.bar, nf.duration != 0, 0); /* a stream of no length: no bar */
    widget_set_visible(ui.remain, nf.duration != 0, 0);
    if (ui.ipod)
        ipod_times(ui.elapsed, ui.remain, ui.bar, (int)(nf.duration / 1000)); /* a new length */
    unsigned ms = np_position();
    if (ms / 1000 + 1 != ui.shown) {
        ui.shown = ms / 1000 + 1;
        times(ms);
    }
    /* only when the drawn width (addon_np_paint's) moves */
    unsigned bw = (unsigned)I(ui.bar, W_W);
    unsigned bar = (unsigned)((ms >> 4) * bw / ((nf.duration >> 4) | 1));
    if (bar + 1 != ui.bar_drawn) {
        ui.bar_drawn = bar + 1;
        widget_invalidate_force(ui.bar, 0);
    }
    if (ui.playing != (unsigned)nf.playing + 1) {
        ui.playing = (unsigned)nf.playing + 1;
        widget_invalidate_force(ui.glyph, 0);
    }
}

static void box(void *canvas, int x, int y, int w, int h, unsigned color, unsigned radius) {
    int r[4] = { x, y, w, h };
    if (canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) {
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, x, y, w, h);
    }
}

/* addon_paint's, every widget after stock painted it: the bar, as Spotify's (spot_paint), and the
 * play state in the top row. */
void addon_np_paint(void *w, void *canvas) {
    if (!ui.page || !w || (w != ui.bar && w != ui.glyph) || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    unsigned ms = np_position();
    if (w == ui.bar && ui.ipod) { /* Classic's, as the local page's */
        int bw = I(w, W_W);
        ipod_bar(canvas, 0, 0, bw, (int)((ms >> 4) * (unsigned)bw / ((nf.duration >> 4) | 1)), 0);
    } else if (w == ui.bar) {
        int x = (int)((ms >> 4) * SPOT_BAR_W / ((nf.duration >> 4) | 1)); /* 32-bit to 57 h */
        /* Minimal's 4 px line, Modern's 8 px capsule: the view's height at most */
        int h = theme()->np_bar < SPOT_BAR_H ? theme()->np_bar : SPOT_BAR_H;
        box(canvas, 0, 0, SPOT_BAR_W, h, rgba(TRACK_COLOR, 255), h / 2);
        if (x) box(canvas, 0, 0, x < h ? h : x, h, rgba(accent_tone(2), 255), h / 2);
    } else { /* a triangle playing, two bars otherwise */
        canvas_set_fill_color(canvas, 0xffffffffu);
        if (nf.playing)
            for (int i = 0; i < 7; ++i) canvas_fill_rect(canvas, 2 * i, i, 2, 14 - 2 * i);
        else
            canvas_fill_rect(canvas, 0, 0, 4, 14), canvas_fill_rect(canvas, 8, 0, 4, 14);
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* Return's, on a timer: this page goes, airplay_page shows, and the poll leaves it shut till the
 * next AirPlay session. */
static int leave(const void *unused) {
    ui.leave = 0;
    nf.left = 1;
    navigator_back();
    return 0;
}

static int keyup(void *ctx, void *event) { return return_key(event, &ui.leave, leave); }

static int closed(void *ctx, void *event) {
    stop_timer(&ui.leave);
    memset(&ui, 0, sizeof ui);
    return 0;
}

/* The page over airplay_page, laid out as spot_np's. */
static void np_open(void) {
    char raw[512], path[48];
    if (!(ui.page = page_open(NP_PAGE, closed, keyup))) return;
    tk_snprintf(path, sizeof path, CARD "/.%s/q2app", nf.id);
    state_read(path, raw, sizeof raw);
    field(raw, "name", ui.app, sizeof ui.app);
    unsigned k = strlen(ui.app);
    if (k && ui.app[k - 1] == '\r') ui.app[k - 1] = 0; /* a Windows editor's line end */
    void *f = widget_factory(), *page = ui.page;
    int h = widget_get_prop_int(page, "h", client_h());
    ui.info = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    ui.msg = widget_factory_create_widget(f, "view", page, 0, 0, 375, h);
    /* the name: the label Classic's title bar reads (page_caption) */
    ui.name = label(ui.info, 16, 12, 200, 16, "s_scrlabel_white20l", 16, SPOT_GREY);
    widget_set_name(ui.name, PAGE_TITLE);
    ui.glyph = widget_factory_create_widget(f, "view", ui.info, 337, 13, 14, 14); /* NP's top row */
    ui.art = image_create(ui.info, SPOT_ART_X, SPOT_ART_Y, SPOT_ART_PX, SPOT_ART_PX);
    image_set_draw_type(ui.art, 4); /* as Coverflow's covers */
    image_base_set_image(ui.art, PLACEHOLDER);
    ui.title = label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 41, SPOT_TEXT_W, 28, "s_scrlabel_white20l",
                     NP_TITLE_PX, 0xffffffffu);
    ui.artist = label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 75, SPOT_TEXT_W, 22, "s_scrlabel_white20l",
                      NP_META_PX, 0xff000000u | NP_ARTIST_RGB);
    ui.album = label(ui.info, SPOT_TEXT_X, SPOT_ART_Y + 103, SPOT_TEXT_W, 22, "s_scrlabel_white20l",
                     NP_META_PX, 0xff000000u | NP_ALBUM_RGB);
    void *fields[] = { ui.title, ui.artist, ui.album };
    for (unsigned i = 0; i < 3; ++i) {
        hscroll_label_set_only_focus(fields[i], 0);
        hscroll_label_set_ellipses(fields[i], 0);
    }
    ui.bar = widget_factory_create_widget(f, "view", ui.info, SPOT_BAR_X, SPOT_BAR_Y, SPOT_BAR_W,
                                          SPOT_BAR_H);
    ui.elapsed =
        label(ui.info, SPOT_TIME_X, SPOT_TIMES_Y, 80, 16, "s_scrlabel_white20l", 14, SPOT_GREY);
    ui.remain = label(ui.info, 375 - SPOT_TIME_X - 80, SPOT_TIMES_Y, 80, 16, "s_scrlabel_white20r",
                      14, SPOT_GREY);
    /* Classic: the local page's places, its title bar naming the page and showing the play state */
    void *rows[4] = { ui.title, ui.artist, ui.album, 0 };
    ui.ipod = stream_ipod(ui.art, rows, ui.elapsed, ui.remain, ui.bar, 0);
    if (ui.ipod) widget_set_visible(ui.name, 0, 0), widget_set_visible(ui.glyph, 0, 0);
    ui.note =
        label(ui.msg, CF_EDGE, 100, 375 - 2 * CF_EDGE, 30, "s_scrlabel_white20c", 20, 0xffffffffu);
    ui.sub =
        label(ui.msg, CF_EDGE, 134, 375 - 2 * CF_EDGE, 24, "s_scrlabel_white20c", 16, SPOT_GREY);
    np_refresh();
}

/* addon_poll's, every UI loop pass, paced to NP_POLL_MS: AirPlay stopped closes the page and ends
 * the session's Return; open, it follows the file while the screen is on; shut, it opens over
 * airplay_page on top once the app's file is fresh, unless Return closed it in this session. */
void addon_np_poll(void) {
    unsigned now = time_now_ms();
    if (now - ui.polled < NP_POLL_MS) return;
    ui.polled = now;
    if (airplayGetFlag() != 2) {
        nf.left = 0;
        if (ui.page) window_close(ui.page);
        return;
    }
    if (ui.page) {
        if (g_backlight_status) np_read(), np_refresh();
        return;
    }
    void *top = window_manager_get_top_window(window_manager());
    if (!nf.left && top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "airplay_page") &&
        np_read())
        np_open();
}
