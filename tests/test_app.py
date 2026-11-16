"""Smoke tests:  DATA_DIR is a temp folder; run with  python3 -m pytest -q tests"""
import io, os, sys, tempfile
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="coffee-test-")
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as coffee  # noqa: E402

coffee.geocode_place = lambda *a: None  # no network in tests
c = coffee.app.test_client()


def jpeg():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (300, 400), (120, 80, 40)).save(b, "JPEG")
    b.seek(0)
    return b


def add_bag(**over):
    body = {"roaster": {"name": "Stumptown Coffee Roasters", "city": "Portland", "country": "USA"},
            "coffee": {"name": "Ethiopia Guji Hambela", "origin": "Ethiopia", "process": "Natural",
                       "roaster_notes": "blueberry, jasmine"},
            "bag": {"purchased_on": "2026-10-10", "roast_date": "2026-10-02", "weight": "12 oz", "price": "$22",
                    "shop": "Stumptown Division", "city": "Portland", "country": "United States",
                    "lat": 45.505, "lng": -122.63, "trip": "Portland 2026"}}
    body.update(over)
    r = c.post("/admin/api/bags", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_flow():
    up = c.post("/admin/api/uploads", data={"file": (jpeg(), "bag.jpg")}, content_type="multipart/form-data")
    token = up.get_json()["uploads"][0]
    bag = add_bag(uploads=[token])
    assert bag["weight_g"] == 340.2 and bag["price"] == 22 and len(bag["photos"]) == 1
    assert bag["coffee"]["process"] == "natural"
    # same roaster/coffee again -> reused
    bag2 = add_bag(bag={"purchased_on": "2026-11-01", "roast_date": "2026-10-28"})
    assert bag2["coffee"]["id"] == bag["coffee"]["id"]
    # one review per method per bag; second PUT updates
    r = c.put(f"/admin/api/bags/{bag['id']}/reviews", json={"method": "v60", "rating": 7, "grind": "14",
                                                            "brewed_on": "2026-10-11", "flavors": "Blueberry, cocoa"})
    assert r.status_code == 201 and r.get_json()["review"]["method"] == "V60"
    assert r.get_json()["review"]["days_off_roast"] == 9
    r = c.put(f"/admin/api/bags/{bag['id']}/reviews", json={"method": "V60", "rating": 8.3, "grind": "15"})
    assert r.status_code == 200 and r.get_json()["review"]["rating"] == 8.5
    c.put(f"/admin/api/bags/{bag['id']}/reviews", json={"method": "Cold brew", "rating": 9})
    full = c.get(f"/admin/api/bags/{bag['id']}").get_json()
    assert len(full["reviews"]) == 2 and full["status"] == "open"
    # public view hides private fields
    pub = c.get(f"/api/bags/{bag['id']}", headers={"Cf-Ray": "x"}).get_json()
    assert "price" not in pub and "lat" not in pub
    # admin from Cloudflare without JWT is refused
    assert c.get("/admin/api/shelf", headers={"Cf-Ray": "x"}).status_code == 401
    coffees = c.get("/api/coffees?q=guji").get_json()["coffees"]
    assert coffees[0]["avg_rating"] == 8.75 and coffees[0]["bag_count"] == 2
    st = c.get("/admin/api/stats").get_json()
    assert st["totals"]["bags"] == 2 and st["by_method"]
    assert c.get("/admin/api/last-recipe?method=v60").get_json()["grind"] == "15"
    assert c.get("/healthz").get_json()["ok"]


def test_merge_and_mcp():
    a = add_bag(roaster={"name": "Heart Roasters"}, coffee={"name": "Kenya Kiriga"})
    b = add_bag(roaster={"name": "Heart Coffee Roasters", "city": "Portland"}, coffee={"name": "Kenya Kiriga AA"})
    m = c.get("/admin/api/match?roaster=Heart Coffee&coffee=Kenya Kiriga").get_json()
    assert m["roaster_match"]
    r = c.post(f"/admin/api/roasters/{a['roaster']['id']}/merge", json={"into": b['roaster']['id']})
    assert r.get_json()["ok"]
    r = c.post(f"/admin/api/coffees/{a['coffee']['id']}/merge", json={"into": b['coffee']['id']})
    assert c.get(f"/admin/api/coffees/{b['coffee']['id']}").get_json()["bag_count"] == 2
    rpc = lambda m, p=None, path="/mcp": c.post(path, json={"jsonrpc": "2.0", "id": 1, "method": m, "params": p or {}}).get_json()
    tools = {t["name"] for t in rpc("tools/list")["result"]["tools"]}
    assert "search_coffees" in tools and "log_review" not in tools
    res = rpc("tools/call", {"name": "search_coffees", "arguments": {"query": "kenya"}})
    assert res["result"]["structuredContent"]["count"] == 1


def test_label_reading(monkeypatch):
    add_bag(roaster={"name": "Sightglass Coffee", "city": "San Francisco"}, coffee={"name": "Owl's Howl"})
    monkeypatch.setattr(coffee, "ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(coffee, "call_claude", lambda imgs, prompt: """Here you go:
      {"roaster": {"name": "SIGHTGLASS", "city": "San Francisco", "website": "sightglass.coffee"},
       "coffee": {"name": "Owls Howl Espresso", "origin": ["Ethiopia", "Colombia"], "process": "Washed",
                  "roaster_notes": "dark chocolate, cherry", "is_blend": true},
       "bag": {"roast_date": "2026-10-03", "roast_date_printed": "10.03.26", "weight": "12 oz", "price": null},
       "uncertain": ["roast_date"]}""")
    up = c.post("/admin/api/uploads", data={"file": (jpeg(), "bag.jpg")}, content_type="multipart/form-data")
    r = c.post("/admin/api/label", json={"uploads": up.get_json()["uploads"]}).get_json()
    assert r["fields"]["coffee"]["origin"] == "Ethiopia, Colombia" and r["fields"]["coffee"]["is_blend"] is True
    assert r["roaster_match"]["name"] == "Sightglass Coffee"
    assert r["coffee_match"]["name"] == "Owl's Howl"


def test_mcp_private_log_review(monkeypatch):
    monkeypatch.setattr(coffee, "MCP_TOKEN", "secret")
    bag = add_bag(roaster={"name": "Proud Mary"}, coffee={"name": "Hondo"})
    call = lambda tool, args: c.post("/mcp/secret", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                          "params": {"name": tool, "arguments": args}}).get_json()["result"]
    res = call("log_review", {"bag_id": bag["id"], "method": "aeropress", "rating": 8, "grind": "11"})
    assert res["structuredContent"]["review"]["method"] == "AeroPress"
    assert call("log_review", {"bag_id": 99999, "method": "V60"})["isError"]
    assert c.post("/mcp/wrong", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404


def test_method_presets_and_migration():
    p = c.get("/admin/api/last-recipe?method=drip").get_json()
    assert (p["grinder"], p["grind"], p["brewer"], p["recipe"], p["source"]) == \
        ("Baratza Encore (M2 burrs)", "21", "Breville Precision Brewer", "Gold Cup mode", "preset")
    assert c.get("/admin/api/last-recipe?method=Espresso").get_json()["grinder"] == "Eureka Mignon Zero 55mm"
    bag = add_bag(roaster={"name": "Verve"}, coffee={"name": "Streetlevel"})
    c.put(f"/admin/api/bags/{bag['id']}/reviews", json={"method": "Drip", "rating": 7, "grind": "20", "brewer": "Breville Precision Brewer"})
    assert c.get("/admin/api/last-recipe?method=Drip").get_json()["grind"] == "20"  # last review beats the preset
    r = c.put("/admin/api/methods", json={"methods": [{"name": "Drip", "grinder": "Encore", "grind": "22"}, "Cold brew"]}).get_json()
    assert [m["name"] for m in r["methods"]] == ["Drip", "Cold brew"] and r["methods"][1]["grind"] == "37"
    # an old database without the new columns is upgraded in place
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE methods (name TEXT PRIMARY KEY, pos INTEGER, hidden INTEGER);"
                      "CREATE TABLE reviews (id INTEGER PRIMARY KEY, bag_id INTEGER, method TEXT);")
    coffee.migrate(con)
    assert "brewer" in {r[1] for r in con.execute("PRAGMA table_info(reviews)")}
    assert "grind" in {r[1] for r in con.execute("PRAGMA table_info(methods)")}


def test_map_places_bags_and_falls_back_to_roaster(monkeypatch):
    places = {"Santa Barbara": (34.42, -119.70), "Carpinteria": (34.40, -119.52)}
    monkeypatch.setattr(coffee, "geocode_place", lambda city, region, country: places.get(city))
    monkeypatch.setattr(coffee.time, "sleep", lambda s: None)
    # misspelled purchase city: not found, so the bag shows at the roaster's city
    typo = add_bag(roaster={"name": "Dart Coffee Co.", "city": "Santa Barbara", "region": "CA"},
                   coffee={"name": "Ethiopia Kayon Mountain"},
                   bag={"city": "Carpeneria", "region": "CA", "country": "United States", "shop": "Dart Coffee Co"})
    assert typo["lat"] is None and typo["map"] == "roaster"
    # fixing the spelling looks the place up again
    fixed = c.put(f"/admin/api/bags/{typo['id']}", json={"city": "Carpinteria"}).get_json()
    assert fixed["lat"] == 34.40 and fixed["map"] == "bought"
    # bought online, roaster with no city: nowhere to put it
    add_bag(roaster={"name": "Nowhere Roasters"}, coffee={"name": "Mystery"}, bag={"online": True})
    pts = c.get("/admin/api/map").get_json()
    kinds = {p["coffee_name"]: p["kind"] for p in pts["points"]}
    assert kinds["Ethiopia Kayon Mountain"] == "bought" and "Mystery" not in kinds and pts["unplaced"] >= 1
    # an older bag that was never looked up gets placed when the map opens
    con = coffee.connect()
    bid = con.execute("INSERT INTO bags(coffee_id, city, online) VALUES (?, 'Carpinteria', 0)", (typo["coffee"]["id"],)).lastrowid
    con.commit()
    c.get("/admin/api/map")
    assert tuple(coffee.connect().execute("SELECT lat, geo_tried FROM bags WHERE id=?", (bid,)).fetchone()) == (34.40, 1)
    # the public view never shows GPS points or lookup flags
    pub = c.get(f"/api/bags/{typo['id']}", headers={"Cf-Ray": "x"}).get_json()
    assert "lat" not in pub and "geo_tried" not in pub and "map" not in pub and "lat" not in pub["roaster"]


def test_roaster_privacy_endpoints():
    bag = add_bag(roaster={"name": "Privacy Roasters", "city": "Oakland", "country": "USA"},
                  coffee={"name": "Privacy Blend"})
    con = coffee.connect()
    rid = bag["roaster"]["id"]
    con.execute("UPDATE roasters SET lat=37.8, lng=-122.2, geo_tried=1 WHERE id=?", (rid,))
    con.commit()

    # Public views must never expose lat, lng, or geo_tried on roasters
    pub_roasters = c.get("/api/roasters", headers={"Cf-Ray": "x"}).get_json()["roasters"]
    pub_r = next(r for r in pub_roasters if r["id"] == rid)
    assert "lat" not in pub_r and "lng" not in pub_r and "geo_tried" not in pub_r

    pub_roaster = c.get(f"/api/roasters/{rid}", headers={"Cf-Ray": "x"}).get_json()
    assert "lat" not in pub_roaster and "lng" not in pub_roaster and "geo_tried" not in pub_roaster

    pub_coffee = c.get(f"/api/coffees/{bag['coffee']['id']}", headers={"Cf-Ray": "x"}).get_json()
    assert "lat" not in pub_coffee["roaster"] and "lng" not in pub_coffee["roaster"] and "geo_tried" not in pub_coffee["roaster"]

    # Private views show lat and lng, but strip geo_tried
    priv_roaster = c.get(f"/admin/api/roasters/{rid}").get_json()
    assert priv_roaster["lat"] == 37.8 and priv_roaster["lng"] == -122.2 and "geo_tried" not in priv_roaster

    priv_coffee = c.get(f"/admin/api/coffees/{bag['coffee']['id']}").get_json()
    assert priv_coffee["roaster"]["lat"] == 37.8 and "geo_tried" not in priv_coffee["roaster"]


def test_backfill_locations_per_row_commit(monkeypatch):
    bag = add_bag(bag={"city": "Ventura", "lat": None, "lng": None})
    con = coffee.connect()
    # Ensure two bags pending geocoding
    bid1 = bag["id"]
    bid2 = con.execute("INSERT INTO bags(coffee_id, city, online, geo_tried) VALUES (?, 'Ojai', 0, 0)",
                       (bag["coffee_id"],)).lastrowid
    con.execute("UPDATE bags SET lat=NULL, lng=NULL, geo_tried=0 WHERE id=?", (bid1,))
    con.commit()

    committed_while_in_loop = []

    def mock_geocode(city, region, country):
        if city == "Ventura":
            return 34.28, -119.29
        # While geocoding the second bag, check if first bag was already committed
        con2 = coffee.connect()
        row = con2.execute("SELECT lat, geo_tried FROM bags WHERE id=?", (bid1,)).fetchone()
        con2.close()
        committed_while_in_loop.append(tuple(row))
        return 34.44, -119.24

    monkeypatch.setattr(coffee, "geocode_place", mock_geocode)
    monkeypatch.setattr(coffee.time, "sleep", lambda s: None)

    main_con = coffee.connect()
    coffee.backfill_locations(main_con, budget=2)
    main_con.close()

    assert committed_while_in_loop == [(34.28, 1)]


def test_method_casing_and_rename_consistency():
    bag = add_bag(coffee={"name": "Method Test Beans"})
    bid = bag["id"]

    # Upsert with lowercase method name, then update with canonical/uppercase
    r1 = c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "chemex", "rating": 7.5})
    assert r1.status_code == 201
    assert r1.get_json()["review"]["method"] == "Chemex"

    # Second put with different casing updates in-place without duplicate
    r2 = c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "CHEMEX", "rating": 8.5})
    assert r2.status_code == 200
    assert r2.get_json()["review"]["id"] == r1.get_json()["review"]["id"]
    assert r2.get_json()["review"]["rating"] == 8.5

    # Changing old_method to an existing method case-insensitively returns 409
    c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "AeroPress", "rating": 8.0})
    r_conflict = c.put(f"/admin/api/bags/{bid}/reviews", json={"old_method": "AeroPress", "method": "chemex"})
    assert r_conflict.status_code == 409

    # PUT /admin/api/methods renames method casing and cascades to existing reviews
    c.put("/admin/api/methods", json={"methods": ["CHEMEX", "AeroPress"]})
    con = coffee.connect()
    methods_in_db = {r[0] for r in con.execute("SELECT method FROM reviews WHERE bag_id=?", (bid,)).fetchall()}
    assert "CHEMEX" in methods_in_db and "Chemex" not in methods_in_db

    # Updating the review now continues to work without duplicating
    r3 = c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "chemex", "rating": 9.0})
    assert r3.status_code == 200
    revs = c.get(f"/admin/api/bags/{bid}").get_json()["reviews"]
    assert len(revs) == 2


def test_staged_uploads_cleanup_and_delete(monkeypatch):
    import time
    # Upload staged photo
    up = c.post("/admin/api/uploads", data={"file": (jpeg(), "test.jpg")}, content_type="multipart/form-data")
    assert up.status_code == 200
    token = up.get_json()["uploads"][0]
    assert (coffee.PHOTO_DIR / token).exists()
    assert (coffee.PHOTO_DIR / ("t-" + token)).exists()

    # Reject non-tmp and invalid filenames
    assert c.delete("/admin/api/uploads/not-tmp.jpg").status_code == 400
    assert c.delete("/admin/api/uploads/1-abcdef1234.jpg").status_code == 400
    assert c.delete("/admin/api/uploads/tmp-toolong12345678.jpg").status_code == 400

    # Auth check: unauthorized requests rejected
    assert c.delete(f"/admin/api/uploads/{token}", headers={"Cf-Ray": "x"}).status_code == 401

    # Valid DELETE removes both full image and thumbnail
    del_res = c.delete(f"/admin/api/uploads/{token}")
    assert del_res.status_code == 200 and del_res.get_json()["ok"] is True
    assert not (coffee.PHOTO_DIR / token).exists()
    assert not (coffee.PHOTO_DIR / ("t-" + token)).exists()

    # Stale upload cleanup removes old tmp-* and t-tmp-* files
    old_file = coffee.PHOTO_DIR / "tmp-0123456789.jpg"
    old_thumb = coffee.PHOTO_DIR / "t-tmp-0123456789.jpg"
    recent_file = coffee.PHOTO_DIR / "tmp-9876543210.jpg"
    old_file.write_bytes(b"old")
    old_thumb.write_bytes(b"old thumb")
    recent_file.write_bytes(b"recent")

    old_mtime = time.time() - 3 * 86400
    os.utime(old_file, (old_mtime, old_mtime))
    os.utime(old_thumb, (old_mtime, old_mtime))

    coffee.cleanup_tmp_uploads()
    assert not old_file.exists()
    assert not old_thumb.exists()
    assert recent_file.exists()
    recent_file.unlink()


def test_parse_label_non_greedy():
    import pytest
    text = """Here is the JSON:
    {"roaster": {"name": "Blue Bottle", "city": "Oakland"},
     "coffee": {"name": "Bella Donovan", "origin": "Ethiopia, Sumatra", "is_blend": true},
     "bag": {"weight": "12 oz", "price": 18.5},
     "uncertain": []}
    Trailing commentary with braces: {note: blend of washed and natural coffees; best at 200F}"""
    parsed = coffee.parse_label(text)
    assert parsed["roaster"]["name"] == "Blue Bottle"
    assert parsed["coffee"]["origin"] == "Ethiopia, Sumatra"
    assert parsed["coffee"]["is_blend"] is True
    assert parsed["bag"]["weight"] == "12 oz"

    with pytest.raises(ValueError, match="No JSON in the response"):
        coffee.parse_label("No braces in this text")


def test_stats_consistent_origin_review_counts():
    bag = add_bag(coffee={"name": "Multi Origin Test", "origin": "Rwanda, Bolivia", "process": "Honey", "roast_level": "Light"})
    bid = bag["id"]

    # One rated review, one unrated review
    c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "V60", "rating": 8.0})
    c.put(f"/admin/api/bags/{bid}/reviews", json={"method": "Drip"})  # unrated review

    stats = c.get("/admin/api/stats").get_json()
    origins = {o["name"]: o for o in stats["by_origin"]}
    assert origins["Rwanda"]["n"] == 2
    assert origins["Rwanda"]["avg"] == 8.0
    assert origins["Bolivia"]["n"] == 2
    assert origins["Bolivia"]["avg"] == 8.0

    # Matches by_process and by_roast counting both rated and unrated reviews
    proc = next(p for p in stats["by_process"] if p["name"] == "honey")
    assert proc["n"] == 2
    roast = next(r for r in stats["by_roast"] if r["name"] == "light")
    assert roast["n"] == 2
