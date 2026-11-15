/* Eject Card: the card made safe to remove from System settings. */
#include "navigation.h"
#include "playlist_art.h"

#define CARD "/mnt/mmc"
#define CARD_FLAG "/tmp/mmc_add" /* stock's "card in" (check_tfstatus); its mdev script's */
#define EBUSY 16
#define EJECT_TRIES 20 /* EJECT_STEP_MS apart: 2 s for a job that closes its file late */
#define EJECT_STEP_MS 100

/* demo imports neither umount2 nor sync: the o32 syscalls. eject_umount2 answers 0 or -errno. */
__asm__(".globl eject_umount2\neject_umount2:\n.set push\n.set noreorder\nli $v0, 4052\nsyscall\n"
        "bnez $a3, 1f\nnop\njr $ra\nnop\n1:\njr $ra\nsubu $v0, $zero, $v0\n.set pop\n"
        ".globl eject_sync\neject_sync:\nli $v0, 4036\nsyscall\njr $ra\nnop\n");
extern int eject_umount2(const char *path, int flags);
extern void eject_sync(void);

/* The jobs that write to the card or keep its files open, each stopped: the card apps (they run
 * from /tmp: addon.c), Tidal's downloads (tidal.c), Download Album Lyrics' job, playlist art's
 * worker and the song durations' (durations.c). USB Storage (navigation.c ringnav_usbmode) and
 * Eject Card. */
void card_away(void) {
    tidal_card_away();
    addon_card_away();
    album_lyrics_stop();
    playlist_art_stop();
    durations_shutdown(0);
}

/* link names a file on the card: path holds it. */
static int on_card(const char *link, char *path, unsigned size) {
    int n = readlink(link, path, size - 1);
    if (n <= 0) return 0;
    path[n] = 0;
    return !strcmp(path, CARD) || !strncmp(path, CARD "/", sizeof CARD);
}

/* pid's working folder, program or an open file is on the card: path holds it. */
static int holds(int pid, char *path, unsigned size) {
    static const char *const links[] = { "cwd", "exe" };
    char link[48];
    for (unsigned i = 0; i < 2; ++i) {
        snprintf(link, sizeof link, "/proc/%d/%s", pid, links[i]);
        if (on_card(link, path, size)) return 1;
    }
    snprintf(link, sizeof link, "/proc/%d/fd", pid);
    void *fds = opendir(link);
    int found = 0;
    for (struct dirent *e; fds && !found && (e = readdir(fds));) {
        snprintf(link, sizeof link, "/proc/%d/fd/%s", pid, e->d_name);
        found = on_card(link, path, size);
    }
    if (fds) closedir(fds);
    return found;
}

/* What keeps the card busy into out: another program's name, or demo's own file's; "" for none
 * found. ponytail: a file only mapped (/proc/<pid>/maps) is not looked for. */
static void holder(char *out, unsigned size) {
    char path[264];
    void *proc = opendir("/proc");
    out[0] = 0;
    for (struct dirent *e; proc && !out[0] && (e = readdir(proc));) {
        int pid = atoi(e->d_name);
        if (pid <= 0 || !holds(pid, path, sizeof path)) continue;
        if (pid == getpid()) {
            snprintf(out, size, "%s", strrchr(path, '/') + 1);
            continue;
        }
        char comm[32];
        snprintf(comm, sizeof comm, "/proc/%d/comm", pid);
        void *f = fopen(comm, "rb");
        unsigned n = f ? fread(comm, 1, sizeof comm - 1, f) : 0;
        if (f) fclose(f);
        while (n && comm[n - 1] == '\n') --n;
        comm[n] = 0;
        snprintf(out, size, "%s", n ? comm : "another program");
    }
    if (proc) closedir(proc);
}

/* The row's press: playback stopped and every card job, the card synced and unmounted, and stock
 * told it is gone as its mdev script does on a pull (sync, the flag removed, umount): its check
 * (check_cardmount_status) runs now, without its TF Card Removed toast, as the card is still in
 * the slot. A pull, then a card put in, mounts again through stock's mdev script, and the card apps
 * start again (addon_poll). A card still busy after EJECT_TRIES stays mounted, and the toast names
 * what holds it. ponytail: the jobs stay stopped, and playback too, when the card stays. */
static int eject_click(void *ctx, event_t *event) {
    if (!card_mounted()) {
        toast(tr("No card to eject"));
        return 0;
    }
    player_stop();
    radio_stop();
    spot_stop(); /* librespot runs from the card (SPOT_DIR) */
    card_away();
    eject_sync();
    int err = eject_umount2(CARD, 0);
    for (int i = 0; err == -EBUSY && i < EJECT_TRIES; ++i) {
        sleep_ms(EJECT_STEP_MS);
        err = eject_umount2(CARD, 0);
    }
    if (err == -EBUSY) {
        char what[64], text[80];
        holder(what, sizeof what);
        snprintf(text, sizeof text, tr("Card is busy: %s"), what);
        toast(*what ? text : tr("Card is busy"));
        return 0;
    }
    if (err) {
        toast(tr("Could not eject the card"));
        return 0;
    }
    unlink(CARD_FLAG);
    stock_cardmount_trampoline();
    *(volatile char *)CARD_TOAST = 0;
    toast(tr("Safe to remove the card"));
    return 0;
}

/* System settings' Eject Card row (navigation.c ringnav_systemset). */
void eject_row(widget_t *view) {
    widget_set_text_utf8(list_row(view, "system_update", eject_click, 0), tr("Eject Card"));
}
