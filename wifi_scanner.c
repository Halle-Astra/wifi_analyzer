/*
 * wifi_scanner.c — Linux nl80211 WiFi scanner
 *
 * Communicates directly with the kernel via generic netlink (nl80211) to:
 *   1. Trigger a WiFi scan (requires CAP_NET_ADMIN / root)
 *   2. Dump scan results including hidden/anonymous BSS entries
 *
 * Outputs JSON identical to the macOS wifi_scanner.swift format:
 * {
 *   "timestamp": "2025-04-16 17:00:00",
 *   "networks": [ { "ssid", "bssid", "rssi", "noise", "channel",
 *                    "channelBand", "channelWidth", "hidden", "beaconInterval" }, ... ],
 *   "totalCount": N,
 *   "hiddenCount": N
 * }
 *
 * Build:  gcc -O2 -o wifi_scanner wifi_scanner.c
 * No external library dependencies (uses raw netlink sockets).
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>
#include <net/if.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <linux/genetlink.h>
#include <linux/nl80211.h>

#define BUF_SIZE 65536
#define MAX_NETWORKS 256

/* Band constants matching macOS wifi_scanner.swift */
#define BAND_UNKNOWN 0
#define BAND_2GHZ    1
#define BAND_5GHZ    2
#define BAND_6GHZ    3

/* Channel width constants matching macOS */
#define WIDTH_20MHZ  1
#define WIDTH_40MHZ  2
#define WIDTH_80MHZ  3
#define WIDTH_160MHZ 4

struct network_info {
    char ssid[64];
    unsigned char bssid[6];
    int rssi;       /* dBm (signal / 100) */
    int noise;
    int channel;
    int freq;
    int band;       /* BAND_* */
    int width;      /* WIDTH_* */
    int hidden;
    int beacon_interval;
};

static struct network_info networks[MAX_NETWORKS];
static int net_count = 0;

/* ------------------------------------------------------------------ */
/* Netlink helpers (no libnl dependency)                               */
/* ------------------------------------------------------------------ */

static int nl_sock = -1;
static int nl80211_id = -1;
static __u32 nl_seq = 0;
static __u32 nl_pid;

struct nl_msg {
    struct nlmsghdr nlh;
    char payload[4096];
};

static int nl_open(void)
{
    struct sockaddr_nl addr = { .nl_family = AF_NETLINK };
    nl_sock = socket(AF_NETLINK, SOCK_RAW, NETLINK_GENERIC);
    if (nl_sock < 0) return -1;
    if (bind(nl_sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) return -1;
    socklen_t len = sizeof(addr);
    getsockname(nl_sock, (struct sockaddr *)&addr, &len);
    nl_pid = addr.nl_pid;
    return 0;
}

static void nl_close(void) { if (nl_sock >= 0) close(nl_sock); }

static void *nla_data(struct nlattr *nla) { return (char *)nla + NLA_HDRLEN; }
static int nla_len(struct nlattr *nla) { return nla->nla_len - NLA_HDRLEN; }

#define NLA_NEXT(nla) ((struct nlattr *)((char *)(nla) + NLA_ALIGN((nla)->nla_len)))
#define NLA_OK(nla, rem) ((rem) >= (int)sizeof(struct nlattr) && \
    (nla)->nla_len >= sizeof(struct nlattr) && (nla)->nla_len <= (rem))

static struct nlattr *nla_put(void *buf, int *offset, int type, const void *data, int len)
{
    struct nlattr *nla = (struct nlattr *)((char *)buf + *offset);
    nla->nla_type = type;
    nla->nla_len = NLA_HDRLEN + len;
    if (data && len > 0) memcpy(nla_data(nla), data, len);
    *offset += NLA_ALIGN(nla->nla_len);
    return nla;
}

static int nl_send(struct nl_msg *msg)
{
    struct sockaddr_nl addr = { .nl_family = AF_NETLINK };
    msg->nlh.nlmsg_seq = ++nl_seq;
    msg->nlh.nlmsg_pid = nl_pid;
    return sendto(nl_sock, &msg->nlh, msg->nlh.nlmsg_len, 0,
                  (struct sockaddr *)&addr, sizeof(addr));
}

static int nl_recv(char *buf, int bufsz)
{
    struct sockaddr_nl addr;
    struct iovec iov = { .iov_base = buf, .iov_len = bufsz };
    struct msghdr mh = { .msg_name = &addr, .msg_namelen = sizeof(addr),
                         .msg_iov = &iov, .msg_iovlen = 1 };
    return recvmsg(nl_sock, &mh, 0);
}

/* Resolve generic netlink family id */
static int resolve_nl80211(void)
{
    struct nl_msg msg;
    memset(&msg, 0, sizeof(msg));
    msg.nlh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
    msg.nlh.nlmsg_type = GENL_ID_CTRL;
    msg.nlh.nlmsg_flags = NLM_F_REQUEST;

    struct genlmsghdr *ghdr = NLMSG_DATA(&msg.nlh);
    ghdr->cmd = CTRL_CMD_GETFAMILY;
    ghdr->version = 1;

    int off = NLMSG_ALIGN(msg.nlh.nlmsg_len) - (int)((char *)&msg.nlh - (char *)&msg);
    /* Actually recalculate offset from start of payload after genlmsghdr */
    off = GENL_HDRLEN;
    const char *name = "nl80211";
    nla_put(msg.payload, &off, CTRL_ATTR_FAMILY_NAME, name, strlen(name) + 1);
    msg.nlh.nlmsg_len = NLMSG_LENGTH(off);

    if (nl_send(&msg) < 0) return -1;

    char buf[BUF_SIZE];
    int len = nl_recv(buf, sizeof(buf));
    if (len < 0) return -1;

    struct nlmsghdr *nlh = (struct nlmsghdr *)buf;
    if (!NLMSG_OK(nlh, len) || nlh->nlmsg_type == NLMSG_ERROR) return -1;

    struct genlmsghdr *rghdr = NLMSG_DATA(nlh);
    int attrlen = nlh->nlmsg_len - NLMSG_HDRLEN - GENL_HDRLEN;
    struct nlattr *nla = (struct nlattr *)((char *)rghdr + GENL_HDRLEN);
    int rem = attrlen;
    while (NLA_OK(nla, rem)) {
        if (nla->nla_type == CTRL_ATTR_FAMILY_ID) {
            nl80211_id = *(__u16 *)nla_data(nla);
            return 0;
        }
        int step = NLA_ALIGN(nla->nla_len);
        nla = (struct nlattr *)((char *)nla + step);
        rem -= step;
    }
    return -1;
}

/* ------------------------------------------------------------------ */
/* Scan trigger + dump                                                */
/* ------------------------------------------------------------------ */

static int freq_to_channel(int freq)
{
    if (freq >= 2412 && freq <= 2484) {
        if (freq == 2484) return 14;
        return (freq - 2407) / 5;
    }
    if (freq >= 5170 && freq <= 5825) return (freq - 5000) / 5;
    if (freq >= 5955 && freq <= 7115) return (freq - 5950) / 5;
    return 0;
}

static int freq_to_band(int freq)
{
    if (freq < 3000) return BAND_2GHZ;
    if (freq < 6000) return BAND_5GHZ;
    return BAND_6GHZ;
}

/* All 2.4GHz + 5GHz frequencies to ensure full channel coverage.
 * Default kernel scan may skip DFS channels or weak signals. */
static const __u32 all_freqs[] = {
    /* 2.4 GHz: ch 1-14 */
    2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472, 2484,
    /* 5 GHz UNII-1: ch 36-48 */
    5180, 5200, 5220, 5240,
    /* 5 GHz UNII-2: ch 52-64 (DFS) */
    5260, 5280, 5300, 5320,
    /* 5 GHz UNII-2 Extended: ch 100-144 (DFS) */
    5500, 5520, 5540, 5560, 5580, 5600, 5620, 5640, 5660, 5680, 5700, 5720,
    /* 5 GHz UNII-3: ch 149-165 */
    5745, 5765, 5785, 5805, 5825,
};
#define NUM_FREQS (sizeof(all_freqs) / sizeof(all_freqs[0]))

static int trigger_scan(int ifindex)
{
    /* Use a larger message buffer for the frequency list */
    struct {
        struct nlmsghdr nlh;
        char payload[8192];
    } msg;
    memset(&msg, 0, sizeof(msg));
    msg.nlh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
    msg.nlh.nlmsg_type = nl80211_id;
    msg.nlh.nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;

    struct genlmsghdr *ghdr = NLMSG_DATA(&msg.nlh);
    ghdr->cmd = NL80211_CMD_TRIGGER_SCAN;
    ghdr->version = 0;

    int off = GENL_HDRLEN;
    __u32 idx = ifindex;
    nla_put(msg.payload, &off, NL80211_ATTR_IFINDEX, &idx, 4);

    /* Scan flags: flush cache for fresh results */
    __u32 flags = (1 << 1); /* NL80211_SCAN_FLAG_FLUSH */
    nla_put(msg.payload, &off, NL80211_ATTR_SCAN_FLAGS, &flags, 4);

    /* Specify all frequencies for full channel coverage */
    struct nlattr *freq_nest = nla_put(msg.payload, &off,
        NL80211_ATTR_SCAN_FREQUENCIES | NLA_F_NESTED, NULL, 0);
    int nest_start = off;
    for (unsigned i = 0; i < NUM_FREQS; i++) {
        nla_put(msg.payload, &off, i + 1, &all_freqs[i], 4);
    }
    freq_nest->nla_len = NLA_HDRLEN + (off - nest_start);

    msg.nlh.nlmsg_len = NLMSG_LENGTH(off);

    if (nl_send((struct nl_msg *)&msg) < 0) return -1;
    char buf[BUF_SIZE];
    int attempts = 0;
    while (attempts++ < 50) {  /* up to ~10 seconds */
        struct timeval tv = { .tv_sec = 0, .tv_usec = 200000 };
        fd_set fds;
        FD_ZERO(&fds);
        FD_SET(nl_sock, &fds);
        int ret = select(nl_sock + 1, &fds, NULL, NULL, &tv);
        if (ret <= 0) continue;

        int len = nl_recv(buf, sizeof(buf));
        if (len < 0) continue;

        struct nlmsghdr *nlh;
        for (nlh = (struct nlmsghdr *)buf; NLMSG_OK(nlh, len);
             nlh = NLMSG_NEXT(nlh, len)) {
            if (nlh->nlmsg_type == NLMSG_ERROR) {
                struct nlmsgerr *err = NLMSG_DATA(nlh);
                if (err->error == 0) continue; /* ACK */
                if (err->error == -EBUSY) {
                    /* Scan already in progress, wait */
                    usleep(500000);
                    continue;
                }
                return err->error;
            }
            if (nlh->nlmsg_type == nl80211_id) {
                struct genlmsghdr *g = NLMSG_DATA(nlh);
                if (g->cmd == NL80211_CMD_NEW_SCAN_RESULTS)
                    return 0;  /* scan done */
            }
        }
    }
    return 0; /* timeout, try dump anyway */
}

/* Parse Information Elements to extract HT/VHT/HE channel width */
static int parse_ie_width(const unsigned char *ie, int ie_len)
{
    int width = WIDTH_20MHZ;
    int pos = 0;
    while (pos + 2 <= ie_len) {
        int eid = ie[pos];
        int elen = ie[pos + 1];
        if (pos + 2 + elen > ie_len) break;
        const unsigned char *data = &ie[pos + 2];

        if (eid == 61 && elen >= 1) { /* HT Operation */
            int sec_offset = data[1] & 0x03;
            if (sec_offset == 1 || sec_offset == 3)
                width = WIDTH_40MHZ;
        }
        if (eid == 192 && elen >= 1) { /* VHT Operation */
            int vht_width = data[0];
            if (vht_width == 1) width = WIDTH_80MHZ;
            else if (vht_width == 2 || vht_width == 3) width = WIDTH_160MHZ;
        }
        /* HE Operation (Element ID Extension 36) */
        if (eid == 255 && elen >= 2 && data[0] == 36) {
            if (elen >= 7) {
                int he_width = data[1];
                if (he_width & 0x04) width = WIDTH_80MHZ;
                if (he_width & 0x08) width = WIDTH_160MHZ;
            }
        }
        pos += 2 + elen;
    }
    return width;
}

static void parse_bss(struct nlattr *bss_attr)
{
    if (net_count >= MAX_NETWORKS) return;

    struct network_info *net = &networks[net_count];
    memset(net, 0, sizeof(*net));
    net->rssi = -100;
    net->hidden = 1;  /* assume hidden until we find SSID */

    int rem = nla_len(bss_attr);
    struct nlattr *nla = nla_data(bss_attr);
    const unsigned char *ies = NULL;
    int ies_len = 0;

    while (NLA_OK(nla, rem)) {
        switch (nla->nla_type) {
        case NL80211_BSS_BSSID:
            if (nla_len(nla) == 6)
                memcpy(net->bssid, nla_data(nla), 6);
            break;
        case NL80211_BSS_FREQUENCY:
            net->freq = *(__u32 *)nla_data(nla);
            net->channel = freq_to_channel(net->freq);
            net->band = freq_to_band(net->freq);
            break;
        case NL80211_BSS_SIGNAL_MBM:
            net->rssi = (__s32)(*(int *)nla_data(nla)) / 100;
            break;
        case NL80211_BSS_SIGNAL_UNSPEC:
            if (net->rssi == -100) {
                int pct = *(__u8 *)nla_data(nla);
                net->rssi = pct / 2 - 100;
            }
            break;
        case NL80211_BSS_BEACON_INTERVAL:
            net->beacon_interval = *(__u16 *)nla_data(nla);
            break;
        case NL80211_BSS_INFORMATION_ELEMENTS:
        case NL80211_BSS_BEACON_IES: {
            const unsigned char *ie = nla_data(nla);
            int ielen = nla_len(nla);
            /* Parse SSID from IE tag 0 */
            int p = 0;
            while (p + 2 <= ielen) {
                int tag = ie[p], tlen = ie[p + 1];
                if (p + 2 + tlen > ielen) break;
                if (tag == 0) { /* SSID */
                    if (tlen > 0 && tlen < (int)sizeof(net->ssid)) {
                        /* Check if all zeros (hidden) */
                        int all_zero = 1;
                        for (int i = 0; i < tlen; i++)
                            if (ie[p + 2 + i] != 0) { all_zero = 0; break; }
                        if (!all_zero) {
                            memcpy(net->ssid, &ie[p + 2], tlen);
                            net->ssid[tlen] = '\0';
                            net->hidden = 0;
                        }
                    }
                }
                p += 2 + tlen;
            }
            if (nla->nla_type == NL80211_BSS_INFORMATION_ELEMENTS ||
                ies == NULL) {
                ies = ie;
                ies_len = ielen;
            }
            break;
        }
        }
        int step = NLA_ALIGN(nla->nla_len);
        nla = (struct nlattr *)((char *)nla + step);
        rem -= step;
    }

    if (ies && ies_len > 0)
        net->width = parse_ie_width(ies, ies_len);
    if (net->width == 0)
        net->width = WIDTH_20MHZ;

    net_count++;
}

static int dump_scan(int ifindex)
{
    struct nl_msg msg;
    memset(&msg, 0, sizeof(msg));
    msg.nlh.nlmsg_len = NLMSG_LENGTH(GENL_HDRLEN);
    msg.nlh.nlmsg_type = nl80211_id;
    msg.nlh.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;

    struct genlmsghdr *ghdr = NLMSG_DATA(&msg.nlh);
    ghdr->cmd = NL80211_CMD_GET_SCAN;
    ghdr->version = 0;

    int off = GENL_HDRLEN;
    __u32 idx = ifindex;
    nla_put(msg.payload, &off, NL80211_ATTR_IFINDEX, &idx, 4);
    msg.nlh.nlmsg_len = NLMSG_LENGTH(off);

    if (nl_send(&msg) < 0) return -1;

    char buf[BUF_SIZE];
    int done = 0;
    while (!done) {
        int len = nl_recv(buf, sizeof(buf));
        if (len < 0) return -1;

        struct nlmsghdr *nlh;
        for (nlh = (struct nlmsghdr *)buf; NLMSG_OK(nlh, len);
             nlh = NLMSG_NEXT(nlh, len)) {
            if (nlh->nlmsg_type == NLMSG_DONE) { done = 1; break; }
            if (nlh->nlmsg_type == NLMSG_ERROR) { done = 1; break; }
            if (nlh->nlmsg_type != nl80211_id) continue;

            struct genlmsghdr *g = NLMSG_DATA(nlh);
            int attrlen = nlh->nlmsg_len - NLMSG_HDRLEN - GENL_HDRLEN;
            struct nlattr *nla = (struct nlattr *)((char *)g + GENL_HDRLEN);
            int rem = attrlen;
            while (NLA_OK(nla, rem)) {
                if (nla->nla_type == NL80211_ATTR_BSS)
                    parse_bss(nla);
                int step = NLA_ALIGN(nla->nla_len);
                nla = (struct nlattr *)((char *)nla + step);
                rem -= step;
            }
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* JSON output                                                        */
/* ------------------------------------------------------------------ */

static void json_escape(const char *s, char *out, int outlen)
{
    int j = 0;
    for (int i = 0; s[i] && j < outlen - 2; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') { out[j++] = '\\'; out[j++] = c; }
        else if (c < 0x20) {
            j += snprintf(out + j, outlen - j, "\\u%04x", c);
        } else {
            out[j++] = c;
        }
    }
    out[j] = '\0';
}

static void print_json(void)
{
    time_t now = time(NULL);
    struct tm *t = localtime(&now);
    char ts[32];
    strftime(ts, sizeof(ts), "%Y-%m-%d %H:%M:%S", t);

    int hidden_count = 0;
    for (int i = 0; i < net_count; i++)
        if (networks[i].hidden) hidden_count++;

    printf("{\n");
    printf("  \"timestamp\": \"%s\",\n", ts);
    printf("  \"networks\": [\n");
    for (int i = 0; i < net_count; i++) {
        struct network_info *n = &networks[i];
        char ssid_esc[256];
        json_escape(n->ssid, ssid_esc, sizeof(ssid_esc));
        printf("    {\"ssid\": \"%s\", \"bssid\": \"%02x:%02x:%02x:%02x:%02x:%02x\", "
               "\"rssi\": %d, \"noise\": %d, \"channel\": %d, "
               "\"channelBand\": %d, \"channelWidth\": %d, "
               "\"hidden\": %s, \"beaconInterval\": %d}%s\n",
               ssid_esc,
               n->bssid[0], n->bssid[1], n->bssid[2],
               n->bssid[3], n->bssid[4], n->bssid[5],
               n->rssi, n->noise, n->channel,
               n->band, n->width,
               n->hidden ? "true" : "false",
               n->beacon_interval,
               (i < net_count - 1) ? "," : "");
    }
    printf("  ],\n");
    printf("  \"totalCount\": %d,\n", net_count);
    printf("  \"hiddenCount\": %d\n", hidden_count);
    printf("}\n");
}

/* ------------------------------------------------------------------ */
/* Main                                                               */
/* ------------------------------------------------------------------ */

static void usage(const char *prog)
{
    fprintf(stderr, "Usage: %s [-i <interface>] [-n]\n", prog);
    fprintf(stderr, "  -i <iface>   WiFi interface (default: auto-detect)\n");
    fprintf(stderr, "  -n           No trigger, only dump cached results\n");
    exit(1);
}

static int find_wifi_ifindex(char *ifname_out, int buflen)
{
    /* Try common names */
    const char *candidates[] = {"wlan0", "wlp2s0", "wlp3s0", "wlp0s20f3", NULL};
    for (int i = 0; candidates[i]; i++) {
        int idx = if_nametoindex(candidates[i]);
        if (idx > 0) {
            snprintf(ifname_out, buflen, "%s", candidates[i]);
            return idx;
        }
    }
    /* Scan /sys/class/net for wireless */
    FILE *fp = popen("ls /sys/class/net/*/wireless 2>/dev/null | head -1", "r");
    if (fp) {
        char path[256];
        if (fgets(path, sizeof(path), fp)) {
            /* path = /sys/class/net/<name>/wireless */
            char *p = strstr(path, "/sys/class/net/");
            if (p) {
                p += strlen("/sys/class/net/");
                char *end = strchr(p, '/');
                if (end) {
                    *end = '\0';
                    snprintf(ifname_out, buflen, "%s", p);
                    pclose(fp);
                    return if_nametoindex(p);
                }
            }
        }
        pclose(fp);
    }
    return 0;
}

int main(int argc, char *argv[])
{
    char ifname[64] = "";
    int no_trigger = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-i") == 0 && i + 1 < argc)
            snprintf(ifname, sizeof(ifname), "%s", argv[++i]);
        else if (strcmp(argv[i], "-n") == 0)
            no_trigger = 1;
        else
            usage(argv[0]);
    }

    int ifindex;
    if (ifname[0]) {
        ifindex = if_nametoindex(ifname);
    } else {
        ifindex = find_wifi_ifindex(ifname, sizeof(ifname));
    }
    if (ifindex == 0) {
        fprintf(stderr, "{\"error\": \"No WiFi interface found\"}\n");
        return 1;
    }

    if (nl_open() < 0) {
        fprintf(stderr, "{\"error\": \"Failed to open netlink socket\"}\n");
        return 1;
    }

    if (resolve_nl80211() < 0) {
        fprintf(stderr, "{\"error\": \"Failed to resolve nl80211\"}\n");
        nl_close();
        return 1;
    }

    if (!no_trigger) {
        int ret = trigger_scan(ifindex);
        if (ret < 0 && ret != -EBUSY) {
            /* If trigger fails (e.g. no permission), still try dump */
            fprintf(stderr, "Warning: scan trigger failed (%d), dumping cache\n", ret);
        }
    }

    if (dump_scan(ifindex) < 0) {
        fprintf(stderr, "{\"error\": \"Failed to dump scan results\"}\n");
        nl_close();
        return 1;
    }

    nl_close();
    print_json();
    return 0;
}
