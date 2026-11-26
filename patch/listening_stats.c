/* Listening Stats (docs/internals.md#listening-stats): the Library's page of what was played this
 * week, month and year and in all, from the card's scrobble logs: the unsent .scrobbler.log and
 * .scrobbler.log.sent, where an upload moves what it sent (scrobble.c retire). A worker streams
 * both files and counts each listen's artist, album and song in one bounded table; the UI thread
 * shows the counts once it ends. */
#include "navigation.h"

#define STATS_LOG "/mnt/mmc/.scrobbler.log"
#define STATS_TOP 25
#define STATS_BUF 4096 /* a line longer than this (scrobble.c's LINE_BYTES is 2560) is junk */
/* The table's bounds, about 1.4 MB while the page is open: STATS_KEYS distinct artists, albums
 * and songs together, their names in STATS_POOL bytes. A listen whose artist, album or song is
 * new once either is full still counts in the time played and the play counts, and for the names
 * already in the table, but the new name is not ranked. */
#define STATS_SLOTS 16384 /* a power of two, over STATS_KEYS for short probes */
#define STATS_KEYS 12288
#define STATS_POOL (1u << 20)
enum { WEEK, MONTH, YEAR, ALL, PERIODS };
enum { ARTISTS, ALBUMS, SONGS, KINDS };

typedef struct {
    unsigned hash, name, sub, n[PERIODS]; /* name, sub: offsets in the pool; sub the artist */
    unsigned kind;
} stat_t;
typedef struct {
    unsigned from[PERIODS]; /* each period's first second, set before the worker starts */
    unsigned *slot;         /* a key's index + 1, 0 empty */
    stat_t *key;
    char *pool, *buf;
    unsigned keys, used;
    unsigned plays[PERIODS], secs[PERIODS], count[PERIODS][KINDS];
    unsigned top[PERIODS][KINDS][STATS_TOP], tops[PERIODS][KINDS];
    volatile int cancel, done;
} stats_t;

static struct {
    void *page, *period, *list; /* the stats page, a period's, a top list's */
    stats_t *s;
    unsigned long thread;
    unsigned timer;
    int threaded, shown; /* shown: the period opened */
} lst __attribute__((section(".scratch")));

static const char *const period_name[PERIODS] = { N_("This Week"), N_("This Month"),
                                                  N_("This Year"), N_("All Time") };
static const char *const kind_name[KINDS] = { N_("Top Artists"), N_("Top Albums"),
                                              N_("Top Songs") };
static const char *const kind_unit[KINDS][2] = { { N_("artist"), N_("artists") },
                                                 { N_("album"), N_("albums") },
                                                 { N_("song"), N_("songs") } };

static unsigned fnv_str(unsigned h, const char *s) {
    for (; *s; s++) h = (h ^ (unsigned char)*s) * 16777619u;
    return (h ^ '\t') * 16777619u;
}

static unsigned pool_add(stats_t *s, const char *str) {
    unsigned n = (unsigned)strlen(str) + 1, at = s->used;
    if (n == 1) return 0; /* the pool's first byte: "" */
    memcpy(s->pool + at, str, n);
    s->used += n;
    return at;
}

/* The key for kind's name (with sub, its artist), added if new; 0 when the table is full. */
static stat_t *stat_find(stats_t *s, unsigned kind, const char *name, const char *sub) {
    unsigned h = fnv_str(fnv_str(2166136261u ^ kind, name), sub), i = h & (STATS_SLOTS - 1);
    for (; s->slot[i]; i = (i + 1) & (STATS_SLOTS - 1)) {
        stat_t *k = s->key + s->slot[i] - 1;
        if (k->hash == h && k->kind == kind && !strcmp(s->pool + k->name, name) &&
            !strcmp(s->pool + k->sub, sub))
            return k;
    }
    if (s->keys == STATS_KEYS || s->used + strlen(name) + strlen(sub) + 2 > STATS_POOL) return 0;
    stat_t *k = s->key + s->keys;
    k->hash = h, k->kind = kind;
    k->name = pool_add(s, name);
    k->sub = pool_add(s, sub);
    s->slot[i] = ++s->keys;
    return k;
}

/* A time of 1 to 10 digits, not all zero, that fits 32 bits (scrobble.c's epoch); 0 for any
 * other. */
static unsigned stat_time(const char *t) {
    unsigned long long v = 0;
    int n = 0;
    for (; *t; t++, n++) {
        if (*t < '0' || *t > '9' || n == 10) return 0;
        v = v * 10 + (unsigned)(*t - '0');
    }
    return v <= 0xffffffffu ? (unsigned)v : 0;
}

/* One Rockbox line (artist, album, title, track, length, rating, time, MusicBrainz ID), counted
 * when it is a listen (L, not a skip's S) with an artist, a title and a time, as an upload takes
 * them. The length is the song's: the time played adds it whole. */
static void stat_line(stats_t *s, char *line) {
    char *f[8] = { line };
    int n = 1;
    if (*line == '#') return;
    for (char *c = line; *c && n < 8; c++)
        if (*c == '\t') *c = 0, f[n++] = c + 1;
    unsigned t = n >= 7 ? stat_time(f[6]) : 0;
    if (!t || !*f[0] || !*f[2] || strcmp(f[5], "L")) return;
    int len = atoi(f[4]);
    stat_t *k[KINDS] = { stat_find(s, ARTISTS, f[0], ""),
                         *f[1] ? stat_find(s, ALBUMS, f[1], f[0]) : 0,
                         stat_find(s, SONGS, f[2], f[0]) };
    for (int p = 0; p < PERIODS; p++) {
        if (t < s->from[p]) continue;
        s->plays[p]++;
        s->secs[p] += len > 0 ? (unsigned)len : 0;
        for (int j = 0; j < KINDS; j++)
            if (k[j]) k[j]->n[p]++;
    }
}

/* path's whole lines, streamed; one cut short at the end (a listen being written) waits. */
static void stat_file(stats_t *s, const char *path) {
    void *f = fopen(path, "rb");
    unsigned have = 0, k;
    int junk = 0;
    while (f && !s->cancel && (k = fread(s->buf + have, 1, STATS_BUF - 1 - have, f)) > 0) {
        unsigned at = 0;
        have += k;
        for (unsigned j; at < have; at = j + 1) {
            for (j = at; j < have && s->buf[j] != '\n';) j++;
            if (j == have) break;
            s->buf[j] = 0;
            if (!junk) stat_line(s, s->buf + at);
            junk = 0;
        }
        memmove(s->buf, s->buf + at, have - at);
        have -= at;
        if (have == STATS_BUF - 1) have = 0, junk = 1; /* the rest of this line goes too */
    }
    if (f) fclose(f);
}

/* Key a before key b: more plays in period p, then by name and artist. */
static int stat_before(const stats_t *s, unsigned p, unsigned a, unsigned b) {
    const stat_t *x = s->key + a, *y = s->key + b;
    if (x->n[p] != y->n[p]) return x->n[p] > y->n[p];
    int c = strcmp(s->pool + x->name, s->pool + y->name);
    return c ? c < 0 : strcmp(s->pool + x->sub, s->pool + y->sub) < 0;
}

/* Each period's STATS_TOP of each kind, in order, and how many it has. */
static void stat_rank(stats_t *s) {
    for (unsigned i = 0; i < s->keys; i++) {
        unsigned kind = s->key[i].kind;
        for (unsigned p = 0; p < PERIODS; p++) {
            if (!s->key[i].n[p]) continue;
            s->count[p][kind]++;
            unsigned *top = s->top[p][kind], n = s->tops[p][kind];
            if (n == STATS_TOP && !stat_before(s, p, i, top[n - 1])) continue;
            unsigned at = n < STATS_TOP ? n++ : n - 1;
            for (; at > 0 && stat_before(s, p, i, top[at - 1]); at--) top[at] = top[at - 1];
            top[at] = i;
            s->tops[p][kind] = n;
        }
    }
}

/* ponytail: an upload's rewrite during the read may count its batch twice or not at all (read
 * .sent, then the log, without scrobble.c's lock); the next open counts again. */
static void *stat_worker(void *arg) {
    stats_t *s = arg;
    stat_file(s, STATS_LOG ".sent");
    stat_file(s, STATS_LOG);
    if (!s->cancel) stat_rank(s);
    __atomic_store_n(&s->done, 1, __ATOMIC_RELEASE);
    return 0;
}

static void stats_free(stats_t *s) {
    if (!s) return;
    free(s->slot), free(s->key), free(s->pool), free(s->buf);
    free(s);
}

/* Local midnight on t's day: the day's start by localtime (clock_back's for past 2038). */
static unsigned day_start(unsigned t) {
    int tm[16];
    long b = clock_back(t);
    const int *x = localtime_r(&b, tm);
    return x ? t - (unsigned)(x[2] * 3600 + x[1] * 60 + x[0]) : t;
}

/* The periods' starts in local time: Monday's midnight, the 1st's, January 1st's. Each from the
 * day's noon, so a daylight saving change between lands on the right day. With the clock never
 * set, only All Time counts. */
static void stat_bounds(unsigned *from) {
    long long wall = wall_time();
    int tm[16];
    long b = clock_back(wall);
    const int *x = wall >= CLOCK_SET ? localtime_r(&b, tm) : (void *)0;
    for (int p = 0; p < ALL; p++) from[p] = 0xffffffffu;
    from[ALL] = 0;
    if (!x) return;
    unsigned noon = day_start((unsigned)wall) + 12 * 3600, back[ALL] = {
        (unsigned)(x[6] + 6) % 7, (unsigned)x[3] - 1, (unsigned)x[7] /* wday, mday, yday */
    };
    for (int p = 0; p < ALL; p++) from[p] = day_start(noon - back[p] * 86400u);
}

static void plays_text(char *out, unsigned size, unsigned n) {
    tk_snprintf(out, size, n == 1 ? tr("%u play") : tr("%u plays"), n);
}

static int top_closed(void *ctx, event_t *event) {
    lst.list = 0;
    return 0;
}

/* A kind's top list: a row a name and its plays, an album's and a song's with the artist. */
static int top_open(void *ctx, event_t *event) {
    unsigned kind = (unsigned)(long)ctx - 1, p = (unsigned)lst.shown;
    stats_t *s = lst.s;
    if (kind >= KINDS || !s || lst.list ||
        !(lst.list = page_open("stats_top_page", top_closed, page_keyup)))
        return 0;
    unsigned n = s->tops[p][kind];
    void *view =
        page_list_or(lst.list, lst.list, tr(kind_name[kind]), tr("No listens"), (int)n, 64);
    char plays[32], detail[300];
    for (unsigned i = 0; i < n; i++) {
        const stat_t *k = s->key + s->top[p][kind][i];
        plays_text(plays, sizeof plays, k->n[p]);
        if (kind == ARTISTS)
            tk_snprintf(detail, sizeof detail, "%s", plays);
        else
            tk_snprintf(detail, sizeof detail, "%s · %s", s->pool + k->sub, plays);
        page_row_detail(view, (int)i, s->pool + k->name, detail, 0);
    }
    return 0;
}

static int period_closed(void *ctx, event_t *event) {
    lst.period = 0;
    return 0;
}

/* A period: its time played and plays, then a row for each top list with how many it ranks. */
static int period_open(void *ctx, event_t *event) {
    unsigned p = (unsigned)(long)ctx;
    stats_t *s = lst.s;
    if (p >= PERIODS || !s || lst.period ||
        !(lst.period = page_open("stats_period_page", period_closed, page_keyup)))
        return 0;
    lst.shown = (int)p;
    void *view = page_list(lst.period, lst.period, 0, tr(period_name[p]), 1 + KINDS, 64);
    char plays[32], detail[64];
    unsigned min = s->secs[p] / 60;
    plays_text(plays, sizeof plays, s->plays[p]);
    if (min >= 60)
        tk_snprintf(detail, sizeof detail, tr("%u h %u min · %s"), min / 60, min % 60, plays);
    else
        tk_snprintf(detail, sizeof detail, tr("%u min · %s"), min, plays);
    page_row_detail(view, 0, tr("Time Played"), detail, 0);
    for (unsigned k = 0; k < KINDS; k++) {
        unsigned n = s->count[p][k];
        tk_snprintf(detail, sizeof detail, "%u %s", n, tr(kind_unit[k][n != 1]));
        page_row_detail_into(view, (int)k + 1, tr(kind_name[k]), detail, top_open);
    }
    return 0;
}

static void stats_show(void) {
    int any = lst.s->plays[ALL] != 0;
    void *view = page_list_or(lst.page, lst.page, tr("Listening Stats"), tr("No listens yet"),
                              any ? PERIODS : 0, 48);
    for (int p = 0; any && p < PERIODS; p++)
        page_row_into(view, p, tr(period_name[p]), period_open);
}

static int stats_poll(const void *unused) {
    if (!lst.s || !__atomic_load_n(&lst.s->done, __ATOMIC_ACQUIRE)) return 8; /* RET_REPEAT */
    if (lst.threaded) pthread_join(lst.thread, 0);
    lst.threaded = 0;
    lst.timer = 0;
    stats_show();
    return 0;
}

/* The page gone: a count still running stops at its next read and is joined here. */
static int stats_closed(void *ctx, event_t *event) {
    lst.page = 0;
    stop_timer(&lst.timer);
    if (lst.s) lst.s->cancel = 1;
    if (lst.threaded) pthread_join(lst.thread, 0);
    lst.threaded = 0;
    stats_free(lst.s);
    lst.s = 0;
    return 0;
}

/* The Library's Listening Stats row: the page, counting, then its periods. Without a thread the
 * count runs here, before the page shows. */
int listening_stats(void *ctx, event_t *event) {
    if (lst.page) return 0;
    stats_t *s = calloc(1, sizeof *s);
    if (s) {
        s->slot = calloc(STATS_SLOTS, sizeof *s->slot);
        s->key = calloc(STATS_KEYS, sizeof *s->key);
        s->pool = calloc(1, STATS_POOL);
        s->buf = calloc(1, STATS_BUF);
    }
    if (!s || !s->slot || !s->key || !s->pool || !s->buf) {
        stats_free(s);
        toast(tr("Not enough memory"));
        return 0;
    }
    s->used = 1; /* offset 0: "" */
    stat_bounds(s->from);
    if (!(lst.page = page_open("stats_page", stats_closed, page_keyup))) {
        stats_free(s);
        return 0;
    }
    lst.s = s;
    page_row_detail(page_list(lst.page, lst.page, 0, tr("Listening Stats"), 1, 48), 0,
                    tr("Counting listens\xe2\x80\xa6"), 0, 0);
    lst.threaded = !pthread_create(&lst.thread, 0, stat_worker, s);
    if (!lst.threaded) stat_worker(s);
    lst.timer = timer_add(stats_poll, (void *)0, 250);
    return 0;
}
