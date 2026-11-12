"""Build emoji/emoji-data.json for write.html's picker from emojibase-data.

    npm pack emojibase-data@<version> && tar xzf emojibase-data-*.tgz
    python3 local/build_emoji.py package/en/data.json emoji/emoji-data.json

Lives in local/ because the deploy does not publish it.

Format (compact on purpose, it is fetched once when the picker opens):
  {"source": ..., "groups": [[name, [[emoji, search, version, skins?], ...]], ...]}
  search  -- label and tags, lowercased, space-separated
  version -- Unicode emoji version, so the page can test support for new ones
  skins   -- the five single-tone variants, light to dark, when there are all five
"""
import json, sys

src, out = sys.argv[1], sys.argv[2]
data = json.load(open(src))

GROUPS = {0: "Smileys & emotion", 1: "People & body", 3: "Animals & nature", 4: "Food & drink",
          5: "Travel & places", 6: "Activities", 7: "Objects", 8: "Symbols", 9: "Flags"}

groups = {g: [] for g in GROUPS}
skipped = 0
for e in sorted(data, key=lambda e: e.get("order", 1e9)):
    g = e.get("group")
    if g not in GROUPS:          # skin-tone/hair components, bare regional indicators
        skipped += 1
        continue
    words = [e["label"]] + e.get("tags", [])
    search = " ".join(dict.fromkeys(w.lower() for w in words))
    row = [e["emoji"], search, e.get("version", 0)]
    tones = {s["tone"]: s["emoji"] for s in e.get("skins", []) if isinstance(s.get("tone"), int)}
    if all(t in tones for t in (1, 2, 3, 4, 5)):
        row.append([tones[t] for t in (1, 2, 3, 4, 5)])
    groups[g].append(row)

doc = {
    "source": "emojibase-data 18.0.0 (MIT), en/data.json -- see emoji/LICENSE.txt",
    "groups": [[GROUPS[g], rows] for g, rows in groups.items()],
}
json.dump(doc, open(out, "w"), ensure_ascii=False, separators=(",", ":"))
total = sum(len(r) for r in groups.values())
print(total, "emoji;", sum(1 for r in groups.values() for x in r if len(x) > 3), "with skin tones;", skipped, "skipped")
