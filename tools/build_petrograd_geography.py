"""
Builds settings/petrograd_1917/geography.json - the movement graph the game's code routes over
(2026-11-03). Run it again after editing the tables below:

    python tools/build_petrograd_geography.py

One graph, three scales, joined at shared nodes:
  - the city: places grouped by which bank of the Neva they're on, joined within a bank by street
    links (generated: each place to its nearest neighbours on the same bank) and across banks ONLY
    by named bridges - so raising a bridge really does cut a district off;
  - the region and country: the 1917 railway network, simplified to the lines and junctions that
    matter for the revolution and the Civil War, entering the city at its five termini (the same
    nodes the city graph uses);
  - the sea: Kronstadt, Oranienbaum, Helsingfors and Reval, and the Neva quays.

City positions come from the verified map pins (map.json). Bridge ends, stations and every place
outside the city are approximate - good to a few hundred metres in town and a few kilometres in the
country, which is all a travel-time model needs. Distances are great-circle distances times a
detour factor per mode; the line list is a simplification of the 1917 network, not a timetable.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTING = ROOT / "settings" / "petrograd_1917"
DETOUR = {"street": 1.3, "road": 1.25, "rail": 1.2, "sea": 1.1, "bridge": 1.0}

# ---------------------------------------------------------------- city places, by bank
# area values: left_bank (the centre and south), vasilievsky, petrograd_side, fortress, vyborg_side
CITY = {
    # map.json landmarks and barracks reuse their map ids, so positions stay in one place
    "winter_palace": "left_bank", "mariinsky_palace": "left_bank", "central_telephone_exchange": "left_bank",
    "central_telegraph": "left_bank", "nevsky_prospekt": "left_bank", "smolny_institute": "left_bank",
    "tauride_palace": "left_bank", "putilov_works": "left_bank",
    "pavlovsky": "left_bank", "preobrazhensky": "left_bank", "volynsky": "left_bank", "litovsky": "left_bank",
    "keksgolmsky": "left_bank", "izmailovsky": "left_bank", "semyonovsky": "left_bank", "egersky": "left_bank",
    "don_cossacks": "left_bank", "armored_cars": "left_bank",
    "finlyandsky": "vasilievsky",
    "junkers": "petrograd_side", "grenadier": "petrograd_side",
    "peter_and_paul_fortress": "fortress",
    "finland_station": "vyborg_side", "kresty_prison": "vyborg_side", "red_guards": "vyborg_side", "moskovsky": "vyborg_side",
}
# places the map doesn't have (approximate)
EXTRA_CITY = {
    "nikolaevsky_station": ("Nikolaevsky Station", "station", "left_bank", 59.9307, 30.3615, "Trains to Moscow - and the Northern line to Vologda and Murmansk."),
    "warsaw_station": ("Warsaw Station", "station", "left_bank", 59.9076, 30.3025, "Trains south-west: Gatchina, Luga, Pskov and the Northern Front."),
    "baltic_station": ("Baltic Station", "station", "left_bank", 59.9071, 30.2992, "Trains to Oranienbaum, Narva and Reval."),
    "vitebsk_station": ("Tsarskoe Selo Station", "station", "left_bank", 59.9202, 30.3290, "Trains to Tsarskoe Selo, Dno and the south."),
    "neva_quays": ("English Embankment quays", "quay", "left_bank", 59.9340, 30.2900, "Where ships from Kronstadt tie up - the Aurora's berth is upriver."),
    "general_staff": ("General Staff building (Military District HQ)", "landmark", "left_bank", 59.9390, 30.3175, "Headquarters of the Petrograd Military District, across Palace Square from the Winter Palace."),
    "admiralty": ("Admiralty", "landmark", "left_bank", 59.9375, 30.3086, "The Naval Ministry."),
    "state_bank": ("State Bank", "landmark", "left_bank", 59.9310, 30.3290, "On Sadovaya Street; the treasury's vaults."),
    "franco_russian_works": ("Franco-Russian Works", "factory", "left_bank", 59.9165, 30.2810, "Shipyard on the Neva where the cruiser Aurora is under repair."),
    "strelka": ("The Strelka (Stock Exchange)", "landmark", "vasilievsky", 59.9440, 30.3050, "The point of Vasilievsky Island facing the Winter Palace."),
    "university": ("Petrograd University", "landmark", "vasilievsky", 59.9417, 30.2990, "The Twelve Collegia; students and lecture halls."),
    "baltic_shipyard": ("Baltic Shipyard", "factory", "vasilievsky", 59.9325, 30.2580, "Big naval yard; a Bolshevik stronghold."),
    "kshesinskaya_mansion": ("Kshesinskaya Mansion", "landmark", "petrograd_side", 59.9536, 30.3301, "The Bolsheviks' headquarters until July; now held by the government."),
    "arsenal": ("Petrograd Arsenal", "factory", "vyborg_side", 59.9537, 30.3755, "Arms works on the Vyborg embankment."),
    "nobel_works": ("Nobel works", "factory", "vyborg_side", 59.9592, 30.3446, "Ludwig Nobel engineering works."),
    "okhta": ("Okhta", "district", "vyborg_side", 59.9480, 30.4150, "Workers' district across the Neva from the Smolny; the Okhta powder works."),
    "okhtinsky_bridge_s": ("Okhtinsky Bridge (Smolny end)", "bridgehead", "left_bank", 59.9420, 30.4010, ""),
    "okhtinsky_bridge_n": ("Okhtinsky Bridge (Okhta end)", "bridgehead", "vyborg_side", 59.9455, 30.4100, ""),
    "grenadersky_bridge_w": ("Grenadersky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9705, 30.3325, ""),
    "grenadersky_bridge_e": ("Grenadersky Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.9712, 30.3375, ""),
    "tuchkov_bridge_s": ("Tuchkov Bridge (island end)", "bridgehead", "vasilievsky", 59.9498, 30.2858, ""),
    "tuchkov_bridge_n": ("Tuchkov Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9522, 30.2895, ""),
    "palace_bridge_s": ("Palace Bridge (Admiralty end)", "bridgehead", "left_bank", 59.9398, 30.3090, ""),
    "palace_bridge_n": ("Palace Bridge (Strelka end)", "bridgehead", "vasilievsky", 59.9431, 30.3070, ""),
    "nikolaevsky_bridge_s": ("Nikolaevsky Bridge (Admiralty end)", "bridgehead", "left_bank", 59.9331, 30.2930, ""),
    "nikolaevsky_bridge_n": ("Nikolaevsky Bridge (island end)", "bridgehead", "vasilievsky", 59.9364, 30.2862, ""),
    "troitsky_bridge_s": ("Troitsky Bridge (Field of Mars end)", "bridgehead", "left_bank", 59.9460, 30.3297, ""),
    "troitsky_bridge_n": ("Troitsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9518, 30.3253, ""),
    "liteyny_bridge_s": ("Liteyny Bridge (Liteyny end)", "bridgehead", "left_bank", 59.9498, 30.3492, ""),
    "liteyny_bridge_n": ("Liteyny Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.9538, 30.3500, ""),
    "birzhevoy_bridge_w": ("Birzhevoy Bridge (Strelka end)", "bridgehead", "vasilievsky", 59.9458, 30.3040, ""),
    "birzhevoy_bridge_e": ("Birzhevoy Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9483, 30.3058, ""),
    "sampsonievsky_bridge_w": ("Sampsonievsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9562, 30.3365, ""),
    "sampsonievsky_bridge_e": ("Sampsonievsky Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.9575, 30.3405, ""),
    "ioannovsky_bridge_n": ("Ioannovsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.9526, 30.3209, ""),
}
BRIDGES = [  # (edge id, name, end a, end b, raisable)
    ("palace_bridge", "Palace Bridge", "palace_bridge_s", "palace_bridge_n", True),
    ("nikolaevsky_bridge", "Nikolaevsky Bridge", "nikolaevsky_bridge_s", "nikolaevsky_bridge_n", True),
    ("troitsky_bridge", "Troitsky Bridge", "troitsky_bridge_s", "troitsky_bridge_n", True),
    ("liteyny_bridge", "Liteyny Bridge", "liteyny_bridge_s", "liteyny_bridge_n", True),
    ("birzhevoy_bridge", "Birzhevoy Bridge", "birzhevoy_bridge_w", "birzhevoy_bridge_e", True),
    ("sampsonievsky_bridge", "Sampsonievsky Bridge", "sampsonievsky_bridge_w", "sampsonievsky_bridge_e", False),
    ("okhtinsky_bridge", "Okhtinsky Bridge", "okhtinsky_bridge_s", "okhtinsky_bridge_n", True),
    ("grenadersky_bridge", "Grenadersky Bridge", "grenadersky_bridge_w", "grenadersky_bridge_e", False),
    ("tuchkov_bridge", "Tuchkov Bridge", "tuchkov_bridge_s", "tuchkov_bridge_n", False),
    ("ioannovsky_bridge", "Ioannovsky Bridge (to the fortress)", "ioannovsky_bridge_n", "peter_and_paul_fortress", False),
]

# ---------------------------------------------------------------- the region and country (approximate)
OUTSIDE = {
    # id: (name, kind, lat, lng, note)
    "kronstadt": ("Kronstadt", "port", 59.9908, 29.7747, "Island naval base of the Baltic Fleet."),
    "oranienbaum": ("Oranienbaum", "port", 59.9160, 29.7700, "Rail terminus and ferry for Kronstadt."),
    "levashovo": ("Levashovo", "town", 60.1031, 30.2075, "Station on the Finland line north of the city."),
    "pulkovo": ("Pulkovo Heights", "town", 59.7720, 30.3260, "The high ground on the southern road into the city."),
    "tsarskoe_selo": ("Tsarskoe Selo", "town", 59.7200, 30.4000, "The old imperial residence; garrisoned."),
    "gatchina": ("Gatchina", "junction", 59.5650, 30.1300, "Junction of the Warsaw and Baltic lines south of the city."),
    "luga": ("Luga", "town", 58.7370, 29.8460, "On the Warsaw line toward Pskov."),
    "pskov": ("Pskov", "hq", 57.8190, 28.3320, "Headquarters of the Northern Front."),
    "dno": ("Dno", "junction", 57.8270, 29.9640, "Junction where the Tsar's train was stopped in March."),
    "dvinsk": ("Dvinsk", "front", 55.8740, 26.5360, "Front-line fortress on the Western Dvina."),
    "riga": ("Riga", "front", 56.9500, 24.1100, "Taken by the Germans in late August."),
    "narva": ("Narva", "town", 59.3790, 28.1900, "On the Baltic line to Reval."),
    "reval": ("Reval", "port", 59.4370, 24.7540, "Naval base; Estonia."),
    "beloostrov": ("Beloostrov", "town", 60.1490, 30.0010, "Border station with the Grand Duchy of Finland."),
    "terijoki": ("Terijoki", "town", 60.1730, 29.6960, "Finnish resort town on the line to Vyborg."),
    "vyborg": ("Vyborg", "town", 60.7100, 28.7500, "Finnish garrison town; old fortress."),
    "helsingfors": ("Helsingfors", "port", 60.1700, 24.9400, "Capital of Finland; Baltic Fleet base. Lenin is hiding here."),
    "mga": ("Mga", "junction", 59.7500, 31.0600, "Junction on the line east to Vologda."),
    "zvanka": ("Zvanka", "junction", 60.1200, 32.3100, "Where the Murmansk line leaves the Vologda line."),
    "petrozavodsk": ("Petrozavodsk", "town", 61.7900, 34.3500, "On the new Murmansk railway."),
    "murmansk": ("Murmansk", "port", 68.9700, 33.0800, "Ice-free port at the end of the line; Allied supplies."),
    "vologda": ("Vologda", "junction", 59.2200, 39.8900, "Northern junction: lines to Archangel, Moscow and the Urals."),
    "arkhangelsk": ("Archangel", "port", 64.5400, 40.5400, "White Sea port; Allied supplies."),
    "vyatka": ("Vyatka", "town", 58.6000, 49.6600, "On the northern line to Perm."),
    "perm": ("Perm", "town", 58.0000, 56.2300, "Urals city."),
    "ekaterinburg": ("Ekaterinburg", "town", 56.8400, 60.6000, "Urals industrial city."),
    "chudovo": ("Chudovo", "junction", 59.1200, 31.6600, "On the Nikolaevsky line; branch to Novgorod."),
    "novgorod": ("Novgorod", "town", 58.5200, 31.2700, "Old Novgorod."),
    "bologoye": ("Bologoye", "junction", 57.8800, 34.0500, "Halfway to Moscow; junction for the line west to Dno."),
    "tver": ("Tver", "town", 56.8600, 35.9000, "On the Nikolaevsky line."),
    "moscow": ("Moscow", "city", 55.7500, 37.6200, "Russia's second city and railway hub."),
    "yaroslavl": ("Yaroslavl", "town", 57.6300, 39.8700, "On the Volga, north of Moscow."),
    "novosokolniki": ("Novosokolniki", "junction", 56.3400, 30.1500, "Junction on the Vitebsk line."),
    "vitebsk": ("Vitebsk", "town", 55.1900, 30.2000, "Behind the Western Front."),
    "orsha": ("Orsha", "junction", 54.5100, 30.4200, "Junction of the Moscow-Minsk and Vitebsk-Mogilev lines."),
    "mogilev": ("Mogilev", "hq", 53.9000, 30.3300, "Stavka - supreme army headquarters."),
    "minsk": ("Minsk", "hq", 53.9000, 27.5600, "Headquarters of the Western Front."),
    "baranovichi": ("Baranovichi", "front", 53.1300, 26.0100, "On the Western Front."),
    "smolensk": ("Smolensk", "town", 54.7800, 32.0400, "On the Moscow-Minsk line."),
    "zhlobin": ("Zhlobin", "junction", 52.8900, 30.0400, "Junction south of Mogilev."),
    "gomel": ("Gomel", "town", 52.4300, 31.0000, ""),
    "bakhmach": ("Bakhmach", "junction", 51.1800, 32.8200, "Junction on the Kiev-Kursk line."),
    "kiev": ("Kiev", "city", 50.4500, 30.5200, "Seat of the Ukrainian Rada."),
    "tula": ("Tula", "town", 54.1900, 37.6200, "Arms factories."),
    "orel": ("Orel", "town", 52.9700, 36.0700, "On the Moscow-Kursk line."),
    "kursk": ("Kursk", "junction", 51.7300, 36.1900, ""),
    "kharkov": ("Kharkov", "city", 49.9900, 36.2300, "Industrial hub of the south."),
    "ryazan": ("Ryazan", "junction", 54.6300, 39.7300, ""),
    "kozlov": ("Kozlov", "junction", 52.9000, 40.4900, ""),
    "voronezh": ("Voronezh", "town", 51.6700, 39.2100, ""),
    "povorino": ("Povorino", "junction", 51.2000, 42.2500, ""),
    "tsaritsyn": ("Tsaritsyn", "town", 48.7100, 44.5100, "Volga city; grain and the road to the Caspian."),
    "rostov": ("Rostov-on-Don", "city", 47.2400, 39.7100, "Gateway to the Don and the Caucasus."),
    "novocherkassk": ("Novocherkassk", "town", 47.4200, 40.0900, "Capital of the Don Cossacks under Ataman Kaledin."),
    "ruzaevka": ("Ruzaevka", "junction", 54.0600, 44.9500, ""),
    "kazan": ("Kazan", "city", 55.7900, 49.1200, "Volga city; the State Bank's gold reserve is stored here."),
    "penza": ("Penza", "town", 53.2000, 45.0000, ""),
    "syzran": ("Syzran", "junction", 53.1600, 48.4700, "Volga crossing on the line east."),
    "samara": ("Samara", "city", 53.2000, 50.1500, "Volga city on the Trans-Siberian approach."),
    "orenburg": ("Orenburg", "town", 51.7700, 55.1000, "Capital of the Orenburg Cossacks."),
    "ufa": ("Ufa", "town", 54.7400, 55.9700, ""),
    "chelyabinsk": ("Chelyabinsk", "junction", 55.1600, 61.4000, "Where the Trans-Siberian proper begins."),
    "omsk": ("Omsk", "city", 54.9900, 73.3700, "Western Siberia."),
}
RAIL = [  # (line name, [station ids in order])
    ("Finland connecting line (over the Neva railway bridge)", ["finland_station", "nikolaevsky_station"]),
    ("Finland line", ["finland_station", "levashovo", "beloostrov", "terijoki", "vyborg", "helsingfors"]),
    ("Baltic line", ["baltic_station", "oranienbaum"]),
    ("Baltic line", ["baltic_station", "gatchina"]),
    ("Baltic line (to Reval)", ["gatchina", "narva", "reval"]),
    ("Warsaw line", ["warsaw_station", "gatchina", "luga", "pskov", "dvinsk"]),
    ("Pskov-Riga line", ["pskov", "riga"]),
    ("Vitebsk line", ["vitebsk_station", "tsarskoe_selo", "dno", "novosokolniki", "vitebsk", "orsha", "mogilev", "zhlobin", "gomel", "bakhmach", "kiev"]),
    ("Pskov-Dno line", ["pskov", "dno"]),
    ("Bologoye-Dno line", ["dno", "bologoye"]),
    ("Nikolaevsky line", ["nikolaevsky_station", "chudovo", "bologoye", "tver", "moscow"]),
    ("Novgorod branch", ["chudovo", "novgorod"]),
    ("Northern line", ["nikolaevsky_station", "mga", "zvanka", "vologda", "yaroslavl", "moscow"]),
    ("Murmansk railway", ["zvanka", "petrozavodsk", "murmansk"]),
    ("Archangel line", ["vologda", "arkhangelsk"]),
    ("Perm line", ["vologda", "vyatka", "perm", "ekaterinburg", "chelyabinsk"]),
    ("Moscow-Minsk line", ["moscow", "smolensk", "orsha", "minsk", "baranovichi"]),
    ("Moscow-Kursk line", ["moscow", "tula", "orel", "kursk", "kharkov", "rostov", "novocherkassk"]),
    ("Kursk-Kiev line", ["kursk", "bakhmach"]),
    ("Ryazan-Ural line", ["moscow", "ryazan", "kozlov", "voronezh", "rostov"]),
    ("Tsaritsyn line", ["voronezh", "povorino", "tsaritsyn"]),
    ("Moscow-Kazan line", ["ryazan", "ruzaevka", "kazan"]),
    ("Samara line", ["ruzaevka", "penza", "syzran", "samara", "ufa", "chelyabinsk", "omsk"]),
    ("Orenburg line", ["samara", "orenburg"]),
]
CLOSED_AT_START = {  # edge id: why
    "rail:pskov-riga": "Riga is behind German lines",
}
SEA = [  # (a, b) - sea and river routes
    ("kronstadt", "neva_quays"), ("kronstadt", "oranienbaum"), ("kronstadt", "helsingfors"),
    ("kronstadt", "reval"), ("helsingfors", "reval"),
]
ROADS = [  # roads that matter outside the rail lines
    ("putilov_works", "pulkovo"), ("pulkovo", "tsarskoe_selo"), ("tsarskoe_selo", "gatchina"),
    ("vitebsk_station", "putilov_works"),
]
BARRACKS_NAMES = {
    "red_guards": "Vyborg-side factory district (Red Guard staff)",
    "keksgolmsky": "Keksgolmsky barracks (Yakubovicha Street)",
    "izmailovsky": "Izmailovsky regimental quarter",
    "semyonovsky": "Semyonovsky regimental quarter",
    "don_cossacks": "Cossack barracks (Obvodny Canal)",
    "junkers": "Military schools (Petrograd side)",
    "armored_cars": "Mikhailovsky Manege (armored cars)",
    "pavlovsky": "Pavlovsky barracks (Field of Mars)",
}
NEIGHBOURS = 3  # street links per city place, to its nearest same-bank places


def hav_km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def main():
    m = json.loads((SETTING / "map.json").read_text(encoding="utf-8"))
    pins = {l["id"]: (l["name"], l.get("kind") or "landmark", l["pinned"]) for l in m["landmarks"] if l.get("pinned")}
    for uid, u in m["units"].items():
        if u.get("pinned"):
            pins[uid] = (u.get("where") or u["name"], "barracks", u["pinned"])
    nodes = {}
    for nid, area in CITY.items():
        name, kind, p = pins[nid]
        name = name[0].upper() + name[1:]
        if kind == "barracks":
            name = BARRACKS_NAMES.get(nid) or f"{m['units'][nid]['name']} barracks"
        nodes[nid] = {"name": name, "kind": kind, "scale": "city", "area": area, "lat": p["lat"], "lng": p["lng"]}
    for nid, (name, kind, area, lat, lng, note) in EXTRA_CITY.items():
        nodes[nid] = {"name": name, "kind": kind, "scale": "city", "area": area, "lat": lat, "lng": lng, "note": note}
    for nid, (name, kind, lat, lng, note) in OUTSIDE.items():
        nodes[nid] = {"name": name, "kind": kind, "scale": "country", "area": "outside", "lat": lat, "lng": lng, "note": note}

    edges = {}

    def add(eid, a, b, mode, name=None, **extra):
        km = hav_km((nodes[a]["lat"], nodes[a]["lng"]), (nodes[b]["lat"], nodes[b]["lng"])) * DETOUR[mode]
        edges[eid] = {"a": a, "b": b, "mode": mode, "km": round(max(km, 0.15), 2), **({"name": name} if name else {}), **extra}

    # streets: each city place to its nearest same-bank neighbours (bridgeheads included)
    city_ids = [n for n, v in nodes.items() if v["scale"] == "city"]
    for nid in city_ids:
        same = [o for o in city_ids if o != nid and nodes[o]["area"] == nodes[nid]["area"]]
        same.sort(key=lambda o: hav_km((nodes[nid]["lat"], nodes[nid]["lng"]), (nodes[o]["lat"], nodes[o]["lng"])))
        for o in same[:NEIGHBOURS]:
            a, b = sorted([nid, o])
            add(f"street:{a}-{b}", a, b, "street")
    for eid, name, a, b, raisable in BRIDGES:
        add(eid, a, b, "bridge", name, raisable=raisable)
    for line, stops in RAIL:
        for a, b in zip(stops, stops[1:]):
            add(f"rail:{a}-{b}", a, b, "rail", line)
    for a, b in SEA:
        add(f"sea:{a}-{b}", a, b, "sea", "by sea")
    for a, b in ROADS:
        add(f"road:{a}-{b}", a, b, "road", "road")
    for eid, why in CLOSED_AT_START.items():
        edges[eid]["closed_at_start"] = why

    initial = {uid: uid for uid in m["units"] if uid in nodes}
    initial.update({"kronstadt_sailors": "kronstadt", "womens_battalion": "levashovo", "peter_paul": "peter_and_paul_fortress"})
    out = {
        "_comment": "GENERATED by tools/build_petrograd_geography.py - edit the tables there, not this file. "
                    "Approximate travel network for geography.py: city places by bank joined by streets and "
                    "bridges, the 1917 railway network, and sea routes. km already includes a detour factor.",
        "speeds_kmh": {
            "foot": {"default": 4.0, "cossack": 9.0, "armor": 14.0, "cadet": 4.5, "player": 5.0},
            "rail": 25.0, "sea": 15.0,
        },
        "boarding_hours": {"rail": 4.0, "sea": 2.0},
        "same_scene_hours": 12,
        "areas": {
            "left_bank": "The centre and south (Admiralty, Liteyny, Smolny, Moscow and Narva districts)",
            "vasilievsky": "Vasilievsky Island", "petrograd_side": "The Petrograd side",
            "fortress": "Hare Island (the Peter and Paul Fortress)", "vyborg_side": "The Vyborg side",
            "outside": "Outside the city",
        },
        "player_start": "kresty_prison",
        "initial_positions": initial,
        "nodes": nodes,
        "edges": edges,
    }
    (SETTING / "geography.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(nodes)} nodes, {len(edges)} edges")


if __name__ == "__main__":
    main()
