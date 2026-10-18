"""Conservative, deterministic decisions shared by face and edge references."""

def choose(candidates, allow_moved=True):
    candidates = sorted(candidates, key=lambda c: (c["score"], c["index"]))
    exact = [c for c in candidates if c["exact"]]
    if len(exact) == 1:
        status, index = "exact", exact[0]["index"]
    elif len(exact) > 1:
        status, index = "ambiguous", None
    elif not allow_moved or not candidates or candidates[0]["score"] > 1.0:
        status, index = "missing", None
    elif len(candidates) > 1 and candidates[0]["score"] >= 0.6 * candidates[1]["score"]:
        status, index = "ambiguous", None
    else:
        status, index = "moved", candidates[0]["index"]
    return {"status": status, "index": index, "candidates": candidates}
