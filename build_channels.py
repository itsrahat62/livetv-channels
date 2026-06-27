#!/usr/bin/env python3
"""
Build channels.json for the Live TV app from free, public IPTV sources.

Sources:
  - iptv-org API (streams + channel metadata + logos)  -> https://iptv-org.github.io
  - Shadmanislam/bdiptv  (BD IPTV.m3u)
  - imShakil/tvlink      (iptv.m3u8, all.m3u)

Channels with the SAME name are grouped into one channel with multiple servers
(SportzX-style multi-stream). Clearly-dead URLs (DNS failure / 404 / refused)
are dropped; 403/timeouts are KEPT (they're usually geo-locked, not dead).

Run by a scheduled GitHub Action; the app fetches the resulting channels.json.
"""
import json, re, sys, time, urllib.request, urllib.error, socket, concurrent.futures, ssl

sys.stdout.reconfigure(encoding="utf-8")
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile"

IPTV_STREAMS  = "https://iptv-org.github.io/api/streams.json"
IPTV_CHANNELS = "https://iptv-org.github.io/api/channels.json"
IPTV_LOGOS    = "https://iptv-org.github.io/api/logos.json"
# iptv-org's API already aggregates ~16k streams from 200+ countries (the world).
# These add even more. Add any public M3U URL here — dead ones are skipped safely.
EXTRA_M3U = [
    # Worldwide aggregators
    "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8",
    # Bangladesh / sub-continent
    "https://raw.githubusercontent.com/Shadmanislam/bdiptv/master/BD%20IPTV.m3u",
    "https://raw.githubusercontent.com/imShakil/tvlink/main/iptv.m3u8",
    "https://raw.githubusercontent.com/imShakil/tvlink/main/all.m3u",
    "https://raw.githubusercontent.com/johirxofficial/otv-auto-updated-playlist/main/otv.m3u",
]

# Hand-curated channels (verified WC / sports feeds). force=True keeps the server
# even if the runner can't reach it (geo-blocked); the rest are auto-tested and
# dropped automatically when they go dead/expire — so the list self-heals.
MANUAL_CHANNELS = [
    # — Stable WC live match feeds (verified) —
    {"name": "WC Live English",  "cat": "Sports", "url": "https://pub-f2987c4fc9d2450191dfee2ee8dc9f51.r2.dev/en/index.m3u8", "force": True},
    {"name": "WC Live Espanol",  "cat": "Sports", "url": "https://pub-f2987c4fc9d2450191dfee2ee8dc9f51.r2.dev/sp/index.m3u8", "force": True},
    {"name": "WC Live HD",       "cat": "Sports", "url": "https://1nyaler.streamhostingcdn.top/stream/106/index.m3u8", "force": True},
    # — Verified free sports (stable, no token) —
    {"name": "beIN Sports XTRA", "cat": "Sports", "url": "https://bein-xtra-bein.amagi.tv/playlist.m3u8", "force": True},
    {"name": "Arryadia",         "cat": "Sports", "url": "https://stream-lb.livemediama.com/arryadia/hls/master.m3u8", "force": True},
    {"name": "Caze TV",          "cat": "Sports", "url": "https://dfr80qz435crc.cloudfront.net/MNOP/Amagi/Caze/Caze_TV_BR/1080p-vtt/index.m3u8", "force": True},
    {"name": "TYC Sports",       "cat": "Sports", "url": "https://amg26268-amg26268c14-freelivesports-emea-10267.playouts.now.amagi.tv/ts-us-e2-n2/playlist/amg26268-sportsstudio-tycsports-freelivesportsemea/playlist.m3u8", "force": True},
    {"name": "DD Sports",        "cat": "Sports", "url": "https://d3qs3d2rkhfqrt.cloudfront.net/out/v1/b17adfe543354fdd8d189b110617cddd/index.m3u8", "force": True},
    {"name": "ESPN8 The Ocho",   "cat": "Sports", "url": "https://d3b6q2ou5kp8ke.cloudfront.net/ESPNTheOcho.m3u8", "force": True},
    # — WC-specific sources to auto-test (kept only when the runner verifies them) —
    {"name": "Fancode BD",       "cat": "Sports", "url": "https://bd-mc-fblive.fancode.com/mumbai/142970_english_hls_7371b641c729339_1ta-di_h264/1080p.m3u8", "referer": "https://fancode.com/"},
    {"name": "Fancode India",    "cat": "Sports", "url": "https://in-mc-fblive.fancode.com/mumbai/142970_english_hls_7371b641c729339_1ta-di_h264/1080p.m3u8", "referer": "https://fancode.com/"},
    {"name": "Tapmad Sports",    "cat": "Sports", "url": "https://serieAleague.akamaized.net/hls/live/2107107/PSLE_tapmad2026-Backup/master.m3u8"},
    {"name": "Match TV",         "cat": "Sports", "url": "https://bl.video.matchtv.ru/media/playlist/free_d46d0cf1712c0542ec7fd4f0808f600a_hd/17_89756005/1080/e6bef86de8a133cd7b27deb040758a00/4796141934.m3u8"},
    {"name": "RTBF Sport",       "cat": "Sports", "url": "https://d1211whpimeups.cloudfront.net/smil:rtbgo/chunklist_b2196000_sleng.m3u8"},
    {"name": "WC Stream 23",     "cat": "Sports", "url": "https://1nyaler.streamhostingcdn.top/stream/23/index.m3u8"},
    {"name": "WC Stream 30",     "cat": "Sports", "url": "https://1nyaler.streamhostingcdn.top/stream/30/index.m3u8"},
]

# name -> logo URL, built from iptv-org, used to back-fill channels with no logo
NAME_LOGO = {}

# Block adult / NSFW content
ADULT = re.compile(r"xxx|adult|porn|18\s*\+|\bsex\b|nsfw|brazzers|playboy|hustler|erotic|nude", re.I)
# Clean category set the app shows; anything else collapses to General
CLEAN_CATS = {"Sports", "News", "Entertainment", "Movies", "Music", "Kids",
              "Documentary", "Bangla", "Religious", "Business", "General"}


def clean_cat(cat):
    if cat in CLEAN_CATS:
        return cat
    g = (cat or "").lower()
    if "sport" in g: return "Sports"
    if "news" in g: return "News"
    if "movie" in g or "cinema" in g or "film" in g: return "Movies"
    if "music" in g or "song" in g: return "Music"
    if "kid" in g or "cartoon" in g or "child" in g: return "Kids"
    if "doc" in g: return "Documentary"
    if "bangla" in g or "bd" in g or "desh" in g: return "Bangla"
    if "relig" in g or "islam" in g or "quran" in g or "christ" in g: return "Religious"
    if "movie" in g or "series" in g or "drama" in g or "show" in g: return "Movies"
    return "General"

TEST = "--no-test" not in sys.argv
TEST_BUDGET_SEC = 540          # stop testing after this; untested links are kept
MAX_SERVERS = 8                # cap servers per channel

# iptv-org category -> app tab label
CAT_MAP = {
    "sports": "Sports", "news": "News",
    "movies": "Movies", "series": "Movies",
    "music": "Music",
    "kids": "Kids", "family": "Kids", "animation": "Kids",
    "documentary": "Documentary", "science": "Documentary", "education": "Documentary",
    "entertainment": "Entertainment", "comedy": "Entertainment", "general": "Entertainment",
    "lifestyle": "Entertainment", "culture": "Entertainment", "cooking": "Entertainment",
    "religious": "Religious", "business": "Business",
}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        return json.load(r)


def fetch_text(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        print("  ! failed", url, e)
        return ""


def norm(name):
    s = (name or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"\b(hd|fhd|uhd|sd|4k|1080p?|720p?|480p?|backup|live|tv|channel)\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def host(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1) if m else ""


def map_cat(cats):
    for c in (cats or []):
        if c in CAT_MAP:
            return CAT_MAP[c]
    return "General"


def best_quality_label(url, quality):
    if quality:
        return quality
    u = url.lower()
    for q in ("2160", "1080", "720", "480", "360"):
        if q in u:
            return q + "p"
    return "Auto"


def add_server(groups, key, name, cat, logo, sname, url, referer, ua, typ=None, kkey=None, force=False):
    if ADULT.search(name or "") or ADULT.search(cat or "") or ADULT.search(sname or ""):
        return
    cat = clean_cat(cat)
    g = groups.setdefault(key, {"name": name, "cat": cat, "logo": logo, "servers": [], "urls": set()})
    if not g["logo"] and logo:
        g["logo"] = logo
    if url in g["urls"]:
        return
    g["urls"].add(url)
    t = typ or ("dash" if ".mpd" in url.lower() else "mp4" if ".mp4" in url.lower() else "hls")
    g["servers"].append({"name": sname, "url": url, "referer": referer or "",
                         "ua": ua or "", "type": t, "key": kkey or "", "_force": force})


def from_manual(groups):
    n = 0
    for c in MANUAL_CHANNELS:
        url = (c.get("url") or "").strip()
        if not url:
            continue
        add_server(groups, norm(c["name"]), c["name"], c.get("cat", "Sports"),
                   c.get("logo", ""), host(url) or "Server", url,
                   c.get("referer"), c.get("ua"), typ=c.get("type"),
                   kkey=c.get("key"), force=c.get("force", False))
        n += 1
    print(f"  manual: +{n}")


def from_iptv_org(groups):
    print("Fetching iptv-org ...")
    streams = fetch_json(IPTV_STREAMS)
    channels = {c["id"]: c for c in fetch_json(IPTV_CHANNELS)}
    logos = {}
    try:
        for l in fetch_json(IPTV_LOGOS):
            cid = l.get("channel")
            if cid and l.get("url") and cid not in logos:
                logos[cid] = l["url"]
    except Exception:
        pass
    # name -> logo index (for back-filling logos by channel name everywhere)
    for cid, url in logos.items():
        meta = channels.get(cid)
        if not meta:
            continue
        NAME_LOGO.setdefault(norm(meta["name"]), url)
        for alt in (meta.get("alt_names") or []):
            NAME_LOGO.setdefault(norm(alt), url)
    print(f"  streams={len(streams)} channels={len(channels)} logos={len(logos)}")
    n = 0
    for s in streams:
        url = (s.get("url") or "").strip()
        if not url or not url.startswith("http"):
            continue
        cid = s.get("channel")
        meta = channels.get(cid) if cid else None
        if meta and meta.get("is_nsfw"):
            continue
        if meta:
            name = meta["name"]
            cat = map_cat(meta.get("categories"))
            logo = logos.get(cid)
        else:
            name = (s.get("title") or "").strip() or host(url)
            cat = "General"
            logo = None
        key = cid or norm(name)
        if not key:
            continue
        sname = best_quality_label(url, s.get("quality")) + " · " + host(url)
        add_server(groups, key, name, cat, logo, sname, url,
                   s.get("referrer"), s.get("user_agent"))
        n += 1
    print(f"  added {n} streams")


M3U_NAME = re.compile(r'#EXTINF[^,]*,(.*)')
M3U_LOGO = re.compile(r'tvg-logo="([^"]*)"')
M3U_GROUP = re.compile(r'group-title="([^"]*)"')
VLCOPT_REF = re.compile(r'#EXTVLCOPT:http-referrer=(.*)', re.I)
VLCOPT_UA = re.compile(r'#EXTVLCOPT:http-user-agent=(.*)', re.I)


def from_m3u(groups, text, src):
    name = logo = group = referer = ua = None
    added = 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            m = M3U_NAME.search(line); name = m.group(1).strip() if m else None
            m = M3U_LOGO.search(line); logo = m.group(1) if m else None
            m = M3U_GROUP.search(line); group = m.group(1) if m else None
            referer = ua = None
        elif line.startswith("#EXTVLCOPT"):
            m = VLCOPT_REF.search(line)
            if m: referer = m.group(1).strip()
            m = VLCOPT_UA.search(line)
            if m: ua = m.group(1).strip()
        elif line and not line.startswith("#"):
            if name:
                key = norm(name)
                if key:
                    # clean_cat() (in add_server) maps the group to a clean category
                    add_server(groups, key, name, group or "General", logo,
                               host(line) or "Server", line, referer, ua)
                    added += 1
            name = logo = group = referer = ua = None
    print(f"  {src}: +{added}")


def from_198(groups):
    """The 198.195.239.50 IPTV server (JSON with name/url/logo/category)."""
    base = "http://198.195.239.50/"
    try:
        data = fetch_json(base + "tv_channels.json")
    except Exception as e:
        print("  198 server failed:", e)
        return
    n = 0
    for c in (data.get("channels") or []):
        if c.get("status") == "hidden":
            continue
        url = (c.get("url") or "").strip()
        name = (c.get("name") or "").strip()
        if not url or not name:
            continue
        logo = c.get("logo") or ""
        if logo and not logo.startswith("http"):
            logo = base + logo.replace(" ", "%20")
        cat = (c.get("category") or "Bangla").strip()
        add_server(groups, norm(name), name, cat, logo, host(url), url, None, None)
        n += 1
    print(f"  198 server: +{n}")


def check(item):
    """Strict: a server is kept ONLY if it returns 200 with a valid HLS/DASH manifest."""
    key, idx, url, referer = item
    try:
        headers = {"User-Agent": UA}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        r = urllib.request.urlopen(req, timeout=7, context=CTX)
        if getattr(r, "status", 200) != 200:
            return key, idx, False
        body = r.read(400).decode("latin1", "ignore")
        up = body.upper()
        ok = ("#EXTM3U" in up) or ("#EXT-X" in up) or ("<MPD" in up) or (".TS" in up) \
            or body.lstrip().startswith("#")
        return key, idx, ok
    except Exception:
        return key, idx, False     # anything not clearly working is dropped


def keep_working(groups):
    """Keep only verified-working servers, so the app never shows a dead one."""
    if not TEST:
        return
    items = [(k, i, s["url"], s.get("referer")) for k, g in groups.items()
             for i, s in enumerate(g["servers"])]
    print(f"Testing {len(items)} servers (strict: keep only 200 + valid manifest) ...")
    alive = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=300) as ex:
        for key, idx, ok in ex.map(check, items):
            alive[(key, idx)] = ok
    for k, g in groups.items():
        g["servers"] = [s for i, s in enumerate(g["servers"])
                        if alive.get((k, i), False) or s.get("_force")]
    kept = sum(1 for v in alive.values() if v)
    print(f"  working: {kept}/{len(items)} servers (+ forced manual)")


def main():
    groups = {}
    try:
        from_iptv_org(groups)
    except Exception as e:
        print("iptv-org failed:", e)
    for url in EXTRA_M3U:
        txt = fetch_text(url)
        if txt:
            from_m3u(groups, txt, url.rsplit("/", 1)[-1])
    try:
        from_198(groups)
    except Exception as e:
        print("198 failed:", e)
    try:
        from_manual(groups)
    except Exception as e:
        print("manual failed:", e)

    # back-fill missing logos by channel name, then cap servers
    filled = 0
    for g in groups.values():
        if not g["logo"]:
            lg = NAME_LOGO.get(norm(g["name"]))
            if lg:
                g["logo"] = lg
                filled += 1
        g["servers"] = g["servers"][:MAX_SERVERS]
    print(f"Back-filled {filled} logos by name")

    keep_working(groups)

    # drop the internal _force marker so it doesn't leak into channels.json
    for g in groups.values():
        for s in g["servers"]:
            s.pop("_force", None)

    channels = []
    for key, g in groups.items():
        if not g["servers"]:
            continue
        channels.append({
            "id": "r_" + re.sub(r"[^a-z0-9]+", "", key.lower())[:24] + str(abs(hash(key)) % 1000),
            "name": g["name"][:80],
            "cat": g["cat"],
            "logo": g["logo"] or "",
            "servers": g["servers"],
        })
    # Sports first, then by name
    order = {"Sports": 0, "News": 1, "Entertainment": 2, "Movies": 3, "Bangla": 4}
    channels.sort(key=lambda c: (order.get(c["cat"], 9), c["name"].lower()))

    out = {"updated": int(time.time()), "count": len(channels), "channels": channels}
    with open("channels.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    cats = {}
    for c in channels:
        cats[c["cat"]] = cats.get(c["cat"], 0) + 1
    print(f"\nWrote channels.json: {len(channels)} channels, {sum(len(c['servers']) for c in channels)} servers")
    print("By category:", dict(sorted(cats.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
