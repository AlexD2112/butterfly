"""
Bridge ends (2026-11-03) are the ends of each bridge's way in OpenStreetMap. These bridges all
stand where their 1917 predecessors did (most were rebuilt in place), so the modern line is the
one to use.

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
DETOUR = {"street": 1.3, "road": 1.25, "rail": 1.2, "sea": 1.1, "river": 1.5, "bridge": 1.0}

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
    "okhtinsky_bridge_s": ("Okhtinsky Bridge (Smolny end)", "bridgehead", "left_bank", 59.94270, 30.39820, ""),
    "okhtinsky_bridge_n": ("Okhtinsky Bridge (Okhta end)", "bridgehead", "vyborg_side", 59.94270, 30.40420, ""),
    "grenadersky_bridge_w": ("Grenadersky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.96759, 30.33266, ""),
    "grenadersky_bridge_e": ("Grenadersky Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.96819, 30.33625, ""),
    "tuchkov_bridge_s": ("Tuchkov Bridge (island end)", "bridgehead", "vasilievsky", 59.94810, 30.28454, ""),
    "tuchkov_bridge_n": ("Tuchkov Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.94972, 30.28678, ""),
    "palace_bridge_s": ("Palace Bridge (Admiralty end)", "bridgehead", "left_bank", 59.94000, 30.30969, ""),
    "palace_bridge_n": ("Palace Bridge (Strelka end)", "bridgehead", "vasilievsky", 59.94208, 30.30665, ""),
    "nikolaevsky_bridge_s": ("Nikolaevsky Bridge (Admiralty end)", "bridgehead", "left_bank", 59.93328, 30.29087, ""),
    "nikolaevsky_bridge_n": ("Nikolaevsky Bridge (island end)", "bridgehead", "vasilievsky", 59.93616, 30.28845, ""),
    "troitsky_bridge_s": ("Troitsky Bridge (Field of Mars end)", "bridgehead", "left_bank", 59.94649, 30.32951, ""),
    "troitsky_bridge_n": ("Troitsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.95103, 30.32544, ""),
    "liteyny_bridge_s": ("Liteyny Bridge (Liteyny end)", "bridgehead", "left_bank", 59.95010, 30.34920, ""),
    "liteyny_bridge_n": ("Liteyny Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.95360, 30.35000, ""),
    "birzhevoy_bridge_w": ("Birzhevoy Bridge (Strelka end)", "bridgehead", "vasilievsky", 59.94522, 30.30317, ""),
    "birzhevoy_bridge_e": ("Birzhevoy Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.94736, 30.30346, ""),
    "sampsonievsky_bridge_w": ("Sampsonievsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.95795, 30.33513, ""),
    "sampsonievsky_bridge_e": ("Sampsonievsky Bridge (Vyborg end)", "bridgehead", "vyborg_side", 59.95800, 30.33915, ""),
    "ioannovsky_gate": ("Ioannovsky Gate (fortress end)", "bridgehead", "fortress", 59.95142, 30.32191, ""),
    "ioannovsky_bridge_n": ("Ioannovsky Bridge (Petrograd-side end)", "bridgehead", "petrograd_side", 59.95263, 30.32267, ""),
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
    ("ioannovsky_bridge", "Ioannovsky Bridge (to the fortress)", "ioannovsky_bridge_n", "ioannovsky_gate", False),
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
    # added 2026-11-03 for the country-wide order of battle and the Civil War theatres
    "valka": ("Valka", "junction", 57.7750, 26.0200, "Junction behind the 12th Army's front."),
    "wenden": ("Wenden", "front", 57.3120, 25.2700, "Headquarters area of the Latvian Riflemen, 12th Army."),
    "ostrov": ("Ostrov", "town", 57.3450, 28.3500, "South of Pskov; the 3rd Cavalry Corps was quartered here after Kornilov."),
    "bykhov": ("Bykhov", "town", 53.5200, 30.2500, "Where Kornilov and his generals are held, guarded by the Tekke regiment."),
    "bobruisk": ("Bobruisk", "town", 53.1400, 29.2200, "Fortress town; the 1st Polish Corps is forming nearby."),
    "nizhny_novgorod": ("Nizhny Novgorod", "city", 56.3300, 44.0000, "Volga river port and fair city."),
    "tikhoretskaya": ("Tikhoretskaya", "junction", 45.8500, 40.1200, "Kuban rail junction."),
    "ekaterinodar": ("Ekaterinodar", "city", 45.0400, 38.9800, "Capital of the Kuban Cossacks."),
    "novonikolaevsk": ("Novonikolaevsk", "town", 55.0300, 82.9200, "Where the Trans-Siberian crosses the Ob."),
    "krasnoyarsk": ("Krasnoyarsk", "town", 56.0100, 92.8700, "Central Siberia."),
    "irkutsk": ("Irkutsk", "city", 52.2900, 104.3000, "Eastern Siberia, by Lake Baikal."),
    "chita": ("Chita", "town", 52.0300, 113.5000, "Transbaikalia; the Transbaikal Cossacks."),
    "harbin": ("Harbin", "city", 45.7500, 126.6500, "Russian railway town in Manchuria (Chinese Eastern Railway)."),
    "vladivostok": ("Vladivostok", "port", 43.1200, 131.9000, "Pacific port at the end of the Trans-Siberian."),
}
# Which part of the country each place outside the city belongs to - the GM sees forces grouped this way.
REGION_OF = {
    "petrograd_region": ["kronstadt", "oranienbaum", "levashovo", "pulkovo", "tsarskoe_selo", "gatchina", "beloostrov"],
    "finland_baltic": ["terijoki", "vyborg", "helsingfors", "narva", "reval"],
    "northern_front": ["luga", "pskov", "dno", "ostrov", "dvinsk", "riga", "valka", "wenden", "novosokolniki", "novgorod", "chudovo"],
    "western_front": ["vitebsk", "orsha", "mogilev", "bykhov", "minsk", "baranovichi", "smolensk", "zhlobin", "gomel", "bobruisk"],
    "north": ["mga", "zvanka", "petrozavodsk", "murmansk", "vologda", "arkhangelsk", "vyatka"],
    "centre": ["bologoye", "tver", "moscow", "yaroslavl", "tula", "orel", "kursk", "ryazan", "kozlov", "voronezh", "nizhny_novgorod"],
    "ukraine": ["kiev", "bakhmach", "kharkov"],
    "south": ["rostov", "novocherkassk", "povorino", "tsaritsyn", "tikhoretskaya", "ekaterinodar"],
    "volga_urals": ["ruzaevka", "kazan", "penza", "syzran", "samara", "orenburg", "ufa", "perm", "ekaterinburg", "chelyabinsk"],
    "siberia": ["omsk", "novonikolaevsk", "krasnoyarsk", "irkutsk", "chita", "harbin", "vladivostok"],
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
    ("Riga-Pskov line", ["valka", "wenden"]),
    ("Warsaw line (south of Pskov)", ["pskov", "ostrov"]),
    ("Mogilev-Zhlobin line", ["mogilev", "bykhov"]),
    ("Libava-Romny line", ["minsk", "bobruisk", "gomel"]),
    ("Moscow-Nizhny line", ["moscow", "nizhny_novgorod"]),
    ("Vladikavkaz railway", ["rostov", "tikhoretskaya", "ekaterinodar"]),
    ("Tsaritsyn-Tikhoretskaya line", ["tsaritsyn", "tikhoretskaya"]),
    ("Trans-Siberian railway", ["omsk", "novonikolaevsk", "krasnoyarsk", "irkutsk", "chita"]),
    ("Chinese Eastern Railway", ["chita", "harbin", "vladivostok"]),
]
# the old straight Pskov-Riga and Warsaw-line legs are rerouted through the new junctions below
RAIL_REPLACE = {("pskov", "riga"): ["pskov", "valka", "wenden", "riga"], ("pskov", "dvinsk"): ["pskov", "ostrov", "dvinsk"],
                ("mogilev", "zhlobin"): ["mogilev", "bykhov", "zhlobin"]}
CLOSED_AT_START = {  # edge id: why
    "rail:wenden-riga": "Riga is behind German lines",
}
SEA = [  # (a, b, can walk it over the ice in deep winter)
    ("kronstadt", "neva_quays", True), ("kronstadt", "oranienbaum", True), ("kronstadt", "helsingfors", False),
    ("kronstadt", "reval", False), ("helsingfors", "reval", False),
]
RIVERS = [  # (river, [ports in order]) - navigable April to November only
    ("Volga", ["yaroslavl", "nizhny_novgorod", "kazan", "samara", "syzran", "tsaritsyn"]),
    ("Northern Dvina", ["vologda", "arkhangelsk"]),
]
NO_ROADS = {"kronstadt", "riga"}  # an island; a city behind German lines
WATER_BETWEEN = {frozenset(p) for p in (("helsingfors", "reval"), ("helsingfors", "narva"), ("murmansk", "vyborg"))}
COUNTRY_ROAD_NEIGHBOURS = 3  # every place outside the city gets a road to its nearest few - armies can leave the railway
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
# where the country-wide order of battle (mechanics_catalog.json: russia_forces) starts
COUNTRY_POSITIONS = {
    "latvian_riflemen": "wenden", "fifth_army": "dvinsk", "krasnov_corps": "ostrov", "native_division": "dno",
    "baltic_fleet": "helsingfors", "stavka_guard": "mogilev", "tekke_regiment": "bykhov", "western_front_reserves": "minsk",
    "polish_corps": "bobruisk", "czechoslovak_corps": "kiev", "ukrainian_units": "kiev", "moscow_garrison": "moscow",
    "moscow_red_guards": "moscow", "moscow_junkers": "moscow", "don_host": "novocherkassk", "kuban_host": "ekaterinodar",
    "orenburg_cossacks": "orenburg", "donbass_red_guards": "kharkov", "tsaritsyn_garrison": "tsaritsyn",
    "kazan_garrison": "kazan", "urals_militias": "ekaterinburg",
}
# Historical defaults for units the player isn't driving (Julian dates). Each fires once, on its date,
# only if its conditions still hold - if the campaign has already changed the unit (won it over,
# destroyed it, moved it), history doesn't happen to it. The GM is told when one fires.
SCHEDULE = [
    {"id": "native_division_south", "date": "1917-09-25", "action": "move", "unit": "native_division", "to": "ekaterinodar",
     "if": {"loyalty_max": 0}, "message": "The Caucasian Native Division, disgraced by the Kornilov affair, is entrained for the North Caucasus."},
    {"id": "alekseev_organization", "date": "1917-11-02", "action": "appear", "at": "novocherkassk",
     "unit": {"id": "volunteer_army", "name": "Alekseev Organization (officer volunteers)", "kind": "cadet", "strength": 600,
              "readiness": 4, "loyalty": -3, "note": "Officers and cadets gathering at Novocherkassk under General Alekseev."},
     "message": "General Alekseev reaches Novocherkassk and begins gathering officers into a volunteer force."},
    {"id": "kornilov_escapes", "date": "1917-11-19", "action": "move", "unit": "tekke_regiment", "to": "novocherkassk", "foot_only": True,
     "if": {"loyalty_max": -2}, "message": "Kornilov walks out of Bykhov with the Tekke regiment and rides for the Don."},
    {"id": "volunteer_army_founded", "date": "1917-12-25", "action": "reinforce", "unit": "volunteer_army", "strength_add": 2500,
     "name": "Volunteer Army (Alekseev, Kornilov)", "if": {"loyalty_max": -2},
     "message": "The Volunteer Army is formally constituted at Novocherkassk under Alekseev and Kornilov."},
    {"id": "czech_legion_east", "date": "1918-03-01", "action": "move", "unit": "czechoslovak_corps", "to": "penza",
     "if": {"loyalty_max": 1}, "message": "With Ukraine lost to the Germans, the Czechoslovak Corps entrains east, meaning to sail from Vladivostok for France."},
    {"id": "czech_legion_revolt", "date": "1918-05-12", "action": "split", "unit": "czechoslovak_corps", "at": "chelyabinsk",
     "strength": 12000, "new": {"id": "czech_chelyabinsk", "name": "Czechoslovak Legion (Chelyabinsk group)"},
     "loyalty": -2, "if": {"loyalty_max": 0},
     "message": "Fighting breaks out with the Czechoslovak Legion at Chelyabinsk; its echelons along the Trans-Siberian turn on the Soviets."},
]
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
        region = next((r for r, ids in REGION_OF.items() if nid in ids), None)
        assert region, f"{nid} has no region"
        nodes[nid] = {"name": name, "kind": kind, "scale": "country", "area": region, "lat": lat, "lng": lng, "note": note}

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
        expanded = []
        for a, b in zip(stops, stops[1:]):
            seg = RAIL_REPLACE.get((a, b)) or [a, b]
            expanded += seg if not expanded else seg[1:]
        for a, b in zip(expanded, expanded[1:]):
            add(f"rail:{a}-{b}", a, b, "rail", line)
    for a, b, ice in SEA:
        add(f"sea:{a}-{b}", a, b, "sea", "by sea", freezes=True, **({"ice_walk": True} if ice else {}))
    for river, ports in RIVERS:
        for a, b in zip(ports, ports[1:]):
            add(f"river:{a}-{b}", a, b, "river", f"the {river}", freezes=True)
    for a, b in ROADS:
        add(f"road:{a}-{b}", a, b, "road", "road")
    # country roads: generated, so an army can march off the railway (slowly) - not drawn on the map
    country = [n for n, v in nodes.items() if v["scale"] == "country"]
    for nid in country:
        near = sorted((o for o in country if o != nid), key=lambda o: hav_km((nodes[nid]["lat"], nodes[nid]["lng"]), (nodes[o]["lat"], nodes[o]["lng"])))
        for o in near[:COUNTRY_ROAD_NEIGHBOURS]:
            a, b = sorted([nid, o])
            if (f"road:{a}-{b}" not in edges and not ({a, b} & NO_ROADS) and frozenset((a, b)) not in WATER_BETWEEN
                    and hav_km((nodes[a]["lat"], nodes[a]["lng"]), (nodes[b]["lat"], nodes[b]["lng"])) < 1100):
                add(f"road:{a}-{b}", a, b, "road", "road", generated=True)
    # the city's southern and northern edges meet the country roads
    for a, b in (("pulkovo", "putilov_works"), ("levashovo", "red_guards"), ("okhta", "mga")):
        a2, b2 = sorted([a, b])
        if f"road:{a2}-{b2}" not in edges:
            add(f"road:{a2}-{b2}", a2, b2, "road", "road")
    for eid, why in CLOSED_AT_START.items():
        edges[eid]["closed_at_start"] = why

    initial = {uid: uid for uid in m["units"] if uid in nodes}
    initial.update({"kronstadt_sailors": "kronstadt", "womens_battalion": "levashovo", "peter_paul": "peter_and_paul_fortress"})
    initial.update(COUNTRY_POSITIONS)
    out = {
        "_comment": "GENERATED by tools/build_petrograd_geography.py - edit the tables there, not this file. "
                    "Approximate travel network for geography.py: city places by bank joined by streets and "
                    "bridges, the 1917 railway network, and sea routes. km already includes a detour factor.",
        "speeds_kmh": {
            "foot": {"default": 4.0, "cossack": 7.0, "cavalry": 7.0, "armor": 14.0, "cadet": 4.5, "player": 5.0},
            "rail": 25.0, "sea": 15.0, "river": 12.0,
        },
        "boarding_hours": {"rail": 4.0, "sea": 2.0, "river": 3.0},
        "march_hours_per_day": 8,
        "long_march_km": 15,
        "freeze": {"river": ["12", "01", "02", "03"], "sea_ice_walk": ["01", "02", "03"]},
        "same_scene_hours": 12,
        # couriers.py: a telegram is fast but goes through the government's own Telegraph
        "telegraph": {"hours": 2, "read_by": ["government"], "from": ["central_telegraph"]},
        "areas": {
            "left_bank": "The centre and south (Admiralty, Liteyny, Smolny, Moscow and Narva districts)",
            "vasilievsky": "Vasilievsky Island", "petrograd_side": "The Petrograd side",
            "fortress": "Hare Island (the Peter and Paul Fortress)", "vyborg_side": "The Vyborg side",
            "petrograd_region": "Around Petrograd", "finland_baltic": "Finland and Estonia",
            "northern_front": "The Northern Front", "western_front": "The Western Front and Stavka",
            "north": "The North", "centre": "Moscow and the centre", "ukraine": "Ukraine",
            "south": "The Don, the Kuban and the lower Volga", "volga_urals": "The Volga and the Urals",
            "siberia": "Siberia and the Far East",
        },
        "player_start": "kresty_prison",
        "initial_positions": initial,
        "scheduled": SCHEDULE,
        "nodes": nodes,
        "edges": edges,
    }
    (SETTING / "geography.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(nodes)} nodes, {len(edges)} edges")


if __name__ == "__main__":
    main()
