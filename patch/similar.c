/* More like this (docs/internals.md#more-like-this): a row of Now Playing's Play/Pause hold menu
 * asks Last.fm's artist.getSimilar for the playing song's artist, on a thread of its own, and plays
 * a shuffled mix of the library's songs by the similar artists it has, with a few by the artist.
 * The key is .scrobble.ini's [LASTFM] API_KEY; getSimilar needs no session or secret. */
#include "navigation.h"

#define SIM_INI "/mnt/mmc/.scrobble.ini" /* scrobble.c's */
#define SIM_CA "/etc/scrobble-ca.pem"    /* scrobble.c's, which verifies Last.fm */
#define SIM_MIX 50                       /* songs in a mix */
#define SIM_OWN 5                        /* of them by the playing song's artist */
#define SIM_ASK 100                      /* similar artists asked for */
#define SIM_STR(n) #n
#define SIM_LIMIT(n) SIM_STR(n) /* SIM_ASK in the URL */
#define SIM_NAME 256            /* an artist's name, as keyinfo's */
#define SIM_MAX (512 << 10)     /* the reply: 100 artists with 6 image URLs each, ~80 KB */
#define SIM_WAIT_MS 250
enum { SIM_REFUSED = -2, SIM_OFFLINE };

static struct {
    char (*names)[SIM_NAME]; /* the worker's answer: the similar artists' keys (sim_key) */
    unsigned long thread;    /* 0 once joined */
    volatile int running, cancel;
    int got; /* names, or SIM_OFFLINE, SIM_REFUSED (with Last.fm's message) */
    char artist[SIM_NAME], path[0x200], key[256], message[128];
} sim __attribute__((section(".scratch")));

/* name as it is matched: ASCII case folded, a leading "The " and the spaces around it dropped (a
 * name that is only "The" keeps it); out may be name. */
static void sim_key(char *out, unsigned n, const char *name) {
    while (*name == ' ') ++name;
    if (!strncasecmp(name, "the ", 4)) {
        const char *rest = name + 4;
        while (*rest == ' ') ++rest;
        if (*rest) name = rest;
    }
    unsigned at = 0;
    for (; *name && at + 1 < n; ++name)
        out[at++] = *name >= 'A' && *name <= 'Z' ? (char)(*name | 32) : *name;
    while (at && out[at - 1] == ' ') --at;
    out[at] = 0;
}

/* getSimilar's URL for artist with key, each percent-encoded; 0 if it does not fit in n. */
static int sim_url(char *out, unsigned n, const char *artist, const char *key) {
    static const char hex[] = "0123456789ABCDEF";
    const char *part[] = { "https://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist=",
                           artist, "&api_key=", key,
                           /* NOLINTNEXTLINE(bugprone-suspicious-missing-comma): one part, joined */
                           "&limit=" SIM_LIMIT(SIM_ASK) "&autocorrect=1&format=json" };
    unsigned at = 0;
    for (int i = 0; i < 5; ++i)
        for (const unsigned char *s = (const unsigned char *)part[i]; *s; ++s) {
            int keep = !(i & 1) || (*s >= '0' && *s <= '9') ||
                       ((*s | 32) >= 'a' && (*s | 32) <= 'z') || *s == '-' || *s == '.' ||
                       *s == '_' || *s == '~';
            if (at + (keep ? 1 : 3) >= n) return 0;
            if (keep)
                out[at++] = (char)*s;
            else
                out[at++] = '%', out[at++] = hex[*s >> 4], out[at++] = hex[*s & 15];
        }
    out[at] = 0;
    return 1;
}

/* A string member of a cJSON object, or 0 (cJSON's valuestring at +16, 0 unless a string). */
static const char *sim_str(void *json, const char *name) {
    void *v = json ? cJSON_GetObjectItem(json, name) : 0;
    return v ? *(const char **)((char *)v + 16) : 0;
}

static int sim_keep(char (*names)[SIM_NAME], int got, const char *name) {
    if (!name || !*name || strlen(name) >= SIM_NAME) return got;
    memcpy(names[got], name, strlen(name) + 1);
    return got + 1;
}

/* body's similar artists, at most max, to names in Last.fm's order (most similar first): their
 * count; SIM_REFUSED for an error reply, its message to message; SIM_OFFLINE for anything else. A
 * list of one may come as its member alone (Last.fm's JSON is its XML converted). */
static int sim_parse(const char *body, char (*names)[SIM_NAME], int max, char *message,
                     unsigned n) {
    void *json = cJSON_Parse(body), *list = json ? cJSON_GetObjectItem(json, "similarartists") : 0;
    void *artists = list ? cJSON_GetObjectItem(list, "artist") : 0;
    const char *said = json ? sim_str(json, "message") : 0;
    int got = 0;
    *message = 0;
    if (json && cJSON_GetObjectItem(json, "error")) {
        snprintf(message, n, "%s", said ? said : "");
        got = SIM_REFUSED;
    } else if (!list)
        got = SIM_OFFLINE;
    else if (sim_str(artists, "name"))
        got = sim_keep(names, 0, sim_str(artists, "name"));
    else
        for (int i = 0, k = artists ? cJSON_GetArraySize(artists) : 0; i < k && got < max; ++i)
            got = sim_keep(names, got, sim_str(cJSON_GetArrayItem(artists, i), "name"));
    cJSON_Delete(json);
    return got;
}

static void sim_swap(unsigned *a, unsigned *b) {
    unsigned t = *a;
    *a = *b, *b = t;
}

/* The mix of n songs, each by artists[i] (0: not one to pick): into pick, SIM_MIX at most, its
 * count, and *by the artists it has. SIM_OWN at most by own (a key), the rest by the k similar
 * artists' keys, a song a round each, so a big catalogue does not crowd a small one out; the picks
 * shuffled by rnd. None without a song by a similar artist. */
static unsigned sim_mix(const char *const *artists, unsigned n, const char (*keys)[SIM_NAME], int k,
                        const char *own, unsigned (*rnd)(unsigned), unsigned *pick, unsigned *by) {
    unsigned *song = calloc(n + 1, sizeof *song), *group = calloc(n + 1, sizeof *group),
             *taken = calloc((unsigned)k + 1, sizeof *taken), m = 0, got = 0, similar = 0;
    char key[SIM_NAME];
    *by = 0;
    for (unsigned i = 0; song && group && taken && i < n; ++i) {
        if (!artists[i] || !*artists[i]) continue;
        sim_key(key, sizeof key, artists[i]);
        int g = 0; /* own's songs are its group, k; a similar artist's its index */
        if (!strcmp(key, own))
            g = k;
        else {
            while (g < k && strcmp(key, keys[g])) ++g;
            if (g == k) continue;
        }
        song[m] = i, group[m++] = (unsigned)g;
        similar += g < k;
    }
    for (unsigned i = m; i > 1; --i) { /* Fisher-Yates */
        unsigned j = rnd(i);
        sim_swap(&song[i - 1], &song[j]), sim_swap(&group[i - 1], &group[j]);
    }
    for (unsigned i = 0; similar && i < m && taken[k] < SIM_OWN; ++i)
        if (group[i] == (unsigned)k) pick[got++] = song[i], ++taken[k];
    for (unsigned round = 0, any = 1; similar && any && got < SIM_MIX; ++round) {
        any = 0;
        for (unsigned i = 0; i < m && got < SIM_MIX; ++i)
            if (group[i] < (unsigned)k && taken[group[i]] == round) {
                pick[got++] = song[i], ++taken[group[i]];
                group[i] = (unsigned)k + 1, any = 1; /* picked */
            }
    }
    for (int g = 0; taken && g <= k; ++g) *by += taken[g] != 0;
    for (unsigned i = got; i > 1; --i) sim_swap(&pick[i - 1], &pick[rnd(i)]);
    free(song), free(group), free(taken);
    return got;
}

typedef struct {
    char *buf;
    unsigned len, max;
} sim_body;

static unsigned sim_got(const char *data, unsigned size, unsigned n, void *ctx) {
    sim_body *b = ctx;
    unsigned add = size * n;
    if (b->len + add >= b->max) return 0; /* too big: the transfer fails */
    memcpy(b->buf + b->len, data, add);
    b->len += add;
    return add;
}

/* curl's progress callback: USB storage's stop ends the transfer. */
static int sim_stopped(void *unused, long long total, long long now, long long up, long long sent) {
    return sim.cancel;
}

/* url's reply, NUL-terminated, to free; 0 if the transfer failed or was stopped. Verified TLS with
 * the firmware's CA file, as scrobble.c's calls to Last.fm. */
static char *sim_get(const char *url) {
    sim_body b = { calloc(1, SIM_MAX), 0, SIM_MAX };
    void *c = b.buf ? curl_easy_init() : 0;
    int bad = !c;
    if (c) {
        curl_easy_setopt(c, 10002, url);         /* CURLOPT_URL */
        curl_easy_setopt(c, 10018, Q2POD_UA);    /* USERAGENT */
        curl_easy_setopt(c, 99, 1L);             /* NOSIGNAL: off the UI thread */
        curl_easy_setopt(c, 78, 10L);            /* CONNECTTIMEOUT */
        curl_easy_setopt(c, 13, 20L);            /* TIMEOUT */
        curl_easy_setopt(c, 10065, SIM_CA);      /* CAINFO */
        curl_easy_setopt(c, 64, 1L);             /* SSL_VERIFYPEER */
        curl_easy_setopt(c, 81, 2L);             /* SSL_VERIFYHOST */
        curl_easy_setopt(c, 20108, tls_untimed); /* SSL_CTX_FUNCTION: no date checks */
        curl_easy_setopt(c, 20011, sim_got);     /* WRITEFUNCTION */
        curl_easy_setopt(c, 10001, &b);          /* WRITEDATA */
        curl_easy_setopt(c, 20219, sim_stopped); /* XFERINFOFUNCTION */
        curl_easy_setopt(c, 43, 0L);             /* NOPROGRESS off, for it */
        bad = curl_easy_perform(c) !=
              0; /* an error reply (HTTP 4xx) is JSON too, which sim_parse reads */
        curl_easy_cleanup(c);
    }
    if (bad) free(b.buf), b.buf = 0;
    return b.buf;
}

static void *sim_worker(void *unused) {
    char url[3 * (SIM_NAME + 256) + 128];
    char *body = sim_url(url, sizeof url, sim.artist, sim.key) ? sim_get(url) : 0;
    sim.got =
        body ? sim_parse(body, sim.names, SIM_ASK, sim.message, sizeof sim.message) : SIM_OFFLINE;
    for (int i = 0; i < sim.got; ++i) sim_key(sim.names[i], SIM_NAME, sim.names[i]);
    free(body);
    sim.running = 0;
    return 0;
}

/* The worker's answer, on the UI thread: the mix from Shuffle Songs' query of the library (every
 * song, unsorted: stock's queries run on the UI thread alone), played as Shuffle Songs plays, less
 * the playing song; or a toast that says why not. */
static void sim_play(void) {
    char note[160], own[SIM_NAME];
    if (sim.got == SIM_OFFLINE) {
        toast("Can't reach Last.fm");
        return;
    }
    if (sim.got == SIM_REFUSED) {
        snprintf(note, sizeof note, "Last.fm: %s", sim.message);
        toast(note);
        return;
    }
    if (!sim.got) {
        snprintf(note, sizeof note, "Last.fm knows no artists like %s", sim.artist);
        toast(note);
        return;
    }
    int count;
    void *all = staged(songs_unsorted, (void *)1, &count);
    unsigned n = deque_size(all), pick[SIM_MIX], by = 0, got = 0;
    const char **artists = calloc(n + 1, sizeof *artists);
    for (unsigned i = 0; artists && i < n; ++i) {
        void *r = deque_at(all, i);
        const char *path = P(r, REC_PATH);
        if (I(r, REC_TYPE) == 8 && !playback_video(r) && path && strcmp(path, sim.path))
            artists[i] = P(r, REC_ARTIST);
    }
    sim_key(own, sizeof own, sim.artist);
    if (artists)
        got = sim_mix(artists, n, (const char (*)[SIM_NAME])sim.names, sim.got, own, rand_below,
                      pick, &by);
    free(artists);
    if (got) {
        void *mix = _create_deque("stSongInfo");
        deque_init(mix);
        for (unsigned i = 0; i < got; ++i) _deque_push_back(mix, deque_at(all, pick[i]));
        shuffle_play(mix);
        deque_destroy(mix);
        snprintf(note, sizeof note, "Playing %u songs by %u artists", got, by);
    }
    deque_destroy(all);
    toast(got ? note : "No similar artists in the library");
}

/* The worker joined; once. */
static void sim_finish(void) {
    if (!sim.thread) return;
    pthread_join(sim.thread, 0);
    sim.thread = 0;
}

/* Every SIM_WAIT_MS while the worker asks; then its mix, once no toast is up (a toast's own loop
 * runs this timer). */
static int sim_wait(const void *unused) {
    if (!sim.thread) return 0;              /* stopped for USB storage */
    if (sim.running || st.toasts) return 8; /* RET_REPEAT */
    sim_finish();
    sim_play();
    free(sim.names);
    sim.names = 0;
    return 0;
}

/* The playing song's record, with an artist; 0 otherwise. */
static void *sim_playing(void) {
    void *q = P(mcl_pdeqplaylist, 0), *r;
    unsigned pos = (unsigned)I(MCL_POS, 0);
    if (!q || pos >= deque_size(q) || !(r = deque_at(q, pos))) return 0;
    const char *artist = P(r, REC_ARTIST);
    return artist && *artist ? r : 0;
}

static void sim_put(char *to, unsigned n, const char *s) { snprintf(to, n, "%s", s ? s : ""); }

/* The row's press (queue_menu.c qm_run), and Smart Wi-Fi's once its link is up: the key, the link,
 * then the worker and its timer. A press while it asks starts nothing more. */
int similar_click(void *ctx, void *event) {
    void *r;
    if (sim.thread) {
        toast("Asking Last.fm\xe2\x80\xa6");
        return 0;
    }
    if (!(r = sim_playing())) {
        toast("This song has no artist");
        return 0;
    }
    sim.key[0] = 0;
    toolsReadConfig(SIM_INI, "LASTFM", "API_KEY", sim.key, "");
    if (!sim.key[0]) {
        toast("No Last.fm API key in .scrobble.ini");
        return 0;
    }
    if (!need_link(similar_click, ctx)) return 0; /* held for Smart Wi-Fi's link, or none */
    sim_put(sim.artist, sizeof sim.artist, P(r, REC_ARTIST));
    sim_put(sim.path, sizeof sim.path, P(r, REC_PATH));
    sim.cancel = 0, sim.got = 0, sim.running = 1;
    sim.names = calloc(SIM_ASK, SIM_NAME);
    if (!sim.names || pthread_create(&sim.thread, 0, sim_worker, 0)) {
        sim.thread = 0, sim.running = 0;
        free(sim.names), sim.names = 0;
        toast("Can't ask Last.fm");
        return 0;
    }
    timer_add(sim_wait, (void *)0, SIM_WAIT_MS);
    toast("Asking Last.fm\xe2\x80\xa6");
    return 0;
}

/* USB storage takes the card: the request stops (its transfer at the next progress call) and
 * nothing plays (navigation.c ringnav_usbmode). */
void similar_stop(void) {
    sim.cancel = 1;
    sim_finish();
    free(sim.names);
    sim.names = 0;
}

/* Smart Wi-Fi (smart_wifi.c smart_needed): a request under way keeps the radio on. */
int similar_busy(void) { return sim.running; }
