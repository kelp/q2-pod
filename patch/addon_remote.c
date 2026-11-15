/* Card apps' remote keys (docs/librespot.md, Card apps). While stock's AirPlay receiver plays
 * (airplayGetFlag 2), stock's on_wm_keyup_fun drops Play/Pause (KEY_PLAY) and the side buttons
 * (KEY_BACK_BTN previous, KEY_FWD_BTN next), and a Bluetooth headset's keys reach
 * player_play_pause, which stops AirPlay and starts the local queue. A card app that runs (its
 * loop's pid file /tmp/q2-<id> is there) and whose q2app holds remote=<op> takes them instead,
 * when no other source of Q2 Pod's has them (media_owner 1, no card game with the sound): each is
 * "<op> playpause", "<op> next" or "<op> previous", a request on the app's browse socket, which a
 * worker of its own sends, so the key never waits. Its reply is not waited for: a refusal's message
 * is toasted later, on the UI loop (addon_remote_poll). q2-roon controls a Roon core this way while
 * the core plays to the Q2 over AirPlay: shairport 1.1.1 has no DACP to send the keys back.
 * ponytail: the q2app files are read from the card at every key, as addon_fav_path's are: a key is
 * rare. With two such apps running, the first the card lists takes the keys. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

#define CARD "/mnt/mmc"
#define REMOTE_ID 16        /* an id's bytes, its NUL included: addon.c's APP_ID */
#define REMOTE_OP 16        /* an op's bytes, its NUL included */
#define REMOTE_WAIT_MS 5000 /* a reply's wait, for its message */
#define REMOTE_REPLY 512    /* a reply as far as it is read: its first lines */
#define REMOTE_JOBS 4       /* workers at once; a key past them is dropped */
#define REMOTE_MSG 64

typedef struct {
    char path[48], req[64];
} job_t;

static struct {
    unsigned id;
    volatile int jobs, lock, said; /* said: msg holds a toast for addon_remote_poll */
    char msg[REMOTE_MSG];
} rm __attribute__((section(".scratch")));

/* spotify.c's spot_exchange to the app's socket, within REMOTE_WAIT_MS; a refusal with a message
 * left for addon_remote_poll. Frees its job. */
static void *worker(void *arg) {
    job_t *j = arg;
    char reply[REMOTE_REPLY + 2], *f[4]; /* spot_exchange reads up to max + 1 bytes */
    unsigned n = 0;
    volatile int never = 0; /* a key's request is never cancelled */
    tk_snprintf(reply, sizeof reply, "%s", j->req);
    /* a reply past REMOTE_REPLY fails, but its first bytes hold the header line, all that's read */
    spot_exchange(j->path, REMOTE_WAIT_MS, REMOTE_REPLY, reply, strlen(reply), &n, &never);
    free(j);
    reply[n] = 0;
    /* the header line alone: the rows, if any, matter not */
    char *line = n > 9 ? strstr(reply + 9, "\n") : 0;
    if (line) line[1] = 0, n = (unsigned)(line + 1 - reply);
    if (line && spot_reply(reply, n, f, 0) >= 0 && strcmp(f[1], "ok") && f[2][0] &&
        !__sync_lock_test_and_set(&rm.lock, 1)) {
        tk_snprintf(rm.msg, sizeof rm.msg, "%s", f[2]);
        rm.said = 1;
        __sync_lock_release(&rm.lock);
    }
    __sync_fetch_and_sub(&rm.jobs, 1);
    return 0;
}

/* The first running app whose q2app holds remote=<op>, an op of 1 to REMOTE_OP - 1 bytes with no
 * space, tab or %: its id in id (REMOTE_ID bytes), its op in op; 0 for none. */
static int remote_app(char *id, char *op) {
    char raw[512], path[64], v[48];
    int found = 0;
    void *d = opendir(CARD);
    for (struct dirent *e; !found && d && (e = readdir(d));) {
        const char *s = e->d_name + 1;
        unsigned k = 0;
        while (s[k] &&
               ((s[k] >= 'a' && s[k] <= 'z') || (s[k] >= '0' && s[k] <= '9') || s[k] == '-'))
            ++k;
        if (e->d_name[0] != '.' || !k || s[k] || k >= REMOTE_ID) continue; /* addon.c's valid_id */
        tk_snprintf(path, sizeof path, "/tmp/q2-%s", s);
        if (access(path, 0)) continue;
        tk_snprintf(path, sizeof path, CARD "/.%s/q2app", s);
        if (!state_read(path, raw, sizeof raw)) continue;
        field(raw, "remote", v, sizeof v);
        k = strlen(v);
        if (k && v[k - 1] == '\r') v[--k] = 0; /* a Windows editor's line end */
        unsigned i = 0;                        /* a space, tab or % would need escapes */
        while (i < k && v[i] != ' ' && v[i] != '\t' && v[i] != '%') ++i;
        if (!k || i < k || k >= REMOTE_OP) continue;
        tk_snprintf(id, REMOTE_ID, "%s", s);
        tk_snprintf(op, REMOTE_OP, "%s", v);
        found = 1;
    }
    if (d) closedir(d);
    return found;
}

/* Whether the keys are an app's now: AirPlay plays, no other source has them, and an app asks. */
static int remote_on(char *id, char *op) {
    return airplayGetFlag() == 2 && media_owner() == 1 && !addon_game_audio() && remote_app(id, op);
}
int addon_remote_on(void) {
    char id[REMOTE_ID], op[REMOTE_OP];
    return remote_on(id, op);
}

/* key (KEY_PLAY, KEY_FWD_BTN or KEY_BACK_BTN) sent to the app that takes the keys now; 1 when it
 * took it (its worker started), 0 when the key is the caller's as before. */
int addon_remote_key(unsigned key) {
    const char *word = key == KEY_PLAY       ? "playpause"
                       : key == KEY_FWD_BTN  ? "next"
                       : key == KEY_BACK_BTN ? "previous"
                                             : 0;
    char id[REMOTE_ID], op[REMOTE_OP];
    job_t *j;
    unsigned long thread;
    if (!word || !remote_on(id, op)) return 0;
    if (__sync_fetch_and_add(&rm.jobs, 1) >= REMOTE_JOBS || !(j = calloc(1, sizeof *j))) {
        __sync_fetch_and_sub(&rm.jobs, 1);
        return 1; /* the app's still: dropped, never stock's */
    }
    tk_snprintf(j->path, sizeof j->path, "/tmp/q2-%s.browse.sock", id);
    tk_snprintf(j->req, sizeof j->req, "Q2SPOT/1\n%u\t%s\t%s\n", ++rm.id, op, word);
    if (pthread_create(&thread, 0, worker, j)) {
        free(j);
        __sync_fetch_and_sub(&rm.jobs, 1);
        return 0;
    }
    pthread_detach(thread);
    return 1;
}

/* addon_poll's: a refusal's message, toasted on the UI loop. */
void addon_remote_poll(void) {
    char msg[REMOTE_MSG];
    if (!rm.said || __sync_lock_test_and_set(&rm.lock, 1)) return;
    tk_snprintf(msg, sizeof msg, "%s", rm.msg);
    rm.said = 0;
    __sync_lock_release(&rm.lock);
    toast(msg);
}
