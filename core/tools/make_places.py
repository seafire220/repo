"""Make core/companion/data/places.tsv.gz: the towns the settings page can find, so the owner can say where
the weather is for without typing anything that leaves the device (weather.py).

From GeoNames (geonames.org, CC BY 4.0): every place with 15,000 people or more, and every capital. Download
cities15000.zip (unzip it), admin1CodesASCII.txt and countryInfo.txt from
https://download.geonames.org/export/dump/, then:

    python3 core/tools/make_places.py cities15000.txt admin1CodesASCII.txt countryInfo.txt

The file (gzip, UTF-8, tab-separated) has comment lines (#), a line per country ("@" code, name), then a line
per place: name, state or region, country code, latitude, longitude (two decimals, about a kilometre),
population, time zone.
"""
import datetime
import gzip
import os
import sys

OUT = os.path.join(os.path.dirname(__file__), "..", "companion", "data", "places.tsv.gz")


def clean(text):
    return " ".join(str(text).replace("\t", " ").split())


def main(cities, admin1, countries, out=OUT):
    regions = {}
    with open(admin1, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2:
                regions[parts[0]] = clean(parts[1])
    names = {}
    with open(countries, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) > 4 and len(parts[0]) == 2:
                names[parts[0]] = clean(parts[4])
    rows = []
    with open(cities, encoding="utf-8") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 18:
                continue
            name, country = clean(p[1]), p[8]
            region = regions.get("%s.%s" % (country, p[10]), "")
            try:
                lat, lon, people = float(p[4]), float(p[5]), int(p[14] or 0)
            except ValueError:
                continue
            if not name or country not in names:
                continue
            rows.append((name, region, country, "%.2f" % lat, "%.2f" % lon, str(people), p[17]))
    rows.sort(key=lambda r: (-int(r[5]), r[0]))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    used = sorted({r[2] for r in rows})
    with gzip.open(out, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write("# Places with 15,000 people or more, and every capital, from GeoNames (geonames.org), CC BY 4.0.\n")
        f.write("# Made by core/tools/make_places.py on %s: %d places in %d countries.\n"
                % (datetime.date.today().isoformat(), len(rows), len(used)))
        for code in used:
            f.write("@%s\t%s\n" % (code, names[code]))
        for row in rows:
            f.write("\t".join(row) + "\n")
    print("%d places in %d countries -> %s (%d KB)" % (len(rows), len(used), out, os.path.getsize(out) // 1024))


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
