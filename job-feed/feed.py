#!/usr/bin/env python3
"""job-feed: daily candidate feed for KUNG's job-hunt skill. Runs in GitHub Actions, not in Claude.

Sources (merged, de-duplicated):
  1. SimplifyJobs/Summer2027-Internships  listings.json
  2. vanshb03/Summer2027-Internships      listings.json  (roles Simplify does not carry)
  3. Public ATS job-board APIs (Greenhouse / Lever / Ashby / Workday) of AV and robotics companies.
     Boards come from boards.json plus any board slug seen in Simplify URLs of AV/robot companies.

Filter = the loose two-tier hunt_v3 filter (core -> read JD, triage -> Claude title triage), with one fix:
a posting first published on/after 2026-08-01 but tagged only "Summer 2026" is treated as Summer 2027
(Simplify mislabels these; flag "term_tag_2026").

Output: data/job-feed/feed.json
  {generated_at, window_days, counts, errors, core:[cand], triage:[{line, t, members:[cand]}]}
  cand = {id, company, title, url, locations, degrees, sponsorship_tag, company_url, date_posted, t, src, flags}
  t = unix time of max(date_posted, date_updated); the skill keeps rows with t > last_simplify_run_utc.

usage: feed.py [--simplify PATH] [--vansh PATH] [--no-ats] [--out PATH] [--window 60]
"""
import argparse, collections, datetime as dt, json, os, re, sys, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SIMPLIFY_URL = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
VANSH_URL = "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json"
SEASON_START = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc).timestamp()  # Summer 2027 postings start here
UA = {"User-Agent": "kung-job-feed (personal job search; github.com/RICHKUNG)"}

# --- hard drops: certainly not KUNG's kind of job ---
HARD_JUNK = re.compile(
    r"marketing|financ|accounting|\bsales\b|legal|paralegal|\bhr\b|human resources|recruit|talent|"
    r"business (?:analy|develop|operations|intelligence)|procurement|purchasing|supply chain|\bmba\b|"
    r"product manag|program manag|project manag|\btax\b|audit|actuar|investment|"
    r"banking|underwrit|real estate|customer success|people (?:ops|operations)|graphic design|"
    r"ux (?:design|research)|ui/ux|social media|content (?:creator|writer)|journalis|treasury|"
    r"data analyst|policy analyst|economics|strategy (?:analyst|intern)|operations analyst|"
    r"administrative|office assistant|\bevents?\b|corporate communications|communications (?:intern|specialist|coordinator)", re.I)
SKIP_CATEGORY = {"Product", "Quant", "Product Management"}

# --- core routing (straight to JD read) ---
INCLUDE = re.compile(
    r"robot|autonom|self[- ]?driv|perception|computer vision|\bvision\b|\b3d\b|slam|lidar|radar|"
    r"sensor|locali[sz]ation|mapping|motion planning|path planning|trajectory|planning and control|"
    r"\bcontrols?\b|navigation|manipulat|embodied|embedded|firmware|onboard|on-board|rtos|real[- ]time|"
    r"adas|avionics|flight software|simulation|drone|uav|mechatronic|vehicle software|driver assist|"
    r"calibrat|middleware|sensor fusion|point cloud|driving|automotive|prediction|planner|"
    r"(?:mission|behavior|route) planning|world model|physical ai|\bros\b|c\+\+|edge ai|on-device|"
    r"kinematic|actuator|mobility", re.I)
ENG_TITLE = re.compile(r"software|engineer|developer|firmware|machine learning|\bml\b|\bai\b|"
                       r"computer vision|autonomy|perception|research|scien", re.I)
AV_CO = re.compile(
    r"waymo|zoox|aurora innovation|^aurora$|\bnuro\b|motional|may mobility|kodiak|torc robotics|waabi|gatik|"
    r"plus ?ai|^plus$|bot auto|stack av|applied intuition|foretellix|mobileye|avride|pony\.?ai|weride|wayve|"
    r"tensor auto|glydways|cyngn|forterra|outrider|pronto|bedrock robotics|einride|volvo autonomous|latitude ai|"
    r"woven by toyota|toyota research|42dot|^tesla|^general motors$|^gm$|cruise|^ford|^rivian|^lucid|^nissan|"
    r"american honda|honda research|hyundai|stellantis|^aptiv|^robert bosch$|^bosch$|^magna|continental automotive|"
    r"^continental$|^zf|^denso|^valeo|parallel systems|overland ai|quantum signal|harbinger|scout motors|"
    r"\bslate auto|^polestar|^volvo|^mercedes|^bmw|^porsche|^audi|^volkswagen|^toyota|^subaru|^mazda|^kia", re.I)
ROBOT_CO = re.compile(
    r"robot|^figure|agility|boston dynamics|skydio|zipline|apptronik|^1x|physical intelligence|dexterity|"
    r"symbotic|machina labs|skild|anybotics|serve robotics|starship|intuitive surgical|multiply labs|rhoda|"
    r"openmind|deere|cognex|zebra technologies|kion|trimble|teradyne|universal robots|fanuc|kuka|yaskawa|"
    r"locus|berkshire grey|gecko|sarcos|sanctuary|unitree|covariant|mujin|exotec|ocado|dexory|"
    r"carbon robotics|monarch tractor|bonsai|saildrone|sea machines|reliable robotics|merlin labs|"
    r"joby|archer aviation|wisk|skyryse|shield ai|anduril|field ai|collaborative robotics|the bot company|"
    r"verity|brain corp|nimble|formant|viam|foxglove|nvidia|qualcomm|ambarella|stryker|medtronic|"
    r"caterpillar|komatsu|cnh|agco|oshkosh|amazon robotics|x development|wing\b", re.I)
SOFT_JUNK = re.compile(  # at AV/robot companies these go to triage instead of core
    r"data engineer|data scien|analytics|cloud|frontend|front-end|full ?stack|mobile|\bui\b|\bux\b|\bit\b|"
    r"information security|cyber|rtl|asic|vlsi|physical design|circuit|mixed signal|analog|"
    r"design verification|\bdft\b|silicon|power electronics|energy|solar|\bcell\b|battery|thermal|hvac|"
    r"warranty|aftersales|dealer|customer|manufacturing|process engineer|industrial design|interior|"
    r"exterior|compiler|motorsport|certification|technician|mechanical|electrical|test engineer|"
    r"\bhardware\b(?![- ]in[- ]the[- ]loop)|wireless|\brf\b|antenna|product operations|data analy|data governance|"
    r"logistics|change management|commerciali[sz]ation|depot|facilities|carflow|performance analysis|\bhvp\b|"
    r"product owner|field operations|quality (?:engineer|assurance)", re.I)
TITLE_OFF = re.compile(
    r"electrical|\bhardware\b(?![- ]in[- ]the[- ]loop)|wireless|\brf\b|antenna|test engineer|"
    r"product operations|cloud platform|full ?stack|mechanical", re.I)
DEFENSE = re.compile(
    r"anduril|lockheed|northrop|raytheon|\brtx\b|general dynamics|l3harris|boeing|bae systems|leidos|"
    r"\bsaic\b|booz allen|sierra nevada|aerospace corporation|huntington ingalls|palantir|shield ai|"
    r"saronic|epirus|castelion|hadrian|caci|peraton|parsons|textron|teledyne|v2x", re.I)
NON_US = re.compile(
    r"canada|toronto|vancouver|montreal|ottawa|waterloo|\buk\b|united kingdom|london|cambridge, uk|"
    r"germany|munich|berlin|france|paris|switzerland|zurich|netherlands|israel|tel aviv|india|"
    r"bangalore|china|shanghai|beijing|japan|tokyo|korea|seoul|taiwan|taipei|singapore|ireland|"
    r"spain|italy|poland|sweden|denmark|australia|mexico|brazil|\bon\b, canada|\bbc\b|\bqc\b", re.I)
SMALL_ATS = re.compile(r"ashbyhq\.com|lever\.co|greenhouse\.io|workable\.com|rippling\.com", re.I)
PHD_TITLE = re.compile(r"\bph\.?d\b", re.I)
MS_TITLE = re.compile(r"\bms\b|\bm\.s\.|master", re.I)
REQUIRED_KEYS = ("id", "company_name", "title")


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|technology)\b\.?", " ", s)
    s = re.sub(r"\b(intern(ship)?|summer|2027|co-?op|ms|phd|bs|m\.s\.|ph\.d\.)\b", " ", s)
    return re.sub(r"[^a-z0-9+]+", " ", s).strip()


def get(url, timeout=25, tries=2, body=None):
    data, hdr = None, UA
    if body is not None:
        data, hdr = json.dumps(body).encode(), {**UA, "Content-Type": "application/json", "Accept": "application/json"}
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=hdr), timeout=timeout) as r:
                return r.read()
        except Exception as e:
            err = e
            time.sleep(2)
    raise err


def load(path, url):
    raw = open(path, "rb").read() if path else get(url, timeout=120)
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) < 500:
        raise ValueError(f"listings looks wrong ({type(data).__name__}, len {len(data) if hasattr(data, '__len__') else '?'})")
    return data


ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|(?<=gh_jid=)\d+|(?<=/jobs/)\d{5,}|_(?:JR|R)-?\d+", re.I)


def url_ids(u):
    return {m.lower() for m in ID_RE.findall(u or "")}


def ukey(u):
    u = (u or "").split("?")[0].rstrip("/").lower()
    return re.sub(r"/(application|apply)$", "", u)


def from_vansh(v):
    season = v.get("season") or ""
    return {"id": "v:" + str(v.get("id")), "company_name": v.get("company_name"), "title": v.get("title"),
            "url": v.get("url"), "locations": v.get("locations") or [], "degrees": [],
            "sponsorship": v.get("sponsorship"), "company_url": v.get("company_url"),
            "active": v.get("active", True), "is_visible": v.get("is_visible", True),
            "terms": ["Summer 2027"] if "Summer" in season else [season or "N/A"],
            "date_posted": v.get("date_posted"), "date_updated": v.get("date_updated"),
            "category": None, "_src": "vanshb03"}


BOARD_RE = [("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
            ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_.-]+)", re.I)),
            ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.%-]+)", re.I)),
            # Workday slug = tenant/wdN/site, e.g. aptiv/wd5/aptiv_careers
            ("workday", re.compile(r"([a-z0-9-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[a-z]{2}/)?([a-z0-9_-]+)", re.I))]
INTERN = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b", re.I)
OFF_SEASON = re.compile(r"\b(fall|autumn|winter|spring)\b", re.I)


def discover_boards(simplify):
    """ATS board slugs of AV / robotics companies, read off Simplify listing URLs."""
    found = {}
    for x in simplify:
        co = x.get("company_name") or ""
        if not (AV_CO.search(co) or ROBOT_CO.search(co)):
            continue
        for ats, rx in BOARD_RE:
            m = rx.search(x.get("url") or "")
            if not m:
                continue
            if ats == "workday":
                if re.search(r"redeploy|internal", m.group(2), re.I):
                    continue
                found.setdefault((ats, m.group(1).replace(".", "/").lower() + "/" + m.group(2)), co)
            elif m.group(1).lower() not in ("embed", "jobs"):
                found.setdefault((ats, m.group(1).lower()), co)
    return found


def ts(v):
    if isinstance(v, (int, float)):
        return v / 1000 if v > 1e11 else v
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0


WD_SEEN = {}        # Workday id -> first-seen unix time (Workday only says "Posted 3 Days Ago")
WD_SEEDED = [False]  # True when a seen-cache already existed at start of this run


def wd_posted(text, now):
    text = (text or "").lower()
    if "today" in text:
        return now, 0
    if "yesterday" in text:
        return now - 86400, 1
    m = re.search(r"(\d+)\+?\s*days?", text)
    d = int(m.group(1)) if m else 30
    return now - d * 86400, d


def fetch_workday(slug):
    tenant, wd, site = slug.split("/")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    now, out, offset, total = time.time(), [], 0, None
    while offset < (total if total is not None else 1) and offset < 400:
        r = json.loads(get(f"{host}/wday/cxs/{tenant}/{site}/jobs",
                           body={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "intern"}))
        if total is None:
            total = r.get("total") or 0
        posts = r.get("jobPostings") or []
        if not posts:
            break
        for j in posts:
            path = j.get("externalPath") or ""
            jid = path.rsplit("_", 1)[-1] if "_" in path else path
            posted, days = wd_posted(j.get("postedOn"), now)
            key = f"{slug}/{jid}"
            if key in WD_SEEN:
                t = WD_SEEN[key]
            else:  # new to us: if the cache existed, a fresh posting counts from today (avoids posted-after-run misses)
                t = now if (WD_SEEDED[0] and days <= 2) else posted
                WD_SEEN[key] = t
            loc = j.get("locationsText") or ""
            if re.fullmatch(r"\d+ locations", loc, re.I):  # multi-location: the URL path still names the first one
                m = re.match(r"/job/([^/]+)/", path)
                loc = m.group(1).replace("-", " ").strip() if m else ""
            out.append((jid, j.get("title"), host + f"/{site}" + path, [loc], posted, t))
        offset += len(posts)
        time.sleep(0.3)
    return out


def fetch_board(ats, slug, company):
    out = []
    if ats == "workday":
        out = fetch_workday(slug)
    elif ats == "greenhouse":
        for j in json.loads(get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")).get("jobs", []):
            out.append((str(j.get("id")), j.get("title"), j.get("absolute_url"), [(j.get("location") or {}).get("name") or ""],
                        ts(j.get("first_published") or j.get("updated_at")), ts(j.get("updated_at"))))
    elif ats == "lever":
        for j in json.loads(get(f"https://api.lever.co/v0/postings/{slug}?mode=json")):
            c = j.get("categories") or {}
            out.append((j.get("id"), j.get("text"), j.get("hostedUrl"), [c.get("location") or ""], ts(j.get("createdAt")), ts(j.get("createdAt"))))
    elif ats == "ashby":
        for j in json.loads(get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")).get("jobs", []):
            if j.get("isListed") is False:
                continue
            title = j.get("title") or ""
            if j.get("employmentType") == "Intern" and not INTERN.search(title):
                title += " (Intern)"
            out.append((j.get("id"), title, j.get("jobUrl"), [j.get("location") or ""], ts(j.get("publishedAt")), ts(j.get("publishedAt"))))
    rows = []
    for jid, title, url, locs, posted, upd in out:
        title = title or ""
        if not INTERN.search(title) or re.search(r"\binternal\b", title, re.I):
            continue
        if OFF_SEASON.search(title) and not re.search(r"summer", title, re.I):
            continue
        if re.search(r"2026", title) and not re.search(r"2027", title) and posted < SEASON_START:
            continue
        locs = [l for l in locs if l and not re.fullmatch(r"\d+ locations", l, re.I)]
        rows.append({"id": f"ats:{ats}/{slug}/{jid}", "company_name": company, "title": title, "url": url,
                     "locations": locs, "degrees": [], "sponsorship": None, "company_url": None,
                     "active": True, "is_visible": True, "terms": ["N/A"], "date_posted": posted, "date_updated": upd,
                     "category": None, "_src": f"{ats}/{slug}"})
    return rows


def flags_for(x, title, co, av, tag26):
    f = ["av_priority"] if av else []
    degrees = x.get("degrees") or []
    if TITLE_OFF.search(title): f.append("title_off_direction")
    if ROBOT_CO.search(co): f.append("robot_co")
    if not x.get("active"): f.append("simplify_says_closed_check_official")
    if (degrees and all(g == "PhD" for g in degrees)) or (PHD_TITLE.search(title) and not MS_TITLE.search(title)):
        f.append("phd_only")
    if degrees and not any("Master" in g for g in degrees): f.append("degree_list_has_no_masters")
    locs = x.get("locations") or []
    if locs and all(NON_US.search(l) for l in locs): f.append("non_us")
    if DEFENSE.search(co): f.append("defense_co")  # NOT evidence of exclusion; the skill decides from JD text
    if x.get("sponsorship") in ("Does Not Offer Sponsorship", "U.S. Citizenship is Required"):
        f.append("simplify_tag:" + x["sponsorship"])
    if tag26: f.append("term_tag_2026")
    return f


def summer27(x):
    """(ok, tag26). Simplify tags some fresh postings 'Summer 2026'; posted in the 2027 season => treat as 2027."""
    terms = x.get("terms") or []
    if not terms or any(("Summer 2027" in s) or s in ("N/A", "") for s in terms):
        return True, False
    if any("Summer 2026" in s for s in terms) and (x.get("date_posted") or 0) >= SEASON_START:
        return True, True
    return False, False


def build(simplify, vansh, ats_rows, window_days, now):
    since = now.timestamp() - window_days * 86400
    s_urls = {ukey(x.get("url")) for x in simplify}
    s_ids = set().union(*(url_ids(x.get("url")) for x in simplify)) if simplify else set()
    s_titles = collections.defaultdict(set)
    for x in simplify:
        s_titles[norm(x.get("company_name"))].add(norm(x.get("title")))

    def in_simplify(x):
        if ukey(x.get("url")) in s_urls or (url_ids(x.get("url")) & s_ids):
            return True
        t = norm(x.get("title"))
        return any(t == s or (t and s and (t in s or s in t)) for s in s_titles.get(norm(x.get("company_name")), ()))

    extra_v = [x for x in map(from_vansh, vansh) if not in_simplify(x)]
    seen_extra = collections.defaultdict(set)
    for x in extra_v:
        seen_extra[norm(x["company_name"])].add(norm(x["title"]))
    extra_a, seen_ats = [], set()
    for x in ats_rows:  # the same board can be reached through two slugs; keep one row per posting
        key = (norm(x["company_name"]), norm(x["title"]), tuple(x.get("locations") or []))
        if key in seen_ats or in_simplify(x) or norm(x["title"]) in seen_extra.get(norm(x["company_name"]), ()):
            continue
        seen_ats.add(key); extra_a.append(x)
    ncos = collections.Counter(x.get("company_name") or "" for x in simplify)
    sk = collections.Counter()
    core, tri = [], {}
    for x in list(simplify) + extra_v + extra_a:
        try:
            title, co = x.get("title") or "", x.get("company_name") or ""
            av = bool(AV_CO.search(co)); robo = bool(ROBOT_CO.search(co))
            t = max(x.get("date_posted") or 0, x.get("date_updated") or 0)
            if not x.get("is_visible", True) or t < since or (not x.get("active") and not (av or robo)):
                sk["stale_or_hidden"] += 1; continue
            if x.get("category") in SKIP_CATEGORY:
                sk["category"] += 1; continue
            ok, tag26 = summer27(x)
            if not ok:
                sk["not_summer_2027"] += 1; continue
            if HARD_JUNK.search(title):
                sk["hard_junk"] += 1; continue
            f = flags_for(x, title, co, av, tag26)
            ats = str(x.get("id", "")).startswith("ats:")
            if ats and ats_non_us(x.get("locations")) and "non_us" not in f:
                f.append("non_us")
            soft = SOFT_JUNK.search(title) or (x.get("_src") and NON_ENG_EXTRA.search(title))
            # Simplify already curates its rows; raw ATS rows need an engineering-looking title to skip triage
            eng = not ats or INCLUDE.search(title) or ATS_SW.search(title)
            is_core = bool((((av or robo) and not soft) or (INCLUDE.search(title) and not soft)
                            or (INCLUDE.search(co) and ENG_TITLE.search(title) and not soft)) and eng
                           and not (ats and "non_us" in f))
            c = {"id": x["id"], "company": co, "title": title, "url": x.get("url"),
                 "locations": x.get("locations") or [], "degrees": x.get("degrees") or [],
                 "sponsorship_tag": x.get("sponsorship"), "company_url": x.get("company_url"),
                 "date_posted": dt.datetime.fromtimestamp(x.get("date_posted") or t, dt.timezone.utc).date().isoformat(),
                 "t": int(t), "src": x.get("_src", "simplify"), "flags": f}
            if is_core:
                core.append(c)
            elif "phd_only" in f or "non_us" in f:
                sk["triage_phd_or_abroad"] += 1
            else:
                tri.setdefault((norm(co), norm(title)), []).append(c)
        except Exception:
            sk["malformed"] += 1
    core.sort(key=lambda k: (k["t"], k["id"]))
    triage = []
    for key, cs in sorted(tri.items(), key=lambda kv: (kv[1][0]["company"].lower(), kv[0][1])):
        c0 = cs[0]
        locs = sorted({l for c in cs for l in c["locations"]})
        loc = (locs[0] if locs else "?") + (f"+{len(locs)-1}" if len(locs) > 1 else "")
        deg = "".join(sorted({g[0] for c in cs for g in c["degrees"]})) or "?"
        fl = ",".join(s.replace("simplify_tag:", "tag:") for s in c0["flags"]
                      if s in ("defense_co", "non_us", "phd_only", "av_priority", "term_tag_2026") or s.startswith("simplify_tag"))
        if ncos.get(c0["company"], 0) <= 3 and SMALL_ATS.search(c0.get("url") or ""):
            fl = (fl + "," if fl else "") + "small"
        if c0["src"] != "simplify":
            fl = (fl + "," if fl else "") + "src:" + c0["src"].split("/")[0]
        n = f" x{len(cs)}" if len(cs) > 1 else ""
        triage.append({"line": f"{c0['company']}|{c0['title']}{n}|{loc}|{deg}|{fl}", "t": max(c["t"] for c in cs), "members": cs})
    counts = {"simplify": len(simplify), "vansh": len(vansh), "vansh_not_in_simplify": len(extra_v),
              "ats_intern_rows": len(ats_rows), "ats_not_in_simplify": len(extra_a),
              "core": len(core), "triage_groups": len(triage), "skipped": dict(sk)}
    return core, triage, counts


# vanshb03 / ATS rows carry no Simplify category, so obviously non-software titles at AV / robot
# companies would land in core; send them to triage instead (still visible, never dropped).
NON_ENG_EXTRA = re.compile(
    r"\bdesign(?:er)?\b(?! verification)|brand|strategy|sourcing|supplier|administration|\bproduction\b|"
    r"industrial engineer|process improvement|reliability|airworthiness|information technology|"
    r"low observables|optical payload|operations engineer|\bsecurity\b|product analyst", re.I)

# Raw ATS rows (esp. big Workday employers) are mostly mechanical / materials / plant roles: only
# software-looking titles go straight to core, the rest go to title triage.
ATS_SW = re.compile(r"software|machine learning|\bml\b|\bai\b|computer vision|autonom|perception|deep learning|"
                    r"firmware|developer|algorithm|data scien|research scien|computing|robot", re.I)

# ATS rows have free-text locations from anywhere in the world: treat as non-US unless it looks US.
US_STATES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
             "illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
             "mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|"
             "north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|"
             "south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming")
US_LOC = re.compile(r"united states|\busa?\b|\bu\.s\.|remote|virtual|" + US_STATES +
                    r"|,\s*(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY])\b"
                    r"|\b(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[DLNA]|K[SY]|LA|M[EDAINSOT]|N[EVHJMYCD]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[TA]|W[AVIY]),\s", re.I)


def ats_non_us(locs):
    return bool(locs) and not any(US_LOC.search(l) for l in locs)


GO_RE = re.compile(r"^https?://go\.vansh\.nyc/", re.I)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def redirect_target(u):
    """Location header of a short link, without downloading the destination page."""
    try:
        with urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(u, headers=UA), timeout=15) as r:
            return r.geturl()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
        if e.code in (301, 302, 303, 307, 308) and loc:
            return loc
        raise


def resolve_golinks(cands, cache_path, errors, limit=1500):
    """vanshb03 urls are go.vansh.nyc short links; replace with the real posting URL (cached)."""
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:
        cache = {}
    n = 0
    for c in cands:
        u = c.get("url") or ""
        if not GO_RE.match(u):
            continue
        if u not in cache and n < limit:
            n += 1
            try:
                cache[u] = redirect_target(u)
            except Exception as e:
                errors.append(f"golink {u}: {type(e).__name__}")
                continue
            time.sleep(0.1)
        if cache.get(u) and not GO_RE.match(cache[u]):
            c["short_url"], c["url"] = u, cache[u]
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), indent=0, sort_keys=True)


def dump(path, obj):
    """One candidate per line so daily git diffs stay small."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("{\n")
        for k in ("generated_at", "window_days", "counts", "errors"):
            f.write(f" {json.dumps(k)}: {json.dumps(obj[k], ensure_ascii=False)},\n")
        for k in ("core", "triage"):
            f.write(f" {json.dumps(k)}: [\n")
            f.write(",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in obj[k]))
            f.write("\n ]" + ("," if k == "core" else "") + "\n")
        f.write("}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simplify"); ap.add_argument("--vansh"); ap.add_argument("--no-ats", action="store_true")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "data", "job-feed", "feed.json"))
    ap.add_argument("--window", type=int, default=60)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    errors = []
    simplify = load(a.simplify, SIMPLIFY_URL)  # fatal if this fails: keep yesterday's feed, the skill sees it is stale
    for k in ("id", "company_name", "title"):
        if k not in simplify[0]:
            sys.exit(f"Simplify schema changed: missing {k}")
    try:
        vansh = load(a.vansh, VANSH_URL)
    except Exception as e:
        vansh = []; errors.append(f"vanshb03: {type(e).__name__}: {e}")
    ats_rows, boards = [], {}
    if not a.no_ats:
        boards = discover_boards(simplify)
        try:
            for b in json.load(open(os.path.join(HERE, "boards.json"), encoding="utf-8"))["boards"]:
                boards[(b["ats"], b["slug"].lower())] = b["company"]
        except Exception as e:
            errors.append(f"boards.json: {e}")
        seen_path = os.path.join(os.path.dirname(os.path.abspath(a.out)), "workday_seen.json")
        try:
            WD_SEEN.update(json.load(open(seen_path))); WD_SEEDED[0] = True
        except Exception:
            pass
        for (ats, slug), co in sorted(boards.items()):
            try:
                ats_rows += fetch_board(ats, slug, co)
            except Exception as e:
                errors.append(f"{ats}/{slug}: {type(e).__name__}")
            time.sleep(0.2)
        if WD_SEEN:
            os.makedirs(os.path.dirname(seen_path), exist_ok=True)
            json.dump(WD_SEEN, open(seen_path, "w"), indent=0, sort_keys=True)
        print(f"boards: {len(boards)} checked, {len(ats_rows)} intern rows, {len(errors)} errors", flush=True)
    core, triage, counts = build(simplify, vansh, ats_rows, a.window, now)
    counts["boards_checked"] = len(boards)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if not a.no_ats:
        resolve_golinks(core + [c for g in triage for c in g["members"]], os.path.join(os.path.dirname(out), "golinks.json"), errors)
    dump(out, {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "window_days": a.window, "counts": counts,
               "errors": errors[:60], "core": core, "triage": triage})
    print(json.dumps({"generated_at": now.isoformat(), **counts, "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
