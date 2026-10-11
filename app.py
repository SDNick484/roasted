"""Roasted — a log of the coffee beans I buy (mostly from roasters on trips) and how each one brewed.
Flask + SQLite, a sibling of Cookbooks / Dinner Log / the wish list on CT 210.

Model:  roaster -> coffee (one offering, e.g. "Ethiopia Guji Hambela, natural")
                -> bag (one purchase: where, when, roast date, price, photos)
                -> review (one per brew method per bag; edit it as you dial in)

Public:  /            browse coffees, roasters and reviews (no prices, private notes or GPS)
         /api/...     public JSON
Editors: /admin       same UI with editing; put this path behind Cloudflare Access
         /admin/api/  full JSON + writes; requires a verified Access JWT
                      (or a direct LAN request when LAN_EDIT=1)
MCP:     /mcp         public read-only tools;  /mcp/<MCP_TOKEN> adds private tools

Environment (see /etc/conf.d/roasted):
  DATA_DIR, CF_TEAM_DOMAIN, CF_AUD, ADMIN_EMAILS, LAN_EDIT, PUBLIC_VIEW,
  ANTHROPIC_API_KEY, ANTHROPIC_MODEL, DEFAULT_GRINDER, DEFAULT_CURRENCY, MCP_TOKEN, MCP_PUBLIC
"""
import base64, csv, datetime as dt, difflib, hmac, io, json, os, re, sqlite3, time, unicodedata
import urllib.error, urllib.parse, urllib.request, uuid
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
PHOTO_DIR = DATA_DIR / "photos"
DB_PATH = DATA_DIR / "roasted.db"
CF_TEAM_DOMAIN = os.environ.get("CF_TEAM_DOMAIN", "").strip().rstrip("/")
CF_AUD = os.environ.get("CF_AUD", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
LAN_EDIT = os.environ.get("LAN_EDIT", "1") == "1"
PUBLIC_VIEW = os.environ.get("PUBLIC_VIEW", "1") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5").strip()
DEFAULT_GRINDER = os.environ.get("DEFAULT_GRINDER", "Baratza Encore (M2 burrs)").strip()
DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "USD").strip().upper() or "USD"
MCP_TOKEN = os.environ.get("MCP_TOKEN", "").strip()
MCP_PUBLIC = os.environ.get("MCP_PUBLIC", "1") == "1"
UA = "roasted/1.0 (personal coffee log; https://coffee.nickandrade.com)"

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024

# =============================================================================== db
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS roasters (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, city TEXT, region TEXT, country TEXT, website TEXT, notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS roasters_name ON roasters(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS coffees (
  id INTEGER PRIMARY KEY,
  roaster_id INTEGER NOT NULL REFERENCES roasters(id) ON DELETE CASCADE,
  name TEXT NOT NULL, origin TEXT, region TEXT, producer TEXT, varietal TEXT, process TEXT,
  altitude TEXT, roast_level TEXT, roaster_notes TEXT, is_blend INTEGER NOT NULL DEFAULT 0, description TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS coffees_roaster ON coffees(roaster_id);
CREATE TABLE IF NOT EXISTS bags (
  id INTEGER PRIMARY KEY,
  coffee_id INTEGER NOT NULL REFERENCES coffees(id) ON DELETE CASCADE,
  purchased_on TEXT, shop TEXT, city TEXT, region TEXT, country TEXT, lat REAL, lng REAL, trip TEXT,
  online INTEGER NOT NULL DEFAULT 0, price REAL, currency TEXT, weight_g REAL,
  roast_date TEXT, opened_on TEXT, finished_on TEXT, notes TEXT, added_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS bags_coffee ON bags(coffee_id);
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  bag_id INTEGER NOT NULL REFERENCES bags(id) ON DELETE CASCADE,
  file TEXT NOT NULL, pos INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS photos_bag ON photos(bag_id, pos);
CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY,
  bag_id INTEGER NOT NULL REFERENCES bags(id) ON DELETE CASCADE,
  method TEXT NOT NULL, rating REAL,
  dose_g REAL, water_g REAL, grinder TEXT, grind TEXT, temp TEXT, brew_time TEXT, recipe TEXT,
  brewer TEXT, flavors TEXT, notes TEXT, brewed_on TEXT, reviewed_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (bag_id, method)
);
CREATE TABLE IF NOT EXISTS methods (
  name TEXT PRIMARY KEY COLLATE NOCASE, pos INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
  grinder TEXT, grind TEXT, brewer TEXT, recipe TEXT
);
"""
# Starting equipment per method: pre-fills a method's first review (after that, its last review is used).
# Editable in the app under More -> Brew methods.
ENCORE = "Baratza Encore (M2 burrs)"
METHOD_PRESETS = {
    "Drip": {"grinder": ENCORE, "grind": "21", "brewer": "Breville Precision Brewer", "recipe": "Gold Cup mode"},
    "Cold brew": {"grinder": ENCORE, "grind": "37"},
    "Nitro cold brew": {"grinder": ENCORE, "grind": "37"},
    "French press": {"grinder": ENCORE},
    "Pour over": {"grinder": ENCORE},
    "Percolator": {"grinder": ENCORE},
    "Espresso": {"grinder": "Eureka Mignon Zero 55mm", "grind": "4", "brewer": "Breville Infuser"},
}
DEFAULT_METHODS = ["Drip", "Cold brew", "Nitro cold brew", "French press", "Pour over", "Percolator",
                   "Espresso", "AeroPress", "Moka pot", "V60", "Chemex", "Kalita Wave", "Clever dripper", "Siphon", "Turkish"]


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def migrate(con):
    """Add columns that newer versions expect to an older database."""
    def cols(t):
        return {r[1] for r in con.execute(f"PRAGMA table_info({t})")}
    for col in ("grinder", "grind", "brewer", "recipe"):
        if col not in cols("methods"):
            con.execute(f"ALTER TABLE methods ADD COLUMN {col} TEXT")
    if "brewer" not in cols("reviews"):
        con.execute("ALTER TABLE reviews ADD COLUMN brewer TEXT")


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    if con.execute("SELECT count(*) FROM methods").fetchone()[0] == 0:
        for i, m in enumerate(DEFAULT_METHODS):
            p = METHOD_PRESETS.get(m, {})
            con.execute("INSERT INTO methods(name, pos, grinder, grind, brewer, recipe) VALUES (?,?,?,?,?,?)",
                        (m, i, p.get("grinder"), p.get("grind"), p.get("brewer"), p.get("recipe")))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '2')")
    con.commit()
    con.close()
    # staged uploads that never became a bag
    cutoff = time.time() - 2 * 86400
    for p in PHOTO_DIR.glob("tmp-*"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


# =========================================================================== helpers
def clean(s):
    if s is None:
        return None
    s = str(s).strip()
    return s or None


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


def make_err(code, msg, **extra):
    r = jsonify(error=msg, **extra)
    r.status_code = code
    return r


def num(v, lo=None, hi=None, what="value"):
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace(",", "."))
    except ValueError:
        abort(make_err(400, f"{what} must be a number"))
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        abort(make_err(400, f"{what} must be between {lo:g} and {hi:g}"))
    return x


def iso_date(v, what="date"):
    v = clean(v)
    if not v:
        return None
    try:
        return dt.date.fromisoformat(v[:10]).isoformat()
    except ValueError:
        abort(make_err(400, f"{what} should look like 2026-10-11"))


WEIGHT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|kilo|g|gr|gram|oz|ounce|lb|lbs|pound)?", re.I)


def parse_weight(v):
    """'12 oz' -> 340.2, '1 lb' -> 453.6, '250g' -> 250, 340 -> 340 (grams)."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = WEIGHT_RE.search(str(v))
    if not m:
        abort(make_err(400, "Weight should look like 12 oz, 1 lb or 250 g"))
    x, unit = float(m.group(1).replace(",", ".")), (m.group(2) or "g").lower()
    factor = {"kg": 1000, "kilo": 1000, "oz": 28.3495, "ounce": 28.3495, "lb": 453.592, "lbs": 453.592,
              "pound": 453.592}.get(unit, 1)
    return round(x * factor, 1)


def split_list(s):
    return [p.strip() for p in re.split(r"[,/&;+]|\band\b", s or "") if p.strip()]


FILLER = {"coffee", "coffees", "roasters", "roaster", "roasting", "roastery", "co", "company", "the", "and",
          "espresso", "blend", "whole", "bean", "beans", "cafe", "caffe", "torrefazione"}


def sim(a, b):
    """Name similarity for matching a label to what's already in the log (0..1)."""
    def norm(s):
        s = re.sub(r"[’'`]", "", fold(s))
        return " ".join(re.findall(r"[a-z0-9]+", s))
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ta, tb = set(a.split()) - FILLER, set(b.split()) - FILLER
    if ta and tb and (ta <= tb or tb <= ta):
        return 0.9
    if a in b or b in a:
        return 0.88
    return difflib.SequenceMatcher(None, a, b).ratio()


def days_between(a, b):
    try:
        return (dt.date.fromisoformat(b[:10]) - dt.date.fromisoformat(a[:10])).days
    except (TypeError, ValueError):
        return None


def today():
    return dt.date.today().isoformat()


def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Language": "en"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2 * 1024 * 1024))


# ============================================================================== auth
_jwks = None


def access_identity():
    """Return the editor's email (or 'lan'), or None."""
    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if token and CF_TEAM_DOMAIN and CF_AUD:
        global _jwks
        import jwt
        try:
            if _jwks is None:
                _jwks = jwt.PyJWKClient(f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True)
            key = _jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=CF_AUD,
                                issuer=f"https://{CF_TEAM_DOMAIN}")
        except Exception as e:  # noqa: BLE001 — any failure means "not an editor"
            app.logger.warning("Access JWT rejected: %s", e)
            return None
        email = (claims.get("email") or "").lower()
        if ADMIN_EMAILS and email not in ADMIN_EMAILS:
            return None
        return email or "access-user"
    via_cloudflare = request.headers.get("Cf-Ray") or request.headers.get("Cf-Connecting-Ip")
    if LAN_EDIT and not via_cloudflare and not token:
        return "lan"
    return None


def editor_required(fn):
    @wraps(fn)
    def inner(*a, **kw):
        who = access_identity()
        if not who:
            return jsonify(error="Sign in to edit"), 401
        g.editor = who
        return fn(*a, **kw)
    return inner


def who():
    e = getattr(g, "editor", None)
    return None if e in (None, "lan", "access-user") else e


# ============================================================================= pages
@app.get("/")
def index():
    if not PUBLIC_VIEW:
        return redirect("/admin")
    return send_from_directory(APP_DIR / "static", "index.html", max_age=0)


@app.get("/admin")
@app.get("/admin/")
def admin_index():
    return send_from_directory(APP_DIR / "static", "index.html", max_age=0)


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(APP_DIR / "static", p, max_age=300)


@app.get("/photos/<path:p>")
def photo_files(p):
    if p.startswith("tmp-") and not access_identity():
        abort(404)
    if not PUBLIC_VIEW and not access_identity():
        abort(404)
    return send_from_directory(PHOTO_DIR, p, max_age=7 * 86400)


@app.get("/healthz")
def healthz():
    con = connect()
    n = con.execute("SELECT count(*) FROM bags").fetchone()[0]
    con.close()
    return {"ok": True, "bags": n}


# ======================================================================= serializers
PRIVATE_BAG = ("price", "currency", "notes", "lat", "lng", "added_by")
PRIVATE_REVIEW = ("reviewed_by",)


def photos_for(bag_ids):
    out = {}
    if not bag_ids:
        return out
    qs = ",".join("?" * len(bag_ids))
    for r in db().execute(f"SELECT * FROM photos WHERE bag_id IN ({qs}) ORDER BY pos, id", list(bag_ids)):
        out.setdefault(r["bag_id"], []).append({"id": r["id"], "file": r["file"], "thumb": "t-" + r["file"]})
    return out


def bag_out(r, private, photos=None):
    d = dict(r)
    if not private:
        for k in PRIVATE_BAG:
            d.pop(k, None)
    if photos is not None:
        d["photos"] = photos.get(r["id"], [])
    if d.get("roast_date") and d.get("purchased_on"):
        d["roast_age_at_purchase"] = days_between(d["roast_date"], d["purchased_on"])
    if d.get("roast_date") and not d.get("finished_on"):
        d["roast_age_today"] = days_between(d["roast_date"], today())
    d["status"] = "finished" if d.get("finished_on") else ("open" if d.get("opened_on") else "sealed")
    return d


def review_out(r, private, roast_date=None):
    d = dict(r)
    if not private:
        for k in PRIVATE_REVIEW:
            d.pop(k, None)
    rd = roast_date if roast_date is not None else (d.get("roast_date") if "roast_date" in d.keys() else None)
    d["days_off_roast"] = days_between(rd, d.get("brewed_on")) if rd and d.get("brewed_on") else None
    d["flavor_list"] = split_list(d.get("flavors") or "")
    return d


COFFEE_LIST_SQL = """
SELECT c.*, r.name AS roaster_name, r.city AS roaster_city, r.country AS roaster_country,
  (SELECT count(*) FROM bags b WHERE b.coffee_id=c.id) AS bag_count,
  (SELECT count(*) FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE b.coffee_id=c.id) AS review_count,
  (SELECT avg(v.rating) FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE b.coffee_id=c.id AND v.rating IS NOT NULL) AS avg_rating,
  (SELECT max(v.rating) FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE b.coffee_id=c.id) AS best_rating,
  (SELECT max(coalesce(b.purchased_on, b.created_at)) FROM bags b WHERE b.coffee_id=c.id) AS last_bought,
  (SELECT p.file FROM photos p JOIN bags b ON b.id=p.bag_id WHERE b.coffee_id=c.id
     ORDER BY b.purchased_on DESC, p.pos, p.id LIMIT 1) AS photo
FROM coffees c JOIN roasters r ON r.id=c.roaster_id
"""


def coffee_rows(where="1=1", args=(), order="last_bought DESC", limit=None):
    sql = f"SELECT * FROM ({COFFEE_LIST_SQL}) WHERE {where} ORDER BY {order}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [dict(r) for r in db().execute(sql, list(args))]


def coffee_best_methods(ids):
    """{coffee_id: [{"method", "rating"}...]} best first."""
    out = {}
    if not ids:
        return out
    qs = ",".join("?" * len(ids))
    for r in db().execute(f"""SELECT b.coffee_id, v.method, max(v.rating) AS rating FROM reviews v
            JOIN bags b ON b.id=v.bag_id WHERE b.coffee_id IN ({qs}) AND v.rating IS NOT NULL
            GROUP BY b.coffee_id, v.method ORDER BY rating DESC""", list(ids)):
        out.setdefault(r[0], []).append({"method": r[1], "rating": r[2]})
    return out


# ========================================================================== read API
def feed(private):
    limit = max(1, min(int(request.args.get("limit", 30)), 100))
    offset = max(0, int(request.args.get("offset", 0)))
    sql = """SELECT v.*, b.roast_date, b.coffee_id, b.shop, b.city AS bag_city, b.country AS bag_country, b.trip,
                    c.name AS coffee_name, c.origin, c.process, c.roaster_id, r.name AS roaster_name
             FROM reviews v JOIN bags b ON b.id=v.bag_id JOIN coffees c ON c.id=b.coffee_id
             JOIN roasters r ON r.id=c.roaster_id"""
    args = []
    if request.args.get("method"):
        sql += " WHERE v.method=?"
        args.append(request.args["method"])
    sql += " ORDER BY coalesce(v.brewed_on, date(v.updated_at)) DESC, v.updated_at DESC LIMIT ? OFFSET ?"
    rows = db().execute(sql, args + [limit + 1, offset]).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    ph = photos_for({r["bag_id"] for r in rows})
    out = []
    for r in rows:
        d = review_out(r, private, r["roast_date"])
        d["photo"] = (ph.get(r["bag_id"]) or [{}])[0].get("file")
        out.append(d)
    return jsonify(reviews=out, more=more)


def list_coffees(private):
    where, args = ["1=1"], []
    q = request.args.get("q", "").strip()
    if q:
        for tok in re.findall(r"\w+", fold(q)):
            where.append("""(lower(name) LIKE ? OR lower(roaster_name) LIKE ? OR lower(coalesce(origin,'')) LIKE ?
                OR lower(coalesce(region,'')) LIKE ? OR lower(coalesce(producer,'')) LIKE ?
                OR lower(coalesce(varietal,'')) LIKE ? OR lower(coalesce(process,'')) LIKE ?
                OR lower(coalesce(roaster_notes,'')) LIKE ? OR lower(coalesce(roaster_city,'')) LIKE ?
                OR id IN (SELECT b.coffee_id FROM bags b WHERE lower(coalesce(b.city,'')||' '||coalesce(b.shop,'')||' '||coalesce(b.trip,'')) LIKE ?))""")
            args += [f"%{tok}%"] * 10
    for key, col in (("roaster", "roaster_id"),):
        if request.args.get(key):
            where.append(f"{col}=?")
            args.append(int(request.args[key]))
    if request.args.get("origin"):
        where.append("lower(coalesce(origin,'')) LIKE ?")
        args.append(f"%{fold(request.args['origin'])}%")
    if request.args.get("process"):
        where.append("lower(coalesce(process,'')) = ?")
        args.append(fold(request.args["process"]))
    if request.args.get("method"):
        where.append("id IN (SELECT b.coffee_id FROM bags b JOIN reviews v ON v.bag_id=b.id WHERE v.method=?)")
        args.append(request.args["method"])
    if request.args.get("trip"):
        where.append("id IN (SELECT coffee_id FROM bags WHERE trip=?)")
        args.append(request.args["trip"])
    sort = request.args.get("sort", "recent")
    order = {"rating": "avg_rating IS NULL, avg_rating DESC, last_bought DESC",
             "name": "lower(name)", "roaster": "lower(roaster_name), lower(name)"}.get(sort, "last_bought DESC")
    rows = coffee_rows(" AND ".join(where), args, order)
    best = coffee_best_methods([r["id"] for r in rows])
    for r in rows:
        r["best_methods"] = best.get(r["id"], [])[:3]
    return jsonify(coffees=rows, total=db().execute("SELECT count(*) FROM coffees").fetchone()[0])


def coffee_detail(coffee_id, private):
    rows = coffee_rows("id=?", [coffee_id])
    if not rows:
        abort(404)
    c = rows[0]
    c["roaster"] = dict(db().execute("SELECT * FROM roasters WHERE id=?", (c["roaster_id"],)).fetchone())
    bags = db().execute("SELECT * FROM bags WHERE coffee_id=? ORDER BY coalesce(purchased_on, created_at) DESC",
                        (coffee_id,)).fetchall()
    ph = photos_for([b["id"] for b in bags])
    c["bags"] = []
    for b in bags:
        d = bag_out(b, private, ph)
        d["reviews"] = [review_out(r, private, b["roast_date"]) for r in db().execute(
            "SELECT * FROM reviews WHERE bag_id=? ORDER BY rating IS NULL, rating DESC", (b["id"],))]
        c["bags"].append(d)
    c["by_method"] = [dict(r) for r in db().execute(
        """SELECT v.method, count(*) AS n, avg(v.rating) AS avg, max(v.rating) AS best FROM reviews v
           JOIN bags b ON b.id=v.bag_id WHERE b.coffee_id=? GROUP BY v.method ORDER BY avg DESC""", (coffee_id,))]
    return c


def get_coffee(coffee_id, private):
    return jsonify(coffee_detail(coffee_id, private))


def get_bag(bag_id, private):
    b = db().execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not b:
        abort(404)
    d = bag_out(b, private, photos_for([bag_id]))
    c = coffee_rows("id=?", [b["coffee_id"]])[0]
    d["coffee"] = c
    d["roaster"] = dict(db().execute("SELECT * FROM roasters WHERE id=?", (c["roaster_id"],)).fetchone())
    d["reviews"] = [review_out(r, private, b["roast_date"]) for r in db().execute(
        "SELECT * FROM reviews WHERE bag_id=? ORDER BY coalesce(brewed_on, updated_at) DESC", (bag_id,))]
    d["other_bags"] = [dict(r) for r in db().execute(
        "SELECT id, purchased_on, shop, city FROM bags WHERE coffee_id=? AND id<>? ORDER BY purchased_on DESC",
        (b["coffee_id"], bag_id))]
    return jsonify(d)


def list_roasters(private):
    rows = [dict(r) for r in db().execute("""
        SELECT r.*, count(DISTINCT c.id) AS coffee_count, count(DISTINCT b.id) AS bag_count,
          (SELECT avg(v.rating) FROM reviews v JOIN bags b2 ON b2.id=v.bag_id JOIN coffees c2 ON c2.id=b2.coffee_id
             WHERE c2.roaster_id=r.id) AS avg_rating,
          max(coalesce(b.purchased_on, b.created_at)) AS last_bought,
          (SELECT p.file FROM photos p JOIN bags b3 ON b3.id=p.bag_id JOIN coffees c3 ON c3.id=b3.coffee_id
             WHERE c3.roaster_id=r.id ORDER BY b3.purchased_on DESC, p.pos LIMIT 1) AS photo
        FROM roasters r LEFT JOIN coffees c ON c.roaster_id=r.id LEFT JOIN bags b ON b.coffee_id=c.id
        GROUP BY r.id""")]
    sort = request.args.get("sort", "name")
    if sort == "rating":
        rows.sort(key=lambda r: (r["avg_rating"] is None, -(r["avg_rating"] or 0)))
    elif sort == "bags":
        rows.sort(key=lambda r: (-r["bag_count"], fold(r["name"])))
    elif sort == "recent":
        rows.sort(key=lambda r: r["last_bought"] or "", reverse=True)
    else:
        rows.sort(key=lambda r: fold(re.sub(r"^the\s+", "", r["name"], flags=re.I)))
    return jsonify(roasters=rows)


def get_roaster(roaster_id, private):
    r = db().execute("SELECT * FROM roasters WHERE id=?", (roaster_id,)).fetchone()
    if not r:
        abort(404)
    d = dict(r)
    d["coffees"] = coffee_rows("roaster_id=?", [roaster_id], "last_bought DESC")
    best = coffee_best_methods([c["id"] for c in d["coffees"]])
    for c in d["coffees"]:
        c["best_methods"] = best.get(c["id"], [])[:3]
    d["places"] = [dict(x) for x in db().execute(
        """SELECT b.shop, b.city, b.country, b.trip, count(*) AS n, max(b.purchased_on) AS last
           FROM bags b JOIN coffees c ON c.id=b.coffee_id WHERE c.roaster_id=? AND b.online=0
           GROUP BY coalesce(b.shop,''), coalesce(b.city,'') ORDER BY last DESC""", (roaster_id,))]
    return jsonify(d)


def facets(private):
    con = db()
    origins = {}
    for (o,) in con.execute("SELECT origin FROM coffees WHERE origin IS NOT NULL"):
        for part in split_list(o):
            origins[part] = origins.get(part, 0) + 1
    return jsonify(
        origins=[{"name": k, "n": v} for k, v in sorted(origins.items(), key=lambda kv: fold(kv[0]))],
        processes=[dict(r) for r in con.execute(
            "SELECT lower(process) AS name, count(*) AS n FROM coffees WHERE process IS NOT NULL GROUP BY 1 ORDER BY 1")],
        methods=[r[0] for r in con.execute("SELECT name FROM methods WHERE hidden=0 ORDER BY pos, name")],
        used_methods=[dict(r) for r in con.execute(
            "SELECT method AS name, count(*) AS n FROM reviews GROUP BY method ORDER BY n DESC")],
        roasters=[dict(r) for r in con.execute("SELECT id, name, city, country FROM roasters ORDER BY name COLLATE NOCASE")],
        trips=[dict(r) for r in con.execute(
            "SELECT trip AS name, count(*) AS n, max(purchased_on) AS last FROM bags WHERE trip IS NOT NULL "
            "GROUP BY trip ORDER BY last DESC")],
        grinders=list(dict.fromkeys([r[0] for r in con.execute(
            "SELECT grinder FROM reviews WHERE grinder IS NOT NULL GROUP BY grinder ORDER BY max(updated_at) DESC")]
            + [r[0] for r in con.execute("SELECT DISTINCT grinder FROM methods WHERE grinder IS NOT NULL")]
            + [DEFAULT_GRINDER])),
        brewers=list(dict.fromkeys([r[0] for r in con.execute(
            "SELECT brewer FROM reviews WHERE brewer IS NOT NULL GROUP BY brewer ORDER BY max(updated_at) DESC")]
            + [r[0] for r in con.execute("SELECT DISTINCT brewer FROM methods WHERE brewer IS NOT NULL")])),
        flavors=top_flavors(40),
        totals={"coffees": con.execute("SELECT count(*) FROM coffees").fetchone()[0],
                "bags": con.execute("SELECT count(*) FROM bags").fetchone()[0],
                "roasters": con.execute("SELECT count(*) FROM roasters").fetchone()[0],
                "reviews": con.execute("SELECT count(*) FROM reviews").fetchone()[0]},
        default_currency=DEFAULT_CURRENCY, default_grinder=DEFAULT_GRINDER,
    )


def top_flavors(n):
    counts = {}
    for (f,) in db().execute("SELECT flavors FROM reviews WHERE flavors IS NOT NULL"):
        for x in split_list(f):
            k = x.lower()
            counts[k] = counts.get(k, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def bucket_age(d):
    if d is None:
        return None
    for hi, label in ((6, "0–6 days"), (13, "7–13"), (20, "14–20"), (34, "21–34"), (10 ** 6, "35+")):
        if d <= hi:
            return label


def stats_data(private):
    con = db()
    out = {}
    out["totals"] = {
        "coffees": con.execute("SELECT count(*) FROM coffees").fetchone()[0],
        "bags": con.execute("SELECT count(*) FROM bags").fetchone()[0],
        "roasters": con.execute("SELECT count(*) FROM roasters").fetchone()[0],
        "reviews": con.execute("SELECT count(*) FROM reviews").fetchone()[0],
        "avg_rating": con.execute("SELECT avg(rating) FROM reviews").fetchone()[0],
        "kg": round((con.execute("SELECT sum(weight_g) FROM bags").fetchone()[0] or 0) / 1000, 1),
        "purchase_cities": con.execute("SELECT count(DISTINCT lower(city)) FROM bags WHERE city IS NOT NULL").fetchone()[0],
        "purchase_countries": con.execute("SELECT count(DISTINCT lower(country)) FROM bags WHERE country IS NOT NULL").fetchone()[0],
        "roaster_cities": con.execute("SELECT count(DISTINCT lower(city)) FROM roasters WHERE city IS NOT NULL").fetchone()[0],
    }
    origins = {}
    for r in con.execute(
            "SELECT c.origin, v.rating FROM coffees c LEFT JOIN bags b ON b.coffee_id=c.id "
            "LEFT JOIN reviews v ON v.bag_id=b.id WHERE c.origin IS NOT NULL"):
        for part in split_list(r[0]):
            o = origins.setdefault(part, {"name": part, "ratings": []})
            if r[1] is not None:
                o["ratings"].append(r[1])
    out["totals"]["origins"] = len(origins)
    out["by_origin"] = sorted(({"name": o["name"], "n": len(o["ratings"]),
                                "avg": sum(o["ratings"]) / len(o["ratings"]) if o["ratings"] else None}
                               for o in origins.values()), key=lambda x: (-x["n"], x["name"]))
    out["by_method"] = [dict(r) for r in con.execute(
        "SELECT method AS name, count(*) AS n, avg(rating) AS avg FROM reviews GROUP BY method ORDER BY n DESC")]
    out["by_process"] = [dict(r) for r in con.execute(
        """SELECT lower(c.process) AS name, count(v.id) AS n, avg(v.rating) AS avg FROM coffees c
           JOIN bags b ON b.coffee_id=c.id JOIN reviews v ON v.bag_id=b.id
           WHERE c.process IS NOT NULL GROUP BY 1 ORDER BY n DESC""")]
    out["by_roast"] = [dict(r) for r in con.execute(
        """SELECT lower(c.roast_level) AS name, count(v.id) AS n, avg(v.rating) AS avg FROM coffees c
           JOIN bags b ON b.coffee_id=c.id JOIN reviews v ON v.bag_id=b.id
           WHERE c.roast_level IS NOT NULL GROUP BY 1 ORDER BY n DESC""")]
    ages = {}
    for r in con.execute("SELECT b.roast_date, v.brewed_on, v.rating FROM reviews v JOIN bags b ON b.id=v.bag_id "
                         "WHERE b.roast_date IS NOT NULL AND v.brewed_on IS NOT NULL AND v.rating IS NOT NULL"):
        lab = bucket_age(days_between(r[0], r[1]))
        if lab:
            ages.setdefault(lab, []).append(r[2])
    order = ["0–6 days", "7–13", "14–20", "21–34", "35+"]
    out["by_rest"] = [{"name": k, "n": len(ages[k]), "avg": sum(ages[k]) / len(ages[k])} for k in order if k in ages]
    out["top_coffees"] = coffee_rows("avg_rating IS NOT NULL", (), "avg_rating DESC, review_count DESC", 10)
    out["top_roasters"] = [dict(r) for r in con.execute(
        """SELECT r.id, r.name, r.city, r.country, count(DISTINCT b.id) AS bags, avg(v.rating) AS avg
           FROM roasters r JOIN coffees c ON c.roaster_id=r.id JOIN bags b ON b.coffee_id=c.id
           LEFT JOIN reviews v ON v.bag_id=b.id GROUP BY r.id ORDER BY bags DESC, avg DESC LIMIT 12""")]
    out["places"] = [dict(r) for r in con.execute(
        """SELECT city, country, count(*) AS n FROM bags WHERE city IS NOT NULL AND online=0
           GROUP BY lower(city), lower(coalesce(country,'')) ORDER BY n DESC, city LIMIT 20""")]
    out["by_year"] = [dict(r) for r in con.execute(
        "SELECT substr(purchased_on,1,4) AS name, count(*) AS n FROM bags WHERE purchased_on IS NOT NULL "
        "GROUP BY 1 ORDER BY 1")]
    out["flavors"] = top_flavors(20)
    if private:
        out["spend"] = [dict(r) for r in con.execute(
            "SELECT coalesce(currency, ?) AS currency, sum(price) AS total, count(*) AS bags, "
            "sum(price)/nullif(sum(weight_g),0)*453.592 AS per_lb FROM bags WHERE price IS NOT NULL "
            "GROUP BY 1 ORDER BY total DESC", (DEFAULT_CURRENCY,))]
        out["grind"] = [dict(r) for r in con.execute(
            """SELECT method, grinder, grind, count(*) AS n, avg(rating) AS avg FROM reviews
               WHERE grind IS NOT NULL AND rating IS NOT NULL GROUP BY method, grinder, grind
               HAVING n >= 1 ORDER BY method, avg DESC""")]
    return out


def stats(private):
    return jsonify(stats_data(private))


for prefix, private in (("/api", False), ("/admin/api", True)):
    def _wrap(fn, private=private):
        def f(*a, **kw):
            if not private and not PUBLIC_VIEW:
                return make_err(403, "This catalog is private")
            return fn(*a, private=private, **kw)
        return editor_required(f) if private else f
    app.add_url_rule(f"{prefix}/feed", f"feed_{private}", _wrap(feed))
    app.add_url_rule(f"{prefix}/coffees", f"coffees_{private}", _wrap(list_coffees))
    app.add_url_rule(f"{prefix}/coffees/<int:coffee_id>", f"coffee_{private}", _wrap(get_coffee))
    app.add_url_rule(f"{prefix}/bags/<int:bag_id>", f"bag_{private}", _wrap(get_bag))
    app.add_url_rule(f"{prefix}/roasters", f"roasters_{private}", _wrap(list_roasters))
    app.add_url_rule(f"{prefix}/roasters/<int:roaster_id>", f"roaster_{private}", _wrap(get_roaster))
    app.add_url_rule(f"{prefix}/facets", f"facets_{private}", _wrap(facets))
    app.add_url_rule(f"{prefix}/stats", f"stats_{private}", _wrap(stats))


@app.get("/api/me")
def me_public():
    return jsonify(editor=bool(access_identity()), public=PUBLIC_VIEW)


@app.get("/admin/api/me")
@editor_required
def me():
    return jsonify(editor=True, email=g.editor)


@app.get("/admin/api/features")
@editor_required
def features():
    return jsonify(label_reading=bool(ANTHROPIC_API_KEY), mcp_private=bool(MCP_TOKEN), mcp_public=MCP_PUBLIC,
                   public_view=PUBLIC_VIEW, default_grinder=DEFAULT_GRINDER, default_currency=DEFAULT_CURRENCY)


@app.get("/admin/api/shelf")
@editor_required
def shelf():
    rows = db().execute("""SELECT b.*, c.name AS coffee_name, c.origin, c.process, r.name AS roaster_name,
              (SELECT group_concat(method, '|') FROM reviews WHERE bag_id=b.id) AS methods_done
            FROM bags b JOIN coffees c ON c.id=b.coffee_id JOIN roasters r ON r.id=c.roaster_id
            WHERE b.finished_on IS NULL ORDER BY b.opened_on IS NULL, b.roast_date, b.purchased_on""").fetchall()
    ph = photos_for([r["id"] for r in rows])
    out = []
    for r in rows:
        d = bag_out(r, True, ph)
        d["methods_done"] = (r["methods_done"] or "").split("|") if r["methods_done"] else []
        out.append(d)
    return jsonify(bags=out)


@app.get("/admin/api/map")
@editor_required
def map_points():
    rows = db().execute("""SELECT b.id, b.lat, b.lng, b.shop, b.city, b.country, b.purchased_on, b.trip,
              c.id AS coffee_id, c.name AS coffee_name, r.name AS roaster_name,
              (SELECT max(rating) FROM reviews WHERE bag_id=b.id) AS best
            FROM bags b JOIN coffees c ON c.id=b.coffee_id JOIN roasters r ON r.id=c.roaster_id
            WHERE b.lat IS NOT NULL AND b.lng IS NOT NULL""").fetchall()
    ph = photos_for([r["id"] for r in rows])
    return jsonify(points=[dict(r, photo=(ph.get(r["id"]) or [{}])[0].get("thumb")) for r in rows])


# ========================================================================= photos
def store_image(raw, prefix):
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img).convert("RGB")
    if img.width < 60 or img.height < 60:
        raise ValueError("image too small")
    name = f"{prefix}-{uuid.uuid4().hex[:10]}.jpg"
    big = img.copy()
    big.thumbnail((1600, 1600))
    big.save(PHOTO_DIR / name, "JPEG", quality=84, optimize=True)
    small = img.copy()
    small.thumbnail((520, 520))
    small.save(PHOTO_DIR / ("t-" + name), "JPEG", quality=80, optimize=True)
    return name


def remove_image(name):
    (PHOTO_DIR / name).unlink(missing_ok=True)
    (PHOTO_DIR / ("t-" + name)).unlink(missing_ok=True)


@app.post("/admin/api/uploads")
@editor_required
def upload_staged():
    """Photos taken before the bag exists (add flow). Returns tokens to attach on save or read with Claude."""
    files = request.files.getlist("file")
    if not files:
        return make_err(400, "No image")
    out = []
    for f in files:
        try:
            out.append(store_image(f.read(), "tmp"))
        except Exception as e:  # noqa: BLE001
            return make_err(400, f"Couldn't read that image: {e}")
    return jsonify(uploads=out)


def attach_uploads(bag_id, tokens):
    con = db()
    pos = con.execute("SELECT coalesce(max(pos)+1, 0) FROM photos WHERE bag_id=?", (bag_id,)).fetchone()[0]
    for t in tokens or []:
        if not re.fullmatch(r"tmp-[0-9a-f]{10}\.jpg", t or "") or not (PHOTO_DIR / t).exists():
            continue
        name = f"{bag_id}-{t[4:]}"
        (PHOTO_DIR / t).rename(PHOTO_DIR / name)
        if (PHOTO_DIR / ("t-" + t)).exists():
            (PHOTO_DIR / ("t-" + t)).rename(PHOTO_DIR / ("t-" + name))
        con.execute("INSERT INTO photos(bag_id, file, pos) VALUES (?,?,?)", (bag_id, name, pos))
        pos += 1


@app.post("/admin/api/bags/<int:bag_id>/photos")
@editor_required
def upload_bag_photos(bag_id):
    if not db().execute("SELECT 1 FROM bags WHERE id=?", (bag_id,)).fetchone():
        abort(404)
    con = db()
    pos = con.execute("SELECT coalesce(max(pos)+1, 0) FROM photos WHERE bag_id=?", (bag_id,)).fetchone()[0]
    for f in request.files.getlist("file"):
        try:
            name = store_image(f.read(), str(bag_id))
        except Exception as e:  # noqa: BLE001
            return make_err(400, f"Couldn't read that image: {e}")
        con.execute("INSERT INTO photos(bag_id, file, pos) VALUES (?,?,?)", (bag_id, name, pos))
        pos += 1
    con.commit()
    return get_bag(bag_id, private=True)


@app.delete("/admin/api/photos/<int:pid>")
@editor_required
def delete_photo(pid):
    r = db().execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        abort(404)
    db().execute("DELETE FROM photos WHERE id=?", (pid,))
    db().commit()
    remove_image(r["file"])
    return jsonify(ok=True)


@app.post("/admin/api/photos/<int:pid>/cover")
@editor_required
def cover_photo(pid):
    r = db().execute("SELECT * FROM photos WHERE id=?", (pid,)).fetchone()
    if not r:
        abort(404)
    db().execute("UPDATE photos SET pos=pos+1 WHERE bag_id=?", (r["bag_id"],))
    db().execute("UPDATE photos SET pos=0 WHERE id=?", (pid,))
    db().commit()
    return jsonify(ok=True)


# ===================================================================== write: roasters
ROASTER_FIELDS = ("name", "city", "region", "country", "website", "notes")
COFFEE_FIELDS = ("name", "origin", "region", "producer", "varietal", "process", "altitude", "roast_level",
                 "roaster_notes", "is_blend", "description")


def normalize_url(u):
    u = clean(u)
    if u and not re.match(r"https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def roaster_values(data):
    vals = {k: clean(data[k]) for k in ROASTER_FIELDS if k in data}
    if "website" in vals:
        vals["website"] = normalize_url(vals["website"])
    return vals


def coffee_values(data):
    vals = {}
    for k in COFFEE_FIELDS:
        if k not in data:
            continue
        vals[k] = (1 if data[k] else 0) if k == "is_blend" else clean(data[k])
    if vals.get("process"):
        vals["process"] = vals["process"].lower()
    if vals.get("roast_level"):
        vals["roast_level"] = vals["roast_level"].lower()
    return vals


def find_or_create_roaster(data):
    con = db()
    if data.get("id"):
        r = con.execute("SELECT id FROM roasters WHERE id=?", (int(data["id"]),)).fetchone()
        if not r:
            abort(make_err(400, "That roaster no longer exists"))
        rid = r["id"]
        vals = {k: v for k, v in roaster_values(data).items() if v and k != "name"}
        if vals:  # fill gaps only
            cur = dict(con.execute("SELECT * FROM roasters WHERE id=?", (rid,)).fetchone())
            gaps = {k: v for k, v in vals.items() if not cur.get(k)}
            if gaps:
                con.execute(f"UPDATE roasters SET {','.join(f'{k}=?' for k in gaps)} WHERE id=?", [*gaps.values(), rid])
        return rid
    vals = roaster_values(data)
    if not vals.get("name"):
        abort(make_err(400, "Roaster name is required"))
    r = con.execute("SELECT id FROM roasters WHERE name=? COLLATE NOCASE", (vals["name"],)).fetchone()
    if r:
        return r["id"]
    cur = con.execute(f"INSERT INTO roasters({','.join(vals)}) VALUES ({','.join('?' * len(vals))})",
                      list(vals.values()))
    return cur.lastrowid


def find_or_create_coffee(roaster_id, data):
    con = db()
    if data.get("id"):
        r = con.execute("SELECT id, roaster_id FROM coffees WHERE id=?", (int(data["id"]),)).fetchone()
        if not r:
            abort(make_err(400, "That coffee no longer exists"))
        vals = {k: v for k, v in coffee_values(data).items() if v not in (None, 0) and k != "name"}
        if vals:
            cur = dict(con.execute("SELECT * FROM coffees WHERE id=?", (r["id"],)).fetchone())
            gaps = {k: v for k, v in vals.items() if not cur.get(k)}
            if gaps:
                con.execute(f"UPDATE coffees SET {','.join(f'{k}=?' for k in gaps)} WHERE id=?", [*gaps.values(), r["id"]])
        return r["id"]
    vals = coffee_values(data)
    if not vals.get("name"):
        abort(make_err(400, "Coffee name is required"))
    r = con.execute("SELECT id FROM coffees WHERE roaster_id=? AND name=? COLLATE NOCASE",
                    (roaster_id, vals["name"])).fetchone()
    if r:
        return r["id"]
    cur = con.execute(f"INSERT INTO coffees(roaster_id, {','.join(vals)}) VALUES (?, {','.join('?' * len(vals))})",
                      [roaster_id, *vals.values()])
    return cur.lastrowid


@app.put("/admin/api/roasters/<int:rid>")
@editor_required
def update_roaster(rid):
    con = db()
    if not con.execute("SELECT 1 FROM roasters WHERE id=?", (rid,)).fetchone():
        abort(404)
    vals = roaster_values(request.get_json(force=True))
    if "name" in vals and not vals["name"]:
        return make_err(400, "Roaster name is required")
    if vals.get("name"):
        dup = con.execute("SELECT id FROM roasters WHERE name=? COLLATE NOCASE AND id<>?", (vals["name"], rid)).fetchone()
        if dup:
            return make_err(409, "Another roaster already has that name — use Merge instead", roaster_id=dup["id"])
    if vals:
        con.execute(f"UPDATE roasters SET {','.join(f'{k}=?' for k in vals)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*vals.values(), rid])
        con.commit()
    return get_roaster(rid, private=True)


@app.post("/admin/api/roasters/<int:rid>/merge")
@editor_required
def merge_roaster(rid):
    into = int(request.get_json(force=True).get("into") or 0)
    con = db()
    if rid == into or not con.execute("SELECT 1 FROM roasters WHERE id=?", (into,)).fetchone():
        return make_err(400, "Pick a different roaster to merge into")
    src = dict(con.execute("SELECT * FROM roasters WHERE id=?", (rid,)).fetchone() or abort(404))
    dst = dict(con.execute("SELECT * FROM roasters WHERE id=?", (into,)).fetchone())
    gaps = {k: src[k] for k in ROASTER_FIELDS if k != "name" and src.get(k) and not dst.get(k)}
    if gaps:
        con.execute(f"UPDATE roasters SET {','.join(f'{k}=?' for k in gaps)} WHERE id=?", [*gaps.values(), into])
    for c in con.execute("SELECT id, name FROM coffees WHERE roaster_id=?", (rid,)).fetchall():
        same = con.execute("SELECT id FROM coffees WHERE roaster_id=? AND name=? COLLATE NOCASE",
                           (into, c["name"])).fetchone()
        if same:
            _merge_coffee(con, c["id"], same["id"])
        else:
            con.execute("UPDATE coffees SET roaster_id=? WHERE id=?", (into, c["id"]))
    con.execute("DELETE FROM roasters WHERE id=?", (rid,))
    con.commit()
    return jsonify(ok=True, roaster_id=into)


@app.delete("/admin/api/roasters/<int:rid>")
@editor_required
def delete_roaster(rid):
    con = db()
    if con.execute("SELECT 1 FROM coffees WHERE roaster_id=?", (rid,)).fetchone():
        return make_err(409, "This roaster still has coffees. Delete or move them first.")
    con.execute("DELETE FROM roasters WHERE id=?", (rid,))
    con.commit()
    return jsonify(ok=True)


# ====================================================================== write: coffees
@app.put("/admin/api/coffees/<int:cid>")
@editor_required
def update_coffee(cid):
    con = db()
    row = con.execute("SELECT * FROM coffees WHERE id=?", (cid,)).fetchone()
    if not row:
        abort(404)
    data = request.get_json(force=True)
    vals = coffee_values(data)
    if "name" in vals and not vals["name"]:
        return make_err(400, "Coffee name is required")
    if data.get("roaster_id"):
        vals["roaster_id"] = int(data["roaster_id"])
    if vals:
        con.execute(f"UPDATE coffees SET {','.join(f'{k}=?' for k in vals)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*vals.values(), cid])
        con.commit()
    return get_coffee(cid, private=True)


def _merge_coffee(con, src, dst):
    gaps_src = dict(con.execute("SELECT * FROM coffees WHERE id=?", (src,)).fetchone())
    dst_row = dict(con.execute("SELECT * FROM coffees WHERE id=?", (dst,)).fetchone())
    gaps = {k: gaps_src[k] for k in COFFEE_FIELDS if k not in ("name", "is_blend") and gaps_src.get(k) and not dst_row.get(k)}
    if gaps:
        con.execute(f"UPDATE coffees SET {','.join(f'{k}=?' for k in gaps)} WHERE id=?", [*gaps.values(), dst])
    con.execute("UPDATE bags SET coffee_id=? WHERE coffee_id=?", (dst, src))
    con.execute("DELETE FROM coffees WHERE id=?", (src,))


@app.post("/admin/api/coffees/<int:cid>/merge")
@editor_required
def merge_coffee(cid):
    into = int(request.get_json(force=True).get("into") or 0)
    con = db()
    if cid == into or not con.execute("SELECT 1 FROM coffees WHERE id=?", (into,)).fetchone():
        return make_err(400, "Pick a different coffee to merge into")
    if not con.execute("SELECT 1 FROM coffees WHERE id=?", (cid,)).fetchone():
        abort(404)
    _merge_coffee(con, cid, into)
    con.commit()
    return jsonify(ok=True, coffee_id=into)


@app.delete("/admin/api/coffees/<int:cid>")
@editor_required
def delete_coffee(cid):
    con = db()
    files = [r[0] for r in con.execute(
        "SELECT p.file FROM photos p JOIN bags b ON b.id=p.bag_id WHERE b.coffee_id=?", (cid,))]
    con.execute("DELETE FROM coffees WHERE id=?", (cid,))
    con.execute("DELETE FROM roasters WHERE id NOT IN (SELECT roaster_id FROM coffees) "
                "AND id NOT IN (SELECT id FROM roasters WHERE notes IS NOT NULL OR website IS NOT NULL)")
    con.commit()
    for f in files:
        remove_image(f)
    return jsonify(ok=True)


# ========================================================================= write: bags
BAG_FIELDS = ("purchased_on", "shop", "city", "region", "country", "lat", "lng", "trip", "online", "price",
              "currency", "weight_g", "weight", "roast_date", "opened_on", "finished_on", "notes")


def bag_values(data):
    vals = {}
    for k in BAG_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in ("purchased_on", "roast_date", "opened_on", "finished_on"):
            v = iso_date(v, k.replace("_", " "))
        elif k == "lat":
            v = num(v, -90, 90, "Latitude")
        elif k == "lng":
            v = num(v, -180, 180, "Longitude")
        elif k == "price":
            v = num(re.sub(r"[^\d.,]", "", str(v or "")) or None, 0, 100000, "Price")
        elif k == "online":
            v = 1 if v else 0
        elif k in ("weight", "weight_g"):
            k, v = "weight_g", parse_weight(v)
        elif k == "currency":
            v = (clean(v) or DEFAULT_CURRENCY).upper()[:3]
        else:
            v = clean(v)
        vals[k] = v
    if vals.get("online"):
        vals.update(lat=None, lng=None)
    return vals


def geocode_place(city, region, country):
    """Best-effort city -> lat/lng (OpenStreetMap Nominatim) so the map works without GPS."""
    q = ", ".join(x for x in (city, region, country) if x)
    if not q:
        return None
    try:
        res = http_json("https://nominatim.openstreetmap.org/search?" +
                        urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1}), timeout=6)
        if res:
            return float(res[0]["lat"]), float(res[0]["lon"])
    except Exception as e:  # noqa: BLE001
        app.logger.info("geocode failed for %s: %s", q, e)
    return None


@app.post("/admin/api/bags")
@editor_required
def create_bag():
    """Body: {roaster: {id | name, city, ...}, coffee: {id | name, origin, ...}, bag: {...}, uploads: [tokens]}"""
    data = request.get_json(force=True)
    con = db()
    rid = find_or_create_roaster(data.get("roaster") or {})
    cid = find_or_create_coffee(rid, data.get("coffee") or {})
    vals = bag_values(data.get("bag") or {})
    vals.setdefault("purchased_on", today())
    vals.setdefault("currency", DEFAULT_CURRENCY)
    if not vals.get("online") and vals.get("lat") is None and vals.get("city"):
        ll = geocode_place(vals.get("city"), vals.get("region"), vals.get("country"))
        if ll:
            vals["lat"], vals["lng"] = ll
    vals["added_by"] = who()
    cur = con.execute(f"INSERT INTO bags(coffee_id, {','.join(vals)}) VALUES (?, {','.join('?' * len(vals))})",
                      [cid, *vals.values()])
    bid = cur.lastrowid
    attach_uploads(bid, data.get("uploads"))
    con.commit()
    return get_bag(bid, private=True), 201


@app.put("/admin/api/bags/<int:bag_id>")
@editor_required
def update_bag(bag_id):
    con = db()
    row = con.execute("SELECT * FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not row:
        abort(404)
    data = request.get_json(force=True)
    vals = bag_values(data)
    if data.get("coffee_id"):
        if not con.execute("SELECT 1 FROM coffees WHERE id=?", (int(data["coffee_id"]),)).fetchone():
            return make_err(400, "No such coffee")
        vals["coffee_id"] = int(data["coffee_id"])
    moved = any(k in vals and vals[k] != row[k] for k in ("city", "country"))
    if moved and "lat" not in vals and not (vals.get("online", row["online"])):
        ll = geocode_place(vals.get("city", row["city"]), vals.get("region", row["region"]),
                           vals.get("country", row["country"]))
        vals["lat"], vals["lng"] = ll if ll else (None, None)
    if vals:
        con.execute(f"UPDATE bags SET {','.join(f'{k}=?' for k in vals)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*vals.values(), bag_id])
    attach_uploads(bag_id, data.get("uploads"))
    con.commit()
    return get_bag(bag_id, private=True)


@app.delete("/admin/api/bags/<int:bag_id>")
@editor_required
def delete_bag(bag_id):
    con = db()
    row = con.execute("SELECT coffee_id FROM bags WHERE id=?", (bag_id,)).fetchone()
    if not row:
        abort(404)
    files = [r[0] for r in con.execute("SELECT file FROM photos WHERE bag_id=?", (bag_id,))]
    con.execute("DELETE FROM bags WHERE id=?", (bag_id,))
    left = con.execute("SELECT count(*) FROM bags WHERE coffee_id=?", (row["coffee_id"],)).fetchone()[0]
    con.commit()
    for f in files:
        remove_image(f)
    return jsonify(ok=True, coffee_id=row["coffee_id"], coffee_has_bags=bool(left))


# ====================================================================== write: reviews
REVIEW_FIELDS = ("rating", "dose_g", "water_g", "grinder", "grind", "brewer", "temp", "brew_time", "recipe", "flavors", "notes",
                 "brewed_on")


def canonical_method(name):
    name = clean(name)
    if not name:
        abort(make_err(400, "Pick a brew method"))
    r = db().execute("SELECT name FROM methods WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if r:
        return r[0]
    used = db().execute("SELECT method FROM reviews WHERE lower(method)=lower(?) LIMIT 1", (name,)).fetchone()
    if used:
        return used[0]
    db().execute("INSERT INTO methods(name, pos) VALUES (?, (SELECT coalesce(max(pos)+1,0) FROM methods))", (name,))
    return name


def review_values(data):
    vals = {}
    for k in REVIEW_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k == "rating":
            v = num(v, 0, 10, "Rating")
            if v is not None:
                v = round(v * 2) / 2
        elif k in ("dose_g", "water_g"):
            v = num(v, 0, 5000, "Dose" if k == "dose_g" else "Water")
        elif k == "brewed_on":
            v = iso_date(v, "Brew date")
        elif k == "flavors":
            v = ", ".join(dict.fromkeys(x.lower() for x in split_list(v if isinstance(v, str) else ", ".join(v or []))))
            v = v or None
        else:
            v = clean(v)
        vals[k] = v
    return vals


def upsert_review(bag_id, method, data):
    con = db()
    if not con.execute("SELECT 1 FROM bags WHERE id=?", (bag_id,)).fetchone():
        abort(make_err(404, "No such bag"))
    method = canonical_method(method)
    vals = review_values(data)
    existing = con.execute("SELECT id FROM reviews WHERE bag_id=? AND method=?", (bag_id, method)).fetchone()
    if existing:
        if vals:
            con.execute(f"UPDATE reviews SET {','.join(f'{k}=?' for k in vals)}, reviewed_by=coalesce(?, reviewed_by), "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?", [*vals.values(), who(), existing["id"]])
        rid = existing["id"]
    else:
        vals.setdefault("brewed_on", today())
        cur = con.execute(f"INSERT INTO reviews(bag_id, method, reviewed_by, {','.join(vals)}) "
                          f"VALUES (?,?,?, {','.join('?' * len(vals))})", [bag_id, method, who(), *vals.values()])
        rid = cur.lastrowid
    # brewing a sealed bag means it's open
    con.execute("UPDATE bags SET opened_on=coalesce(opened_on, ?) WHERE id=?", (vals.get("brewed_on") or today(), bag_id))
    con.commit()
    return rid, method, not existing


@app.put("/admin/api/bags/<int:bag_id>/reviews")
@editor_required
def put_review(bag_id):
    data = request.get_json(force=True)
    old = clean(data.get("old_method"))
    new = clean(data.get("method"))
    if old and new and old.lower() != new.lower():  # method changed while editing
        con = db()
        newc = canonical_method(new)
        if con.execute("SELECT 1 FROM reviews WHERE bag_id=? AND method=?", (bag_id, newc)).fetchone():
            return make_err(409, f"This bag already has a {newc} review — edit that one instead")
        con.execute("UPDATE reviews SET method=? WHERE bag_id=? AND method=? COLLATE NOCASE", (newc, bag_id, old))
    rid, method, created = upsert_review(bag_id, new, data)
    row = db().execute("SELECT v.*, b.roast_date FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE v.id=?", (rid,)).fetchone()
    return jsonify(review=review_out(row, True), created=created), (201 if created else 200)


@app.delete("/admin/api/reviews/<int:rid>")
@editor_required
def delete_review(rid):
    db().execute("DELETE FROM reviews WHERE id=?", (rid,))
    db().commit()
    return jsonify(ok=True)


@app.get("/admin/api/last-recipe")
@editor_required
def last_recipe():
    """Prefill for a new review: the last time this method was used (same coffee first)."""
    method, coffee_id = request.args.get("method", ""), request.args.get("coffee_id")
    row = None
    if coffee_id:
        row = db().execute("""SELECT v.* FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE v.method=? COLLATE NOCASE
                              AND b.coffee_id=? ORDER BY v.updated_at DESC LIMIT 1""", (method, int(coffee_id))).fetchone()
    if not row:
        row = db().execute("SELECT * FROM reviews WHERE method=? COLLATE NOCASE ORDER BY updated_at DESC LIMIT 1",
                           (method,)).fetchone()
    keys = ("dose_g", "water_g", "grinder", "grind", "brewer", "temp", "brew_time", "recipe")
    preset = db().execute("SELECT grinder, grind, brewer, recipe FROM methods WHERE name=? COLLATE NOCASE",
                          (method,)).fetchone()
    out = {k: row[k] for k in keys} if row else {}
    if not row and preset:
        out = {k: preset[k] for k in ("grinder", "grind", "brewer", "recipe")}
    elif preset:  # older reviews may lack equipment
        for k in ("grinder", "brewer"):
            out[k] = out.get(k) or preset[k]
    out["grinder"] = out.get("grinder") or DEFAULT_GRINDER
    out["source"] = "last" if row else ("preset" if preset and any(preset) else "default")
    return jsonify(out)


# ====================================================================== methods
@app.put("/admin/api/methods")
@editor_required
def set_methods():
    """Body: {methods: ["Drip", ...] or [{name, grinder, grind, brewer, recipe}, ...]} in display order."""
    rows, seen = [], set()
    for m in request.get_json(force=True).get("methods", []):
        m = m if isinstance(m, dict) else {"name": m}
        name = clean(m.get("name"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        rows.append((name, {k: clean(m.get(k)) for k in ("grinder", "grind", "brewer", "recipe")}, isinstance(m, dict) and len(m) > 1))
    if not rows:
        return make_err(400, "Keep at least one method")
    con = db()
    con.execute("UPDATE methods SET hidden=1")
    for i, (n, p, has_preset) in enumerate(rows):
        con.execute("INSERT INTO methods(name, pos, hidden) VALUES (?,?,0) "
                    "ON CONFLICT(name) DO UPDATE SET name=excluded.name, pos=excluded.pos, hidden=0", (n, i))
        if has_preset:
            con.execute("UPDATE methods SET grinder=?, grind=?, brewer=?, recipe=? WHERE name=? COLLATE NOCASE",
                        (p["grinder"], p["grind"], p["brewer"], p["recipe"], n))
    con.commit()
    return get_methods()


@app.get("/admin/api/methods")
@editor_required
def get_methods():
    return jsonify(methods=[dict(r) for r in db().execute(
        "SELECT name, grinder, grind, brewer, recipe FROM methods WHERE hidden=0 ORDER BY pos, name")])


# ===================================================================== places
@app.get("/admin/api/place")
@editor_required
def reverse_place():
    """lat/lng from the phone -> shop name, city, region, country (OpenStreetMap)."""
    lat = num(request.args.get("lat"), -90, 90, "Latitude")
    lng = num(request.args.get("lng"), -180, 180, "Longitude")
    if lat is None or lng is None:
        return make_err(400, "lat and lng are required")
    out = {"lat": round(lat, 6), "lng": round(lng, 6)}
    try:
        d = http_json("https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
            {"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 18, "addressdetails": 1}))
        a = d.get("address") or {}
        out["city"] = a.get("city") or a.get("town") or a.get("village") or a.get("suburb") or a.get("county")
        out["region"] = a.get("state") or a.get("province") or a.get("region")
        out["country"] = a.get("country")
        kind = d.get("category") or d.get("class")
        if d.get("name") and kind in ("amenity", "shop", "craft", "tourism", "building"):
            out["shop"] = d["name"]
    except Exception as e:  # noqa: BLE001
        out["warning"] = f"Couldn't look up the place name ({e}); the GPS point is still saved."
    # shops I have bought from before, nearest first (within ~300 m)
    near = []
    for r in db().execute("SELECT shop, city, region, country, lat, lng FROM bags WHERE shop IS NOT NULL AND lat IS NOT NULL "
                          "GROUP BY shop, city"):
        dist = ((r["lat"] - lat) ** 2 + ((r["lng"] - lng) * 0.8) ** 2) ** 0.5 * 111000
        if dist < 300:
            near.append((dist, dict(r)))
    out["known"] = [d for _, d in sorted(near, key=lambda x: x[0])[:3]]
    return jsonify(out)


# ===================================================================== label reading (Claude)
LABEL_PROMPT = """These are photos of a bag of coffee beans (front, back, and any stickers). Read the label and fill in what is printed.

Rules:
- Only use what is printed on the bag or its stickers. Don't invent or look anything up. Use null when it isn't shown.
- roaster.name is the roasting company, not the farm. roaster.city/region/country/website only if printed (an address counts).
- coffee.name: the offering's name as the roaster prints it (e.g. "Ethiopia Guji Hambela" or "Hair Bender"). If the bag only names the origin, use that.
- coffee.origin: country or countries of origin, comma separated. coffee.region: region/area. coffee.producer: farm, estate, co-op, washing station or producer.
- coffee.varietal: varieties, comma separated. coffee.process: washed, natural, honey, anaerobic natural, wet-hulled, etc. (lowercase).
- coffee.roast_level: light, medium-light, medium, medium-dark, dark, or espresso/filter if that's all it says.
- coffee.roaster_notes: the tasting notes on the bag, comma separated, lowercase. coffee.is_blend: true if it's a blend.
- coffee.altitude as printed (e.g. "1,900–2,100 masl").
- bag.roast_date as YYYY-MM-DD if a roast date is printed (not a best-by date). Also copy it exactly as printed in roast_date_printed. If a date is ambiguous (e.g. 03/04), assume the roaster's country convention and say so in "uncertain".
- bag.weight exactly as printed (e.g. "12 oz", "340 g"). bag.price only if a price sticker shows it, as a number; bag.currency as a 3-letter code if you can tell.
- "uncertain": a short list of fields you had to guess.

Reply with only JSON:
{"roaster": {"name", "city", "region", "country", "website"},
 "coffee": {"name", "origin", "region", "producer", "varietal", "process", "altitude", "roast_level", "roaster_notes", "is_blend"},
 "bag": {"roast_date", "roast_date_printed", "weight", "price", "currency"},
 "uncertain": [string]}"""


def label_jpeg(path):
    from PIL import Image
    img = Image.open(path).convert("RGB")
    img.thumbnail((1568, 1568))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86)
    return out.getvalue()


def call_claude(images, prompt):
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                            "data": base64.b64encode(im).decode()}} for im in images]
    content.append({"type": "text", "text": prompt})
    body = {"model": ANTHROPIC_MODEL, "max_tokens": 2000, "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(), headers={
        "x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Claude API error {e.code}: {e.read().decode(errors='replace')[:300]}")
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def parse_label(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("No JSON in the response")
    data = json.loads(m.group())
    out = {"roaster": {}, "coffee": {}, "bag": {}, "uncertain": [str(x) for x in data.get("uncertain") or []][:10]}
    for sect, keys in (("roaster", ROASTER_FIELDS), ("coffee", COFFEE_FIELDS),
                       ("bag", ("roast_date", "roast_date_printed", "weight", "price", "currency"))):
        src = data.get(sect) or {}
        for k in keys:
            v = src.get(k)
            if k == "is_blend":
                out[sect][k] = bool(v)
            elif v not in (None, "", []):
                out[sect][k] = ", ".join(map(str, v)) if isinstance(v, list) else str(v).strip()
    rd = out["bag"].get("roast_date")
    if rd:
        try:
            dt.date.fromisoformat(rd)
        except ValueError:
            out["bag"].pop("roast_date")
    return out


def match_existing(fields):
    """Suggest an existing roaster and coffee for what the label says."""
    con = db()
    rname = fields.get("roaster", {}).get("name")
    best_r = None
    if rname:
        for r in con.execute("SELECT id, name, city FROM roasters"):
            s = sim(rname, r["name"])
            if s >= 0.72 and (not best_r or s > best_r["score"]):
                best_r = {"id": r["id"], "name": r["name"], "city": r["city"], "score": round(s, 2)}
    best_c = None
    cname = fields.get("coffee", {}).get("name")
    if best_r and cname:
        for c in con.execute("SELECT id, name, origin, process FROM coffees WHERE roaster_id=?", (best_r["id"],)):
            s = sim(cname, c["name"])
            if fields["coffee"].get("process") and c["process"] and fold(fields["coffee"]["process"]) != fold(c["process"]):
                s -= 0.15
            if s >= 0.7 and (not best_c or s > best_c["score"]):
                best_c = {"id": c["id"], "name": c["name"], "origin": c["origin"], "process": c["process"],
                          "score": round(s, 2)}
    return best_r, best_c


@app.post("/admin/api/label")
@editor_required
def read_label():
    if not ANTHROPIC_API_KEY:
        return make_err(503, "Label reading needs ANTHROPIC_API_KEY in /etc/conf.d/roasted")
    tokens = [t for t in (request.get_json(force=True).get("uploads") or [])
              if re.fullmatch(r"(tmp|\d+)-[0-9a-f]{10}\.jpg", t or "") and (PHOTO_DIR / t).exists()][:4]
    if not tokens:
        return make_err(400, "Take a photo of the bag first")
    try:
        fields = parse_label(call_claude([label_jpeg(PHOTO_DIR / t) for t in tokens], LABEL_PROMPT))
    except Exception as e:  # noqa: BLE001
        return make_err(502, f"Couldn't read the label: {e}")
    r, c = match_existing(fields)
    return jsonify(fields=fields, roaster_match=r, coffee_match=c)


@app.get("/admin/api/match")
@editor_required
def match_route():
    fields = {"roaster": {"name": request.args.get("roaster", "")}, "coffee": {"name": request.args.get("coffee", "")}}
    r, c = match_existing(fields)
    return jsonify(roaster_match=r, coffee_match=c)


# ===================================================================== export
def export_rows():
    return [dict(r) for r in db().execute("""
        SELECT r.name AS roaster, r.city AS roaster_city, r.country AS roaster_country, c.name AS coffee,
          c.origin, c.region, c.producer, c.varietal, c.process, c.roast_level, c.roaster_notes,
          b.id AS bag_id, b.purchased_on, b.shop, b.city, b.country, b.trip, b.price, b.currency, b.weight_g,
          b.roast_date, b.opened_on, b.finished_on,
          v.method, v.rating, v.dose_g, v.water_g, v.grinder, v.grind, v.brewer, v.temp, v.brew_time, v.flavors, v.notes, v.brewed_on
        FROM bags b JOIN coffees c ON c.id=b.coffee_id JOIN roasters r ON r.id=c.roaster_id
        LEFT JOIN reviews v ON v.bag_id=b.id ORDER BY b.purchased_on, b.id, v.method""")]


@app.get("/admin/api/export.csv")
@editor_required
def export_csv():
    rows = export_rows()
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=roasted.csv"})


@app.get("/admin/api/export.json")
@editor_required
def export_json():
    con = db()
    body = {"exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "roasters": [dict(r) for r in con.execute("SELECT * FROM roasters ORDER BY id")],
            "coffees": [dict(r) for r in con.execute("SELECT * FROM coffees ORDER BY id")],
            "bags": [dict(r) for r in con.execute("SELECT * FROM bags ORDER BY id")],
            "photos": [dict(r) for r in con.execute("SELECT * FROM photos ORDER BY id")],
            "reviews": [dict(r) for r in con.execute("SELECT * FROM reviews ORDER BY id")],
            "methods": [dict(r) for r in con.execute("SELECT * FROM methods ORDER BY pos")]}
    return Response(json.dumps(body, ensure_ascii=False, indent=1), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=roasted.json"})


# ===================================================================== MCP
MCP_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MCP_INSTRUCTIONS = ("Nick's coffee log (Roasted): beans bought from roasters (often while traveling), with one "
                    "review per brew method per bag (rating 0–10, grind, dose, tasting notes). A coffee is a roaster's "
                    "offering; a bag is one purchase of it.")


def _coffee_brief(c):
    keys = ("id", "name", "roaster_name", "roaster_city", "origin", "region", "process", "roast_level",
            "roaster_notes", "bag_count", "review_count", "avg_rating", "best_methods", "last_bought")
    return {k: (round(c[k], 2) if k == "avg_rating" and c.get(k) is not None else c.get(k))
            for k in keys if c.get(k) not in (None, "", [])}


def t_search(a, private):
    where, args = ["1=1"], []
    for tok in re.findall(r"\w+", fold(a.get("query") or "")):
        where.append("(lower(name||' '||roaster_name||' '||coalesce(origin,'')||' '||coalesce(region,'')||' '||"
                     "coalesce(process,'')||' '||coalesce(roaster_notes,'')||' '||coalesce(varietal,'')||' '||"
                     "coalesce(roaster_city,'')) LIKE ?)")
        args.append(f"%{tok}%")
    if a.get("origin"):
        where.append("lower(coalesce(origin,'')) LIKE ?")
        args.append(f"%{fold(a['origin'])}%")
    if a.get("process"):
        where.append("lower(coalesce(process,'')) LIKE ?")
        args.append(f"%{fold(a['process'])}%")
    if a.get("min_rating") is not None:
        where.append("avg_rating >= ?")
        args.append(float(a["min_rating"]))
    limit = max(1, min(int(a.get("limit") or 25), 100))
    rows = coffee_rows(" AND ".join(where), args, "avg_rating IS NULL, avg_rating DESC", limit)
    best = coffee_best_methods([r["id"] for r in rows])
    for r in rows:
        r["best_methods"] = best.get(r["id"], [])[:3]
    return {"count": len(rows), "coffees": [_coffee_brief(r) for r in rows]}


def t_get_coffee(a, private):
    cid = a.get("coffee_id")
    if not cid and a.get("name"):
        rows = coffee_rows("lower(name) LIKE ?", [f"%{fold(a['name'])}%"], "last_bought DESC", 1)
        cid = rows[0]["id"] if rows else None
    if not cid:
        raise ValueError("coffee_id or name is required (no match)")
    c = coffee_detail(int(cid), private)
    for b in c["bags"]:
        b.pop("photos", None)
    return c


def t_recent(a, private):
    limit = max(1, min(int(a.get("limit") or 15), 50))
    rows = db().execute("""SELECT v.*, b.roast_date, c.id AS coffee_id, c.name AS coffee_name, r.name AS roaster_name
        FROM reviews v JOIN bags b ON b.id=v.bag_id JOIN coffees c ON c.id=b.coffee_id JOIN roasters r ON r.id=c.roaster_id
        ORDER BY coalesce(v.brewed_on, v.updated_at) DESC LIMIT ?""", (limit,)).fetchall()
    keys = ("bag_id", "coffee_id", "coffee_name", "roaster_name", "method", "rating", "grinder", "grind", "brewer", "dose_g",
            "water_g", "flavors", "notes", "brewed_on", "days_off_roast")
    return {"reviews": [{k: v for k, v in review_out(r, private, r["roast_date"]).items() if k in keys and v is not None}
                        for r in rows]}


def t_roasters(a, private):
    q = fold(a.get("query") or "")
    rows = [dict(r) for r in db().execute("""SELECT r.id, r.name, r.city, r.region, r.country, r.website,
        count(DISTINCT c.id) AS coffees, count(b.id) AS bags FROM roasters r LEFT JOIN coffees c ON c.roaster_id=r.id
        LEFT JOIN bags b ON b.coffee_id=c.id GROUP BY r.id ORDER BY bags DESC, r.name""")]
    if q:
        rows = [r for r in rows if q in fold(" ".join(str(r.get(k) or "") for k in ("name", "city", "region", "country")))]
    return {"roasters": [{k: v for k, v in r.items() if v not in (None, "")} for r in rows]}


def t_stats(a, private):
    s = stats_data(private)
    s["top_coffees"] = [_coffee_brief(c) for c in s["top_coffees"]]
    return s


def t_shelf(a, private):
    rows = db().execute("""SELECT b.id AS bag_id, c.id AS coffee_id, c.name AS coffee_name, r.name AS roaster_name,
              b.roast_date, b.opened_on, b.purchased_on, b.weight_g,
              (SELECT group_concat(method || ' ' || coalesce(rating,'?'), ', ') FROM reviews WHERE bag_id=b.id) AS reviews
            FROM bags b JOIN coffees c ON c.id=b.coffee_id JOIN roasters r ON r.id=c.roaster_id
            WHERE b.finished_on IS NULL ORDER BY b.roast_date""").fetchall()
    out = []
    for r in rows:
        d = {k: v for k, v in dict(r).items() if v is not None}
        if r["roast_date"]:
            d["days_off_roast"] = days_between(r["roast_date"], today())
        out.append(d)
    return {"open_or_sealed_bags": out}


def t_log_review(a, private):
    bag_id = int(a["bag_id"])
    rid, method, created = upsert_review(bag_id, a.get("method"), {k: a[k] for k in REVIEW_FIELDS if k in a})
    row = db().execute("SELECT v.*, b.roast_date FROM reviews v JOIN bags b ON b.id=v.bag_id WHERE v.id=?", (rid,)).fetchone()
    return {"ok": True, "created": created, "review": {k: v for k, v in review_out(row, True).items() if v is not None}}


def t_finish_bag(a, private):
    bag_id = int(a["bag_id"])
    day = iso_date(a.get("date")) or today()
    con = db()
    if not con.execute("SELECT 1 FROM bags WHERE id=?", (bag_id,)).fetchone():
        raise ValueError("No such bag")
    con.execute("UPDATE bags SET finished_on=?, opened_on=coalesce(opened_on, ?) WHERE id=?", (day, day, bag_id))
    con.commit()
    return {"ok": True, "bag_id": bag_id, "finished_on": day}


_S = lambda **p: {"type": "object", "properties": p}
_REVIEW_PROPS = dict(
    bag_id={"type": "integer"}, method={"type": "string", "description": "Brew method, e.g. V60, AeroPress, Espresso"},
    rating={"type": "number", "description": "0–10, halves allowed"}, grinder={"type": "string"},
    grind={"type": "string", "description": "Grind setting as text, e.g. '21'"},
    brewer={"type": "string", "description": "Machine or brewer, e.g. Breville Precision Brewer"},
    dose_g={"type": "number"}, water_g={"type": "number", "description": "Water (or espresso yield) in grams"},
    temp={"type": "string"}, brew_time={"type": "string"}, recipe={"type": "string"},
    flavors={"type": "string", "description": "Comma-separated tasting notes"}, notes={"type": "string"},
    brewed_on={"type": "string", "description": "YYYY-MM-DD, default today"})
TOOLS = [
    ("search_coffees", False, t_search,
     "Find coffees Nick has bought, by words (name, roaster, origin, region, process, tasting notes, roaster city), "
     "origin, process or minimum average rating. Best rated first.",
     _S(query={"type": "string"}, origin={"type": "string"}, process={"type": "string"},
        min_rating={"type": "number"}, limit={"type": "integer"})),
    ("get_coffee", False, t_get_coffee,
     "A coffee's details, every bag bought (where, when, roast date) and every review per brew method.",
     _S(coffee_id={"type": "integer"}, name={"type": "string"})),
    ("recent_reviews", False, t_recent, "The latest brew reviews, newest first.", _S(limit={"type": "integer"})),
    ("list_roasters", False, t_roasters, "Roasters Nick has bought from, with counts. Optional filter by name or place.",
     _S(query={"type": "string"})),
    ("coffee_stats", False, t_stats,
     "Overall stats: totals, ratings by method / origin / process / roast level / days off roast, top coffees, "
     "places bought (private view adds spend and best grind settings).", _S()),
    ("on_the_shelf", True, t_shelf, "Bags not finished yet (sealed or open), with days off roast and reviews so far.", _S()),
    ("log_review", True, t_log_review,
     "Create or update the review for one brew method of one bag (there is one review per method per bag; "
     "calling again updates it). Use on_the_shelf to find bag_id.",
     {**_S(**_REVIEW_PROPS), "required": ["bag_id", "method"]}),
    ("finish_bag", True, t_finish_bag, "Mark a bag as finished.",
     {**_S(bag_id={"type": "integer"}, date={"type": "string"}), "required": ["bag_id"]}),
]
WRITE_TOOLS = {"log_review", "finish_bag"}


def mcp_auth(token):
    if MCP_TOKEN:
        supplied = token or ""
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            supplied = supplied or auth[7:].strip()
        if supplied and hmac.compare_digest(supplied, MCP_TOKEN):
            return "private"
        if token:
            return None
    return "public" if (MCP_PUBLIC and PUBLIC_VIEW) else None


def rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def rpc_error(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": msg}}


def handle_rpc(msg, private):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
        return rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if mid is None:
        return None
    if method == "initialize":
        want = params.get("protocolVersion")
        return rpc_result(mid, {
            "protocolVersion": want if want in MCP_VERSIONS else MCP_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "roasted", "title": "Roasted — Nick's coffee log", "version": "1.0"},
            "instructions": MCP_INSTRUCTIONS + ("" if private else " (Public view: no prices or private notes.)"),
        })
    if method == "ping":
        return rpc_result(mid, {})
    if method == "tools/list":
        return rpc_result(mid, {"tools": [
            {"name": n, "description": d, "inputSchema": s, "annotations": {"readOnlyHint": n not in WRITE_TOOLS}}
            for n, priv_only, _, d, s in TOOLS if private or not priv_only]})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        tool = next((t for t in TOOLS if t[0] == name and (private or not t[1])), None)
        if not tool:
            return rpc_error(mid, -32602, f"Unknown tool: {name}")
        try:
            g.editor = "mcp"
            res = tool[2](args, private)
            return rpc_result(mid, {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, default=str)}],
                                    "structuredContent": res})
        except Exception as e:  # noqa: BLE001 — tool errors go back to the model
            msg = str(e)
            if getattr(e, "response", None) is not None:  # abort(make_err(...)) from shared helpers
                try:
                    msg = e.response.get_json().get("error", msg)
                except Exception:  # noqa: BLE001
                    pass
            elif getattr(e, "description", None):
                msg = e.description
            return rpc_result(mid, {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True})
    return rpc_error(mid, -32601, f"Method not found: {method}")


@app.route("/mcp", methods=["GET", "POST", "DELETE"])
@app.route("/mcp/<token>", methods=["GET", "POST", "DELETE"])
def mcp(token=None):
    level = mcp_auth(token)
    if level is None:
        return (jsonify(error="unauthorized"), 404 if token else 401)
    if request.method != "POST":
        return Response(status=405, headers={"Allow": "POST"})
    try:
        payload = request.get_json(force=True)
    except Exception:  # noqa: BLE001
        return jsonify(rpc_error(None, -32700, "Parse error")), 400
    private = level == "private"
    if isinstance(payload, list):
        out = [r for r in (handle_rpc(m, private) for m in payload) if r is not None]
        return (jsonify(out), 200) if out else Response(status=202)
    res = handle_rpc(payload, private)
    return (jsonify(res), 200) if res is not None else Response(status=202)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8768)), debug=False)
