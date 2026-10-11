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
