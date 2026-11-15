/* OPRA search (docs/internals.md#opra): the EQ page's Presets → Search OPRA finds a headphone in
 * the OPRA project's EQ database (https://github.com/opra-project/OPRA, CC BY-SA 4.0) and saves
 * one of its presets as an import is saved. The database, a JSON Lines file of about 13 MB, comes
 * over Wi-Fi to the card on a pthread through demo's libcurl, and one pass over it there writes an
 * index of each product's name and the byte spans of its presets' records; a search reads only the
 * index, and a product's presets are read from their spans. */
#include "peq.h"

#define OPRA_URL "https://opra.roonlabs.net/database_v1.jsonl"
#define OPRA_CA PEQ_ROOT "/etc/scrobble-ca.pem" /* scrobble.c's; GTS Root R4 signs the host */
#define OPRA_DIR PEQ_ROOT "/mnt/mmc/.q2"
#define OPRA_DB OPRA_DIR "/opra.jsonl"
#define OPRA_INDEX OPRA_DIR "/opra.idx"
#define OPRA_PART OPRA_DB ".part" /* the download, until it is indexed */
#define OPRA_NEW OPRA_INDEX ".part"
#define OPRA_READ 16384 /* a read; a longer line is skipped (the database's longest is 1.6 KB) */
#define OPRA_TEXT 160   /* a name, a product's id or a preset's details (the longest, 128) */
#define OPRA_OUT 4096   /* an index line: a name and OPRA_PRESETS spans */
#define OPRA_HITS 128   /* a search's results */
#define OPRA_PRESETS 64 /* a product's presets (the most, 34) */

/* JSON, enough for the database's records: a value is skipped whole, a member found among its
 * object's own, a string read with its escapes. */
static const char *ws(const char *p) {
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') ++p;
    return p;
}

/* Past the string whose opening quote is at p; 0 if it never closes. */
static const char *past_string(const char *p) {
    for (++p; *p != '"'; ++p)
        if (!*p || (*p == '\\' && !*++p)) return 0;
    return p + 1;
}

/* Past the value at p; 0 if it is not one. */
static const char *past(const char *p, int depth) {
    p = ws(p);
    if (*p == '"') return past_string(p);
    if (*p == '{' || *p == '[') {
        char close = *p == '{' ? '}' : ']';
        if (depth > 16) return 0;
        if (*(p = ws(p + 1)) == close) return p + 1;
        for (;;) {
            if (close == '}' && (*p != '"' || !(p = past_string(p)) || *(p = ws(p)) != ':'))
                return 0;
            if (!(p = past(p + (close == '}'), depth + 1))) return 0;
            if (*(p = ws(p)) == close) return p + 1;
            if (*p != ',') return 0;
            p = ws(p + 1);
        }
    }
    const char *s = p; /* a number, true, false or null */
    while ((*p >= '0' && *p <= '9') || ((*p | 32) >= 'a' && (*p | 32) <= 'z') || *p == '+' ||
           *p == '-' || *p == '.')
        ++p;
    return p > s ? p : 0;
}

/* The value of the object at p's member key; 0 if p is no object or has none. */
static const char *member(const char *p, const char *key) {
    unsigned n = strlen(key);
    if (!p || *(p = ws(p)) != '{') return 0;
    for (p = ws(p + 1); *p == '"'; p = ws(p + 1)) {
        const char *k = p + 1, *v = past_string(p);
        if (!v || *(v = ws(v)) != ':') return 0;
        v = ws(v + 1);
        if ((unsigned)(v - k) > n && k[n] == '"' && !strncmp(k, key, n)) return v;
        if (!(p = past(v, 1)) || *(p = ws(p)) != ',') return 0;
    }
    return 0;
}

/* The string at p into out (n bytes, cut to fit), UTF-8; a control character becomes a space, so
 * the index's tabs and newlines stay its own. 0 if p is no string. */
static int string(const char *p, char *out, unsigned n) {
    unsigned k = 0;
    if (!p || *p != '"') return 0;
    for (++p; *p != '"'; ++p) {
        unsigned c = (unsigned char)*p, len = 1;
        char u[3];
        if (!c) return 0;
        if (c == '\\' && *++p == 'u') {
            c = 0;
            for (int i = 0; i < 4; ++i, ++p)
                if (hex(p[1]) < 0)
                    return 0;
                else
                    c = c * 16 + hex(p[1]);
            if (c >= 0xd800 && c < 0xe000) c = '?'; /* half a pair: none in the database */
            if (c >= 0x800)
                u[0] = 0xe0 | c >> 12, u[1] = 0x80 | (c >> 6 & 63), u[2] = 0x80 | (c & 63), len = 3;
            else if (c >= 0x80)
                u[0] = 0xc0 | c >> 6, u[1] = 0x80 | (c & 63), len = 2;
        } else if (c == '\\') {
            c = (unsigned char)*p;
            if (c == 'b' || c == 'f' || c == 'n' || c == 'r' || c == 't')
                c = ' ';
            else if (c != '"' && c != '\\' && c != '/')
                return 0;
        }
        if (len == 1) u[0] = c < 32 ? ' ' : c; /* a byte of raw UTF-8 as it is */
        for (unsigned i = 0; i < len && k + 1 < n; ++i) out[k++] = u[i];
    }
    out[k] = 0;
    return 1;
}

/* The number at p; 0 if p is none (peq_number's: decimal, finite). */
static int json_number(const char *p, double *out) {
    char s[32];
    unsigned k = 0;
    if (!p) return 0;
    while (k + 1 < sizeof s && ((p[k] >= '0' && p[k] <= '9') || p[k] == '-' || p[k] == '+' ||
                                p[k] == '.' || p[k] == 'e' || p[k] == 'E'))
        s[k] = p[k], ++k;
    s[k] = 0;
    return k && peq_number(s, out);
}

/* An OPRA eq record (a line of the database) as a preset with its own preamp, as an import's, and
 * its author, details and product's id (n bytes each). Its filters are PEQ's three types: OPRA's
 * shelves take a Q at their centre, as APO's LSC and HSC. A band below 20 Hz or over Q 10 is kept
 * at PEQ's limit, as peq_parse keeps a shelf it moves; 0 for any other record, a filter PEQ has no
 * type for (a low or high pass, a band pass or stop), more than PEQ_BANDS bands or a gain outside
 * PEQ's range. */
int opra_record(const char *line, peq_preset *out, char *author, char *details, char *product,
                unsigned n) {
    const char *data = member(line, "data"), *params = member(data, "parameters"),
               *b = member(params, "bands");
    char kind[16];
    peq_preset p;
    peq_default(&p);
    p.count = p.bypass = 0;
    p.automatic = 2;
    if (!string(member(line, "type"), kind, sizeof kind) || strcmp(kind, "eq") ||
        !string(member(data, "author"), author, n) ||
        !string(member(data, "product_id"), product, n) ||
        !json_number(member(params, "gain_db"), &p.preamp) || !b || *b != '[')
        return 0;
    if (!string(member(data, "details"), details, n)) *details = 0;
    if (*(b = ws(b + 1)) != ']')
        for (;;) {
            peq_band x = { 1, -1, 0, 0, 0 };
            if (string(member(b, "type"), kind, sizeof kind))
                x.type = !strcmp(kind, "peak_dip")     ? 0
                         : !strcmp(kind, "low_shelf")  ? 1
                         : !strcmp(kind, "high_shelf") ? 2
                                                       : -1;
            if (x.type < 0 || p.count == PEQ_BANDS ||
                !json_number(member(b, "frequency"), &x.frequency) ||
                !json_number(member(b, "gain_db"), &x.gain) || !json_number(member(b, "q"), &x.q))
                return 0;
            x.frequency = x.frequency < 20 ? 20 : x.frequency > 20000 ? 20000 : x.frequency;
            x.q = x.q < 0.1 ? 0.1 : x.q > 10 ? 10 : x.q;
            p.bands[p.count++] = x;
            if (!(b = past(b, 1)) || (*(b = ws(b)) != ',' && *b != ']')) return 0;
            if (*b == ']') break;
            b = ws(b + 1);
        }
    if (!peq_valid(&p)) return 0;
    *out = p;
    return 1;
}

/* A file's lines through one buffer, each with its byte offset and length (its newline too). */
typedef struct {
    void *f;
    char *buf; /* OPRA_READ + 1 bytes */
    unsigned at, end;
    long pos;
    int eof, skip; /* skip: the line at buf's start began before it, too long to read */
} reader;

/* The next line, NUL-terminated in place of its newline; 0 at the file's end. */
static char *next_line(reader *r, long *off, unsigned *len) {
    for (;;) {
        char *s = r->buf + r->at, *e = s, *end = r->buf + r->end;
        while (e < end && *e != '\n') ++e;
        if (e < end || (r->eof && e > s)) {
            *len = e - s + (e < end);
            *off = r->pos;
            r->pos += *len, r->at += *len;
            *e = 0;
            if (r->skip) {
                r->skip = 0;
                continue;
            }
            return s;
        }
        if (r->eof) return 0;
        memmove(r->buf, s, e - s);
        r->end = e - s, r->at = 0;
        if (r->end == OPRA_READ) r->pos += r->end, r->end = 0, r->skip = 1;
        unsigned got = fread(r->buf + r->end, 1, OPRA_READ - r->end, r->f);
        r->eof = !got;
        r->end += got;
    }
}

/* The download's pthread and its tick: stage 0 the transfer (bytes so far), 1 the index (presets so
 * far); result, once done, one of R_*; changed: the page told of each change. */
enum { R_OK = 1, R_NET, R_CARD, R_DATA, R_CANCEL };
static struct {
    unsigned long thread;
    int running;
    volatile int cancel, done, stage, presets, result;
    volatile unsigned bytes;
    unsigned timer;
    void (*changed)(const char *);
    char shown[64]; /* the progress last told */
} op __attribute__((section(".scratch")));

/* The index of the database at db, to the file index: a line per product with a usable preset,
 * "Vendor Product", then a tab and "offset length" for each of its presets. The database lists a
 * vendor before its products and a product after its presets, so a product takes the presets read
 * since the one before it whose product id is its. The presets indexed; -1 if either file fails
 * or op.cancel is set. */
int opra_index(const char *db, const char *index) {
    struct vendor {
        char id[64], name[64];
    } *v = 0, *grown;
    struct {
        long off;
        unsigned len;
        char product[OPRA_TEXT];
    } *e = calloc(OPRA_PRESETS, sizeof *e);
    reader r = { .f = fopen(db, "rb"), .buf = calloc(1, OPRA_READ + 1) };
    void *out = r.f ? fopen(index, "wb") : 0;
    char *line = calloc(1, OPRA_OUT), kind[16], id[OPRA_TEXT], name[OPRA_TEXT], vendor[64];
    char author[OPRA_TEXT], details[OPRA_TEXT];
    int presets = 0, vendors = 0, room = 0, pending = 0, bad = !out || !e || !r.buf || !line;
    peq_preset p;
    long off;
    unsigned len;
    for (char *s; !bad && (s = next_line(&r, &off, &len));) {
        const char *data = member(s, "data");
        if (op.cancel)
            bad = 1;
        else if (!string(member(s, "type"), kind, sizeof kind))
            continue;
        else if (!strcmp(kind, "eq")) {
            if (pending < OPRA_PRESETS &&
                opra_record(s, &p, author, details, e[pending].product, OPRA_TEXT))
                e[pending].off = off, e[pending].len = len, ++pending;
        } else if (!strcmp(kind, "vendor")) {
            if (vendors == room) {
                if (!(grown = realloc(v, (room ? 2 * room : 256) * sizeof *v))) {
                    bad = 1;
                    continue;
                }
                v = grown, room = room ? 2 * room : 256;
            }
            if (string(member(s, "id"), v[vendors].id, 64) &&
                string(member(data, "name"), v[vendors].name, 64))
                ++vendors;
        } else if (!strcmp(kind, "product") && string(member(s, "id"), id, sizeof id) &&
                   string(member(data, "name"), name, sizeof name)) {
            const char *by = 0;
            if (string(member(data, "vendor_id"), vendor, sizeof vendor))
                for (int k = vendors; k-- > 0 && !by;)
                    if (!strcmp(v[k].id, vendor)) by = v[k].name;
            unsigned at = snprintf(line, OPRA_OUT, "%s%s%s", by ? by : "", by ? " " : "", name);
            int found = 0;
            for (int k = 0; k < pending; ++k)
                if (!strcmp(e[k].product, id))
                    at += snprintf(line + at, OPRA_OUT - at, "\t%ld %u", e[k].off, e[k].len),
                        ++found;
            line[at++] = '\n';
            if (found) {
                bad = fwrite(line, 1, at, out) != at;
                op.presets = presets += found;
            }
            pending = 0;
        }
    }
    free(v), free(e), free(r.buf), free(line);
    if (r.f) fclose(r.f);
    if (out && fclose(out)) bad = 1;
    if (bad && out) unlink(index);
    return bad ? -1 : presets;
}

/* A search's results and the product opened from them: its presets. */
static struct {
    struct {
        long at; /* its line in the index */
        char name[OPRA_TEXT];
    } *hit;
    struct {
        char author[OPRA_TEXT], details[OPRA_TEXT];
        peq_preset p;
    } *preset;
    int hits, presets, open;
    char query[OPRA_TEXT];
} os __attribute__((section(".scratch")));

/* s's letters and digits (bytes past ASCII too), lower case: "HD 600" and "hd600" alike. */
static void flatten(const char *s, unsigned len, char *out, unsigned n) {
    unsigned k = 0;
    for (unsigned i = 0; i < len && s[i] && k + 1 < n; ++i) {
        unsigned char c = s[i] >= 'A' && s[i] <= 'Z' ? s[i] | 32 : s[i];
        if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c >= 128) out[k++] = c;
    }
    out[k] = 0;
}

/* Every word of query is in name, by flatten; a query of no words matches nothing. */
static int matches(const char *name, const char *query) {
    char flat[OPRA_TEXT], word[OPRA_TEXT];
    int any = 0;
    flatten(name, OPRA_TEXT, flat, sizeof flat);
    while (*query) {
        while (*query == ' ') ++query;
        const char *w = query;
        while (*query && *query != ' ') ++query;
        flatten(w, query - w, word, sizeof word);
        if (*word && (any = 1, !strstr(flat, word))) return 0;
    }
    return any;
}

/* The index's products that match query, OPRA_HITS at most, in its order: their count; -1 with no
 * index. */
int opra_find(const char *query) {
    long off;
    unsigned len;
    snprintf(os.query, sizeof os.query, "%s", query);
    os.hits = os.presets = 0;
    if (!os.hit && !(os.hit = calloc(OPRA_HITS, sizeof *os.hit))) return -1;
    reader r = { .f = fopen(OPRA_INDEX, "rb"), .buf = calloc(1, OPRA_READ + 1) };
    if (r.f && r.buf)
        for (char *s, *tab; os.hits < OPRA_HITS && (s = next_line(&r, &off, &len));) {
            for (tab = s; *tab && *tab != '\t'; ++tab) {}
            *tab = 0;
            if (matches(s, os.query)) {
                os.hit[os.hits].at = off;
                snprintf(os.hit[os.hits++].name, OPRA_TEXT, "%s", s);
            }
        }
    free(r.buf);
    if (!r.f) return -1;
    fclose(r.f);
    return os.hits;
}

const char *opra_query(void) { return os.query; }
const char *opra_hit(int i) { return i >= 0 && i < os.hits ? os.hit[i].name : 0; }
const char *opra_opened(void) { return opra_hit(os.open); } /* the result opra_open read */

/* Result i's presets read from the database, any that no longer read left out: their count; -1 past
 * the results or with no database. */
int opra_open(int i) {
    void *f = i >= 0 && i < os.hits ? fopen(OPRA_INDEX, "rb") : 0,
         *db = f ? fopen(OPRA_DB, "rb") : 0;
    char *rec = calloc(1, OPRA_READ + 1), product[OPRA_TEXT];
    reader r = { .f = f, .buf = calloc(1, OPRA_READ + 1) };
    long off, at;
    unsigned len;
    int n = -1;
    if (!os.preset) os.preset = calloc(OPRA_PRESETS, sizeof *os.preset);
    char *s = db && rec && r.buf && os.preset && !fseek(f, os.hit[i].at, 0)
                  ? next_line(&r, &off, &len)
                  : 0;
    for (n = s ? 0 : -1; s && (s = strstr(s, "\t")) && n < OPRA_PRESETS;) {
        at = strtol(s + 1, &s, 10);
        len = strtol(s, &s, 10);
        if (len <= OPRA_READ && !fseek(db, at, 0) && fread(rec, 1, len, db) == len &&
            (rec[len] = 0, opra_record(rec, &os.preset[n].p, os.preset[n].author,
                                       os.preset[n].details, product, OPRA_TEXT)))
            ++n;
    }
    free(rec), free(r.buf);
    if (db) fclose(db);
    if (f) fclose(f);
    os.presets = n > 0 ? n : 0;
    os.open = i;
    return n;
}

/* The open product's preset i's author, and its details in *details; 0 past the last. */
const char *opra_choice(int i, const char **details) {
    if (i < 0 || i >= os.presets) return 0;
    if (details) *details = os.preset[i].details;
    return os.preset[i].author;
}

/* Preset i into out and its name, "Vendor Product (author, details)", into name (n bytes), with
 * the characters the card's FAT refuses as dashes, as Rename refuses them; 0 past the last. */
int opra_take(int i, peq_preset *out, char *name, unsigned n) {
    if (i < 0 || i >= os.presets) return 0;
    *out = os.preset[i].p;
    snprintf(name, n, *os.preset[i].details ? "%s (%s, %s)" : "%s (%s%s)", os.hit[os.open].name,
             os.preset[i].author, os.preset[i].details);
    for (char *s = name; *s; ++s)
        for (const char *bad = "/\\:*?\"<>|"; *bad; ++bad)
            if (*s == *bad) *s = '-';
    return 1;
}

/* The database has been downloaded and indexed. */
int opra_ready(void) { return !access(OPRA_INDEX, 0); }
int opra_busy(void) { return op.running; } /* Smart Wi-Fi keeps the radio on for it */

static void *part; /* the download's file, for got */
static unsigned got(const char *data, unsigned size, unsigned n, void *unused) {
    unsigned wrote = op.cancel ? 0 : fwrite(data, size, n, part) * size;
    if (wrote != size * n && !op.cancel) op.result = R_CARD;
    op.bytes += wrote;
    return wrote; /* short: the transfer stops */
}

static int stopped(void *unused, long long a, long long b, long long c, long long d) {
    return op.cancel; /* closing the page: the transfer ends within a second */
}

/* The download to OPRA_PART, indexed to OPRA_NEW, both renamed over the last only when whole: a
 * failure or a cancel keeps the database there was. */
static void *worker(void *unused) {
    long code = 0;
    int err = 1;
    mkdir(OPRA_DIR, 0700);
    if (!(part = fopen(OPRA_PART, "wb"))) {
        op.result = R_CARD;
        op.done = 1;
        return 0;
    }
    void *c = curl_easy_init();
    if (c) {
        curl_easy_setopt(c, 10002, OPRA_URL);    /* CURLOPT_URL */
        curl_easy_setopt(c, 20011, got);         /* WRITEFUNCTION */
        curl_easy_setopt(c, 20219, stopped);     /* XFERINFOFUNCTION */
        curl_easy_setopt(c, 43, 0L);             /* NOPROGRESS off, for it */
        curl_easy_setopt(c, 99, 1L);             /* NOSIGNAL: off the UI thread */
        curl_easy_setopt(c, 52, 1L);             /* FOLLOWLOCATION */
        curl_easy_setopt(c, 78, 15L);            /* CONNECTTIMEOUT */
        curl_easy_setopt(c, 19, 1024L);          /* LOW_SPEED_LIMIT: a stalled transfer ends */
        curl_easy_setopt(c, 20, 30L);            /* LOW_SPEED_TIME */
        curl_easy_setopt(c, 10018, "Q2Pod");     /* USERAGENT */
        curl_easy_setopt(c, 10065, OPRA_CA);     /* CAINFO */
        curl_easy_setopt(c, 64, 1L);             /* SSL_VERIFYPEER */
        curl_easy_setopt(c, 81, 2L);             /* SSL_VERIFYHOST */
        curl_easy_setopt(c, 20108, tls_untimed); /* SSL_CTX_FUNCTION: no date checks */
        err = curl_easy_perform(c);
        if (!err) curl_easy_getinfo(c, 0x200002, &code); /* RESPONSE_CODE */
        curl_easy_cleanup(c);
    }
    if (fclose(part) && !op.result) op.result = R_CARD;
    if (op.cancel)
        op.result = R_CANCEL;
    else if (!op.result && (err || code != 200))
        op.result = R_NET;
    if (!op.result) {
        op.stage = 1;
        int n = opra_index(OPRA_PART, OPRA_NEW);
        op.result = op.cancel ? R_CANCEL : n < 0 ? R_CARD : !n ? R_DATA : 0;
    }
    if (!op.result && (rename(OPRA_PART, OPRA_DB) || rename(OPRA_NEW, OPRA_INDEX)))
        op.result = R_CARD;
    if (op.result)
        unlink(OPRA_PART), unlink(OPRA_NEW);
    else
        op.result = R_OK;
    op.done = 1;
    return 0;
}

/* Every 500 ms while the download runs: its progress told when it changes, then its end. */
static int tick(const void *unused) {
    char text[64];
    if (op.done) {
        pthread_join(op.thread, 0);
        op.running = 0;
        op.timer = 0;
        if (op.result == R_OK)
            snprintf(text, sizeof text, tr("OPRA ready: %d presets"), op.presets);
        else
            snprintf(text, sizeof text, "%s",
                     op.result == R_CARD                          ? tr("Cannot write to the card")
                     : op.result == R_DATA                        ? tr("OPRA data not recognised")
                     : op.result == R_NET && get_wifisignal() > 0 ? tr("OPRA download failed")
                     : op.result == R_NET                         ? tr("No Wi-Fi")
                                                                  : "");
        if (*text && op.changed) op.changed(text);
        return 7; /* RET_REMOVE */
    }
    if (op.stage)
        snprintf(text, sizeof text, tr("Indexing OPRA\xe2\x80\xa6 %d presets"), op.presets);
    else
        snprintf(text, sizeof text, tr("Downloading OPRA\xe2\x80\xa6 %u.%u MB"), op.bytes / 1000000,
                 op.bytes / 100000 % 10);
    if (strcmp(text, op.shown)) {
        memcpy(op.shown, text, sizeof text);
        if (op.changed) op.changed(text);
    }
    return 8; /* RET_REPEAT */
}

/* The download started, its progress and end told to changed; 0 if one runs or none can start. */
int opra_update(void (*changed)(const char *)) {
    if (op.running) return 0;
    op.cancel = op.done = op.stage = op.presets = op.result = 0;
    op.bytes = 0;
    op.changed = changed;
    snprintf(op.shown, sizeof op.shown, tr("Downloading OPRA\xe2\x80\xa6 %u.%u MB"), 0u, 0u);
    if (pthread_create(&op.thread, 0, worker, 0)) return 0;
    op.running = 1;
    op.timer = timer_add(tick, 0, 500);
    return 1;
}

/* The EQ page closing: the download cancelled and waited for, the lists freed. */
void opra_close(void) {
    worker_stop(op.thread, &op.running, &op.cancel, &op.timer);
    free(os.hit), free(os.preset);
    os.hit = 0, os.preset = 0;
    os.hits = os.presets = 0;
}
