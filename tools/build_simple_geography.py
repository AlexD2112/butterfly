"""
Movement graphs for the settings beyond Petrograd (2026-11-03) - smaller than Petrograd's: towns,
roads and sea lanes at the scale of a campaign, so the GM can ask how long a ride, a march or a
crossing takes instead of inventing it (see geography.py for why). No city streets, no units yet:
these settings' armies are org trees, not forces trackers, so only the player moves on the map.

    python tools/build_simple_geography.py

Positions are approximate (to a few km) and the road lists are the main routes of the period,
simplified. km = great-circle distance x a detour factor.
"""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DETOUR = {"road": 1.25, "sea": 1.1, "river": 1.4}

SETTINGS = {
    "rome_59_bce": {
        "player_start": "roma",
        "speeds_kmh": {"foot": {"default": 4.5, "player": 6.0}, "sea": 8.0},  # a senator travels by litter, carriage or horse
        "areas": {"latium": "Rome and Latium", "italy": "Italy", "cisalpine": "Cisalpine Gaul",
                  "gaul": "Transalpine Gaul", "overseas": "Across the sea"},
        "nodes": {
            "roma": ("Rome", 41.893, 12.483, "latium", "The city - Forum, Senate house, Caesar's house on the Sacra Via."),
            "ostia": ("Ostia", 41.756, 12.290, "latium", "Rome's port at the Tiber mouth."),
            "capua": ("Capua", 41.080, 14.250, "italy", "Campania's great city; Pompey's veterans want land here."),
            "neapolis": ("Neapolis", 40.850, 14.270, "italy", "Greek city on the bay; villas of the rich."),
            "beneventum": ("Beneventum", 41.130, 14.780, "italy", "On the Via Appia across the Apennines."),
            "tarentum": ("Tarentum", 40.470, 17.240, "italy", ""),
            "brundisium": ("Brundisium", 40.640, 17.940, "italy", "Port for Greece and the East."),
            "arretium": ("Arretium", 43.460, 11.880, "italy", "On the Via Cassia in Etruria."),
            "pisae": ("Pisae", 43.720, 10.400, "italy", ""),
            "luca": ("Luca", 43.840, 10.500, "cisalpine", "Last town of Caesar's future province on the Italian side."),
            "ariminum": ("Ariminum", 44.060, 12.570, "italy", "End of the Via Flaminia; the Rubicon is just north."),
            "bononia": ("Bononia", 44.490, 11.340, "cisalpine", "On the Via Aemilia."),
            "ravenna": ("Ravenna", 44.420, 12.200, "cisalpine", ""),
            "placentia": ("Placentia", 45.050, 9.690, "cisalpine", ""),
            "mediolanum": ("Mediolanum", 45.460, 9.190, "cisalpine", ""),
            "aquileia": ("Aquileia", 45.770, 13.370, "cisalpine", "Frontier town facing Illyricum; legions winter here."),
            "genua": ("Genua", 44.410, 8.930, "cisalpine", ""),
            "massilia": ("Massilia", 43.300, 5.370, "gaul", "Rome's old Greek ally on the Gallic coast."),
            "narbo": ("Narbo", 43.180, 3.000, "gaul", "Capital of the Province."),
            "vienna": ("Vienna (Vienne)", 45.520, 4.870, "gaul", "Allobrogan town on the Rhone."),
            "genava": ("Genava", 46.200, 6.150, "gaul", "Allobrogan border town on the lake, facing the Helvetii."),
            "bibracte": ("Bibracte", 46.920, 4.040, "gaul", "Hill-fort capital of the Aedui, Rome's allies."),
            "vesontio": ("Vesontio", 47.240, 6.020, "gaul", "Chief town of the Sequani."),
            "dyrrachium": ("Dyrrachium", 41.320, 19.450, "overseas", "Landing for the Via Egnatia to the East."),
            "athenae": ("Athens", 37.970, 23.720, "overseas", ""),
            "syracusae": ("Syracuse", 37.070, 15.290, "overseas", "Capital of the province of Sicily."),
            "utica": ("Utica", 37.060, 10.060, "overseas", "Capital of the province of Africa."),
            "alexandria": ("Alexandria", 31.200, 29.920, "overseas", "Ptolemy Auletes is paying Caesar and Pompey to be recognized."),
        },
        "roads": [("roma", "ostia"), ("roma", "capua", "Via Appia"), ("capua", "neapolis"), ("capua", "beneventum", "Via Appia"),
                  ("beneventum", "tarentum", "Via Appia"), ("tarentum", "brundisium", "Via Appia"), ("beneventum", "brundisium"),
                  ("roma", "arretium", "Via Cassia"), ("arretium", "bononia"), ("roma", "pisae", "Via Aurelia"), ("pisae", "luca"),
                  ("pisae", "genua"), ("roma", "ariminum", "Via Flaminia"), ("ariminum", "bononia", "Via Aemilia"),
                  ("bononia", "placentia", "Via Aemilia"), ("ariminum", "ravenna"), ("ravenna", "aquileia"),
                  ("placentia", "mediolanum"), ("placentia", "genua"), ("mediolanum", "aquileia"), ("luca", "bononia"),
                  ("genua", "massilia", "the coast road"), ("massilia", "narbo", "Via Domitia"), ("massilia", "vienna"),
                  ("mediolanum", "genava", "over the Alps"), ("vienna", "genava"), ("vienna", "bibracte"),
                  ("bibracte", "vesontio"), ("genava", "vesontio")],
        "sea": [("ostia", "massilia"), ("ostia", "syracusae"), ("ostia", "utica"), ("brundisium", "dyrrachium"),
                ("brundisium", "athenae"), ("syracusae", "alexandria"), ("neapolis", "syracusae"), ("athenae", "alexandria")],
    },
    "britannia_876": {
        "player_start": "wilton",
        "speeds_kmh": {"foot": {"default": 4.0, "player": 6.0}, "sea": 7.0},  # a king rides
        "areas": {"wessex": "Wessex", "the_west": "Somerset, Devon and the west", "kent": "Kent",
                  "mercia": "Mercia", "danelaw": "The Danish north and east"},
        "nodes": {
            "wilton": ("Wilton", 51.080, -1.860, "wessex", "A royal hall of the West Saxon kings."),
            "winchester": ("Winchester", 51.060, -1.310, "wessex", "The West Saxon royal city."),
            "southampton": ("Hamwic (Southampton)", 50.900, -1.400, "wessex", "Trading port."),
            "reading": ("Reading", 51.450, -0.970, "wessex", "Where the Danes dug in, in 871."),
            "wimborne": ("Wimborne", 50.800, -1.990, "wessex", "Minster where Alfred's brother Æthelred lies."),
            "wareham": ("Wareham", 50.690, -2.110, "wessex", "Walled burh the Danes seized last year."),
            "dorchester": ("Dorchester", 50.710, -2.440, "wessex", ""),
            "sherborne": ("Sherborne", 50.950, -2.520, "wessex", "Bishop's seat; Alfred's brothers are buried here."),
            "exeter": ("Exeter", 50.720, -3.530, "the_west", "Old Roman walls; Guthrum is inside."),
            "athelney": ("Athelney", 51.060, -2.930, "the_west", "An island in the Somerset marshes."),
            "glastonbury": ("Glastonbury", 51.150, -2.710, "the_west", "Abbey on its tor above the levels."),
            "countisbury": ("Countisbury", 51.230, -3.790, "the_west", "Old hill-fort above the north Devon coast."),
            "bath": ("Bath", 51.380, -2.360, "the_west", ""),
            "chippenham": ("Chippenham", 51.460, -2.120, "the_west", "A royal estate."),
            "edington": ("Edington", 51.280, -2.100, "the_west", "Below the scarp of Salisbury Plain."),
            "london": ("London", 51.510, -0.100, "mercia", "Mercian, and in Danish hands since 871."),
            "canterbury": ("Canterbury", 51.280, 1.080, "kent", "The archbishop's city."),
            "gloucester": ("Gloucester", 51.860, -2.240, "mercia", ""),
            "worcester": ("Worcester", 52.190, -2.220, "mercia", "Bishop Wærferth's see."),
            "tamworth": ("Tamworth", 52.630, -1.690, "mercia", "The Mercian kings' seat."),
            "repton": ("Repton", 52.840, -1.550, "mercia", "Where the Great Army wintered in 873."),
            "leicester": ("Leicester", 52.630, -1.130, "danelaw", ""),
            "cambridge": ("Cambridge", 52.210, 0.120, "danelaw", "Guthrum's base before he came south."),
            "lincoln": ("Lincoln", 53.230, -0.540, "danelaw", ""),
            "york": ("York", 53.960, -1.080, "danelaw", "Halfdan's Northumbrian kingdom."),
        },
        "roads": [("wilton", "winchester"), ("winchester", "southampton"), ("winchester", "reading"), ("reading", "london"),
                  ("london", "canterbury"), ("wilton", "wimborne"), ("wimborne", "wareham"), ("wareham", "dorchester"),
                  ("dorchester", "exeter"), ("wilton", "sherborne"), ("sherborne", "dorchester"), ("sherborne", "athelney"),
                  ("athelney", "glastonbury"), ("athelney", "exeter"), ("exeter", "countisbury"), ("glastonbury", "bath"),
                  ("wilton", "edington"), ("edington", "chippenham"), ("chippenham", "bath"), ("athelney", "edington"),
                  ("bath", "gloucester"), ("gloucester", "worcester"), ("worcester", "tamworth"), ("tamworth", "repton"),
                  ("tamworth", "leicester"), ("leicester", "lincoln"), ("lincoln", "york"), ("london", "cambridge"),
                  ("cambridge", "lincoln"), ("reading", "chippenham"), ("london", "tamworth", "Watling Street")],
        "sea": [("wareham", "exeter"), ("southampton", "wareham"), ("london", "canterbury")],
    },
    "crusades_1097": {
        "player_start": "dorylaeum",
        "speeds_kmh": {"foot": {"default": 3.5, "player": 6.0}, "sea": 7.0},  # an army with ox carts and pilgrims
        "areas": {"bithynia": "Bithynia and the Bosporus", "anatolia": "The Anatolian plateau",
                  "cilicia": "Cilicia", "north_syria": "Northern Syria", "jazira": "Edessa and the Jazira",
                  "south": "Southern Syria and Palestine"},
        "nodes": {
            "constantinople": ("Constantinople", 41.010, 28.980, "bithynia", "The emperor's city."),
            "nicomedia": ("Nicomedia", 40.770, 29.920, "bithynia", ""),
            "nicaea": ("Nicaea", 40.430, 29.720, "bithynia", "Surrendered to the Byzantines on 19 June."),
            "dorylaeum": ("Dorylaeum", 39.800, 30.520, "anatolia", "Where the road from Nicaea comes down into the plain."),
            "philomelium": ("Philomelium", 38.350, 31.400, "anatolia", ""),
            "iconium": ("Iconium", 37.870, 32.490, "anatolia", "Kilij Arslan's capital."),
            "heraclea": ("Heraclea", 37.500, 34.050, "anatolia", "Where the roads split: Cilician Gates or the long way north."),
            "caesarea": ("Caesarea (Kayseri)", 38.720, 35.490, "anatolia", ""),
            "coxon": ("Coxon", 38.020, 36.500, "anatolia", "Armenian town in the Anti-Taurus."),
            "marash": ("Marash", 37.580, 36.930, "north_syria", "Armenian lordship."),
            "tarsus": ("Tarsus", 36.920, 34.890, "cilicia", "Over the Cilician Gates."),
            "adana": ("Adana", 37.000, 35.320, "cilicia", ""),
            "mamistra": ("Mamistra", 36.960, 35.620, "cilicia", ""),
            "alexandretta": ("Alexandretta", 36.590, 36.170, "cilicia", "Below the Syrian Gates."),
            "antioch": ("Antioch", 36.200, 36.160, "north_syria", "Walled city under Yaghi-Siyan."),
            "iron_bridge": ("The Iron Bridge", 36.270, 36.370, "north_syria", "Fortified bridge over the Orontes."),
            "st_simeon": ("St Simeon", 36.120, 35.930, "north_syria", "Antioch's port."),
            "harim": ("Harim", 36.210, 36.520, "north_syria", ""),
            "aleppo": ("Aleppo", 36.200, 37.160, "north_syria", "Ridwan's city."),
            "latakia": ("Latakia", 35.520, 35.790, "north_syria", ""),
            "maarrat": ("Ma'arrat al-Numan", 35.650, 36.670, "north_syria", ""),
            "tell_bashir": ("Turbessel", 36.840, 37.850, "jazira", ""),
            "edessa": ("Edessa", 37.160, 38.790, "jazira", "Armenian city under Thoros."),
            "mosul": ("Mosul", 36.340, 43.130, "jazira", "Kerbogha's city."),
            "homs": ("Homs", 34.730, 36.720, "south", ""),
            "tripoli": ("Tripoli", 34.430, 35.840, "south", ""),
            "damascus": ("Damascus", 33.510, 36.290, "south", "Duqaq's city."),
            "jaffa": ("Jaffa", 32.050, 34.750, "south", "Port for Jerusalem."),
            "jerusalem": ("Jerusalem", 31.780, 35.230, "south", "Held by the Fatimids' rivals - for now."),
        },
        "roads": [("constantinople", "nicomedia"), ("nicomedia", "nicaea"), ("nicaea", "dorylaeum"),
                  ("dorylaeum", "philomelium"), ("philomelium", "iconium"), ("iconium", "heraclea"),
                  ("heraclea", "caesarea"), ("caesarea", "coxon"), ("coxon", "marash"), ("marash", "antioch"),
                  ("heraclea", "tarsus", "the Cilician Gates"), ("tarsus", "adana"), ("adana", "mamistra"),
                  ("mamistra", "alexandretta"), ("alexandretta", "antioch", "the Syrian Gates"), ("marash", "tell_bashir"),
                  ("tell_bashir", "edessa"), ("antioch", "iron_bridge"), ("iron_bridge", "harim"), ("harim", "aleppo"),
                  ("antioch", "st_simeon"), ("antioch", "latakia"), ("antioch", "maarrat"), ("maarrat", "homs"),
                  ("homs", "damascus"), ("homs", "tripoli"), ("tripoli", "jaffa", "the coast road"), ("jaffa", "jerusalem"),
                  ("damascus", "jerusalem"), ("aleppo", "edessa"), ("edessa", "mosul"), ("aleppo", "maarrat")],
        "sea": [("constantinople", "nicomedia"), ("st_simeon", "latakia"), ("latakia", "tripoli"), ("tripoli", "jaffa"),
                ("st_simeon", "constantinople")],
    },
}


def hav_km(a, b):
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def build(key, spec):
    nodes = {nid: {"name": n, "kind": "town", "scale": "country", "area": area, "lat": lat, "lng": lng, **({"note": note} if note else {})}
             for nid, (n, lat, lng, area, note) in spec["nodes"].items()}
    edges = {}
    for mode, pairs in (("road", spec["roads"]), ("sea", spec["sea"])):
        for p in pairs:
            a, b = p[0], p[1]
            km = hav_km((nodes[a]["lat"], nodes[a]["lng"]), (nodes[b]["lat"], nodes[b]["lng"])) * DETOUR[mode]
            edges[f"{mode}:{a}-{b}"] = {"a": a, "b": b, "mode": mode, "km": round(km, 1),
                                        "name": p[2] if len(p) > 2 else ("by sea" if mode == "sea" else "road")}
    out = {
        "_comment": "GENERATED by tools/build_simple_geography.py - edit the tables there. Approximate positions; main routes of the period, simplified.",
        "speeds_kmh": {**spec["speeds_kmh"], "rail": 1.0},
        "boarding_hours": {"sea": 6.0},
        "march_hours_per_day": 8, "long_march_km": 15,
        "same_scene_hours": 12, "areas": spec["areas"], "player_start": spec["player_start"],
        "initial_positions": {}, "scheduled": [], "nodes": nodes, "edges": edges,
    }
    (ROOT / "settings" / key / "geography.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(key, len(nodes), "places,", len(edges), "routes")


if __name__ == "__main__":
    for k, v in SETTINGS.items():
        build(k, v)
