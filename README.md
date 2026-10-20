# Roasted

My coffee log: the beans I buy, often a pound from an independent roaster on a trip, and how each one brewed. It's like Untappd, but for my own bags. Flask + SQLite, one small service on CT 210 next to Cookbooks, Dinner Log and the wish list, styled with my Café Green design system.

- **Public** `https://coffee.nickandrade.com/` shows the feed, coffees, roasters and stats. Prices, private notes and GPS points are left out.
- **Editing** at `https://coffee.nickandrade.com/admin` sits behind Cloudflare Access. From there you add bags, review them, and use the map.

## How it's organized
| | What it is | Example |
|---|---|---|
| **Roaster** | the company | Heart Coffee Roasters, Portland |
| **Coffee** | one of their offerings | Ethiopia Guji Hambela, natural |
| **Bag** | one purchase | Sep 12 2026, Heart East Burnside, roasted Sep 8, 12 oz, $24, photos |
| **Review** | **one per brew method per bag** | V60: 8.5, Encore @ 15, 20 g : 320 g, "blueberry, jasmine" |

Brewing the same bag with the same method again means **editing** that review: change the grind, the rating or the notes. Reviews also record days off roast automatically, which feeds the *Days off roast* stat.

Buying the same coffee again (**＋ Bought it again** on the coffee's page) adds a new bag to it. The coffee page then compares methods across every bag.

## Install / upgrade (CT 210)
Build the one-file installer from this repo, then run it in the container:
```sh
sh installer/build.sh                 # -> dist/setup-roasted.sh
pct push 210 dist/setup-roasted.sh /root/setup-roasted.sh
pct exec 210 -- sh /root/setup-roasted.sh
```
The script is safe to run again. It replaces the app in `/opt/roasted` but keeps `/var/lib/roasted` (the database and photos) and `/etc/conf.d/roasted`. The service is `roasted` (`rc-service roasted restart`), logging to `/var/log/roasted.log`. `sh setup-roasted.sh --uninstall` removes the app and service and keeps the data.

On first install it copies `ANTHROPIC_API_KEY` and `CF_TEAM_DOMAIN` from `/etc/conf.d/cookbooks` when they exist there.

Port **8768**. The others are Dinner Log 8765, wish list 8766 and Cookbooks 8767. To use another port, run `ROASTED_PORT=… sh setup-roasted.sh` the first time.

## Cloudflare (same setup as Cookbooks)
1. **Tunnel → Public hostname**: `coffee.nickandrade.com` → `http://<CT 210 IP>:8768`, on the same tunnel as the other apps.
2. **Access → Applications → Add → Self-hosted**:
   - Domain `coffee.nickandrade.com`, **path `admin`**.
   - Policy: your email, using One-time PIN.
   - Copy the **AUD tag**.
3. Set `CF_AUD` (and `CF_TEAM_DOMAIN` if it wasn't copied) in `/etc/conf.d/roasted`, then run `rc-service roasted restart`.

The app also verifies the Access JWT itself. Requests that don't come through Cloudflare, i.e. directly on the LAN, can edit while `LAN_EDIT=1`.

If you'd rather keep the whole thing private, set `PUBLIC_VIEW=0`. `/` then redirects to `/admin`, and photos and the public API need a sign-in.

## Adding a bag (on the phone)
1. **＋ Bag → Take photo** for each side of the bag (front, then back), or **Choose photos** to pick several from the gallery. They're separate buttons because Android hides the camera when a picker allows several files.
2. **Read the label** sends the photos to Claude. It fills in the roaster, coffee name, origin, region, producer, varietal, process, roast level, the tasting notes on the bag, the roast date and the size. It also matches roasters and coffees you already have, so "SIGHTGLASS" becomes your existing *Sightglass Coffee*. Check anything it flags as uncertain, especially roast dates like 03/04.
3. **Use my location** fills in the GPS point, then looks up the shop, city and country on OpenStreetMap. If you've bought within ~300 m of here before, it reuses that shop name. Location needs HTTPS, so use coffee.nickandrade.com rather than the LAN IP.
4. Add the trip name (e.g. *Portland, Oct 2026*) so the trip's coffees can be browsed together, plus the price. Then **Save bag**.

Without GPS, the city you type is looked up when you save (OpenStreetMap Nominatim, then Photon, which tolerates small typos), so the bag still lands on the map. A bag whose city can't be found, or that was **Bought online**, shows at the roaster's city instead (a hollow marker), and its page says so. Places that were never looked up, e.g. while a lookup service was down, are retried a few at a time when the map opens.

## Reviewing
On a bag, tap a method chip (**＋ Drip**, **＋ Cold brew**, **＋ Nitro cold brew**, **＋ French press**, **＋ Pour over**, **＋ Percolator**…). The recipe fields follow the method: cold brew, nitro and French press ask for steep time, the percolator for perk time, espresso for yield and shot time. For a method you've used before, the recipe (grinder, grind, dose, water, temp, time) is pre-filled from the last time you brewed it, using the same coffee's recipe first if there is one. Ratings are 0–10 in halves.

**Equipment presets** pre-fill each method's first review. Edit them under **More → Brew methods**:

| Method | Grinder | Setting | Brewer | Technique |
|---|---|---|---|---|
| Drip | Baratza Encore (M2) | 21 | Breville Precision Brewer | Gold Cup mode |
| Cold brew / Nitro cold brew | Baratza Encore (M2) | 37 | | |
| Espresso | Eureka Mignon Zero 55mm | 4 | Breville Infuser | |
| French press / Pour over / Percolator | Baratza Encore (M2) | | | |

After a method's first review, its last review takes over from the preset.

Brewing a sealed bag marks it opened. **Finished it** takes the bag off the shelf.

Edit the list of methods under **More**.

## Stats
- Ratings by method, process, origin, roast level, and days off roast when brewed.
- Where I've bought coffee, top coffees and roasters, and the tasting notes I keep writing.
- In admin only: grind settings that worked and spend per currency (per lb).

## Duplicates
Label reading can create near-duplicates such as "Heart Roasters" and "Heart Coffee Roasters".
- **Roaster → Edit → Merge into…** moves its coffees over, combining coffees with the same name.
- **Coffee → Edit → Merge into…** moves the bags and reviews.

Both fill in blanks on the one you keep.

## MCP (use the log from Claude)
- `https://coffee.nickandrade.com/mcp` is public and read-only: `search_coffees`, `get_coffee`, `recent_reviews`, `list_roasters` and `coffee_stats`.
- `https://coffee.nickandrade.com/mcp/<MCP_TOKEN>` (or `Authorization: Bearer <MCP_TOKEN>`) adds:
  - `on_the_shelf`
  - `log_review`, e.g. *"log a 7.5 for the Kiriga on the AeroPress, grind 11, a bit sour"*
  - `finish_bag`
  - spend and grind stats
- The token is generated on install and lives in `/etc/conf.d/roasted`.
- `/mcp` must stay **outside** the Access app, which only protects `/admin`.

## Backups and alerts
- `/etc/periodic/daily/roasted-backup` writes a consistent SQLite snapshot to `/var/backups/ct210/<date>/roasted-roasted.db.gz`, next to the other apps' snapshots. It uses the same `KEEP_DAYS`, `BACKUP_TARGET` and log as `ct210-backup` (`/etc/conf.d/ct210-ops`).
- With `BACKUP_TARGET` set, photos are copied to `<target>/files/roasted/photos/`.
- The installer adds Roasted to `HEALTH_CHECKS` in `/etc/conf.d/ct210-ops`, so the 15-minute health check alerts Home Assistant if it goes down.
- The service runs under `supervise-daemon`, so a crash restarts it.

Restore by hand:
```sh
rc-service roasted stop
gunzip -c /var/backups/ct210/<date>/roasted-roasted.db.gz > /var/lib/roasted/roasted.db
chown roasted /var/lib/roasted/roasted.db
rc-service roasted start
```
**More → Download** gives a CSV (one row per review) or everything as JSON.

## Development
```sh
python3 app.py                      # http://localhost:8768/admin, data in ./data
python3 -m pytest -q tests
```
## Look and feel
Styled with my **Café Green** design system:
- A British racing green header band, then green-accent pill buttons on a cream canvas.
- Figtree type throughout, and line icons rather than emoji.
- Ratings are tinted greener as they rise; 9 and up earns the gold reserved for milestones.

The token names in `static/style.css` match the system's `tokens.json`, so they're easy to keep in sync.

## Credits
- [Leaflet](https://leafletjs.com) 1.9.4 (BSD-2-Clause), bundled in `static/leaflet`, with © OpenStreetMap tiles. Place lookups use OpenStreetMap Nominatim.
- [Figtree](https://github.com/erikdkennedy/figtree) (SIL Open Font License 1.1), bundled in `static/fonts`.
