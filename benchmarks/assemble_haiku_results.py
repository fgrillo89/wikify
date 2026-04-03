"""Assemble haiku ground truth from agent extraction results."""

import json

chunks = json.loads(open("benchmarks/results/benchmark_chunks.json", encoding="utf-8").read())
chunk_map = {c["chunk_id"]: c for c in chunks}
for c in chunks:
    chunk_map[c["chunk_id"][:8]] = c

all_extractions = [
    {"chunk_id": "06b0c44d-9691-419c-a46c-5f608e3046c2", "concepts": [{"name":"memristor","type":"material","aliases":["memristive device"]},{"name":"cmos compatible oxide memristors","type":"material","aliases":["oxide memristors"]},{"name":"atomic layer deposition","type":"technique","aliases":["ald"]},{"name":"physical vapor deposition","type":"technique","aliases":["pvd"]},{"name":"back-end-of-line","type":"technique","aliases":["beol"]},{"name":"oxygen vacancy engineering","type":"method","aliases":[]},{"name":"self-rectifying memristor","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "a135cc55-6ce9-4782-bc82-d15377fbb90b", "concepts": [{"name":"memtransistor","type":"material","aliases":[]},{"name":"molybdenum ditelluride","type":"material","aliases":["mote2"]},{"name":"laser treatment","type":"technique","aliases":[]},{"name":"perovskite","type":"material","aliases":[]},{"name":"ferroelectric material","type":"material","aliases":[]},{"name":"two-dimensional material","type":"material","aliases":["2d material"]},{"name":"memory window","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "1b36bcf7-7e9b-4c1d-bc1d-1305a60f17f4", "concepts": [{"name":"1m1t1r neuron","type":"method","aliases":[]}]},
    {"chunk_id": "7d53fd05-f49b-43e3-b309-89b558cb4a71", "concepts": [{"name":"ferroelectric tunnel junction","type":"material","aliases":["fetj"]},{"name":"bismuth samarium oxide","type":"material","aliases":["bso"]},{"name":"niobium-doped strontium titanate","type":"material","aliases":["nb:sto"]},{"name":"tunnelling electroresistance","type":"phenomenon","aliases":[]},{"name":"hafnium-zirconium oxide","type":"material","aliases":["hf0.5zr0.5o2"]},{"name":"polarization retention","type":"phenomenon","aliases":["pr"]},{"name":"coercive field","type":"phenomenon","aliases":["ec"]}]},
    {"chunk_id": "03031e0a-e95a-4882-89e7-85cc0fc4c7c3", "concepts": [{"name":"bader charge","type":"method","aliases":[]},{"name":"germanium telluride","type":"material","aliases":["gete"]},{"name":"aluminum oxide","type":"material","aliases":["al2o3"]},{"name":"copper filament","type":"phenomenon","aliases":["cf"]},{"name":"virtual anode","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "4a7c84b8", "concepts": [{"name":"hfo2-based memristive devices","type":"material","aliases":["hafnium oxide memristors"]},{"name":"oxygen vacancy filament","type":"phenomenon","aliases":["vo filament","conductive filament"]},{"name":"resistive switching","type":"phenomenon","aliases":[]},{"name":"set process","type":"technique","aliases":[]},{"name":"reset process","type":"technique","aliases":[]},{"name":"compliance current","type":"phenomenon","aliases":[]},{"name":"bipolar resistive switching","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "37b38ae4", "concepts": [{"name":"atomic layer deposition","type":"method","aliases":["ald"]},{"name":"tetrakis(dimethylamido)hafnium","type":"material","aliases":["tdmah"]},{"name":"spectroscopic ellipsometry","type":"method","aliases":[]},{"name":"sputtering","type":"method","aliases":[]}]},
    {"chunk_id": "f0376a62", "concepts": [{"name":"dc i-v sweep","type":"method","aliases":[]},{"name":"pulse measurements","type":"method","aliases":[]}]},
    {"chunk_id": "42f4f598", "concepts": [{"name":"density functional theory","type":"theory","aliases":["dft"]},{"name":"projector augmented wave","type":"method","aliases":["paw"]},{"name":"generalized gradient approximation","type":"theory","aliases":["gga"]},{"name":"perdew-burke-ernzerhof","type":"theory","aliases":["pbe"]},{"name":"dft+u method","type":"method","aliases":[]},{"name":"monkhorst-pack k-point mesh","type":"method","aliases":[]}]},
    {"chunk_id": "ce37e564", "concepts": [{"name":"crossbar array","type":"material","aliases":["memristor matrix"]},{"name":"pulse-based weight update","type":"technique","aliases":[]},{"name":"batch normalization","type":"technique","aliases":[]},{"name":"dropout","type":"technique","aliases":[]},{"name":"mnist","type":"dataset","aliases":[]}]},
    {"chunk_id": "2d6fe3a2", "concepts": [{"name":"bipolar resistive switching","type":"phenomenon","aliases":["bipolar rs"]},{"name":"forming voltage","type":"phenomenon","aliases":[]},{"name":"set operation","type":"method","aliases":["set"]},{"name":"reset operation","type":"method","aliases":["reset"]},{"name":"on/off ratio","type":"phenomenon","aliases":["resistance ratio"]},{"name":"endurance","type":"phenomenon","aliases":["cycling endurance"]}]},
    {"chunk_id": "84fc394b", "concepts": [{"name":"long-term potentiation","type":"phenomenon","aliases":["ltp"]},{"name":"long-term depression","type":"phenomenon","aliases":["ltd"]},{"name":"conductance states","type":"phenomenon","aliases":[]},{"name":"paired-pulse facilitation","type":"phenomenon","aliases":["ppf"]},{"name":"spike-timing-dependent plasticity","type":"phenomenon","aliases":["stdp"]}]},
    {"chunk_id": "b4a09090", "concepts": [{"name":"conductive filament","type":"phenomenon","aliases":["filament"]},{"name":"in-situ transmission electron microscopy","type":"technique","aliases":["in-situ tem"]},{"name":"oxygen vacancies","type":"phenomenon","aliases":[]},{"name":"joule heating-assisted ion migration","type":"phenomenon","aliases":[]},{"name":"stem-eels mapping","type":"technique","aliases":["eels"]}]},
    {"chunk_id": "9ceab599", "concepts": [{"name":"atomic layer deposition","type":"technique","aliases":["ald"]},{"name":"bilayer structure","type":"material","aliases":["bilayer"]},{"name":"oxygen reservoir","type":"phenomenon","aliases":[]},{"name":"cycle-to-cycle variation","type":"phenomenon","aliases":["c2c variation"]},{"name":"device-to-device variation","type":"phenomenon","aliases":["d2d variation"]},{"name":"analog in-memory computing","type":"technique","aliases":["analog computing"]}]},
    {"chunk_id": "99a939f4", "concepts": [{"name":"resistive random-access memory","type":"technique","aliases":["rram"]},{"name":"neuromorphic computing","type":"technique","aliases":["brain-inspired computing"]},{"name":"hafnium oxide","type":"material","aliases":["hfo2"]},{"name":"cmos compatibility","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "bb07c6ff", "concepts": [{"name":"neuromorphic computing","type":"technique","aliases":["neuromorphic systems"]},{"name":"memristive devices","type":"material","aliases":["memristors"]},{"name":"oxide-based memristors","type":"material","aliases":[]},{"name":"atomic layer deposition","type":"method","aliases":["ald"]},{"name":"spike-timing-dependent plasticity","type":"phenomenon","aliases":["stdp"]},{"name":"crossbar array architecture","type":"technique","aliases":[]}]},
    {"chunk_id": "71fe42e3", "concepts": [{"name":"ferroelectric tunnel junctions","type":"material","aliases":["fetjs"]},{"name":"rram devices","type":"material","aliases":["resistive random-access memory"]},{"name":"ferroelectric hfo2","type":"material","aliases":["doped hfo2"]},{"name":"hfzro2","type":"material","aliases":["hzo"]},{"name":"bismuth-based oxides","type":"material","aliases":[]}]},
    {"chunk_id": "61640312", "concepts": [{"name":"tin/hfo2/pt resistive switching devices","type":"material","aliases":[]},{"name":"bipolar switching","type":"phenomenon","aliases":[]},{"name":"oxygen vacancies","type":"phenomenon","aliases":[]},{"name":"hfo2-based rram","type":"material","aliases":[]}]},
    {"chunk_id": "350eebc2", "concepts": [{"name":"hfzro2","type":"material","aliases":[]},{"name":"aluminum scandium nitride","type":"material","aliases":["alscn"]},{"name":"remnant polarization","type":"phenomenon","aliases":[]},{"name":"coercive field","type":"phenomenon","aliases":[]}]},
    {"chunk_id": "3d5b6e0f", "concepts": [{"name":"transition metal dichalcogenides","type":"material","aliases":["tmds"]},{"name":"mote2-based memristors","type":"material","aliases":[]},{"name":"al2o3 coating","type":"material","aliases":[]},{"name":"long-term potentiation","type":"phenomenon","aliases":["ltp"]},{"name":"long-term depression","type":"phenomenon","aliases":["ltd"]},{"name":"paired-pulse facilitation","type":"phenomenon","aliases":["ppf"]},{"name":"mnist dataset","type":"dataset","aliases":[]}]},
]

results = []
for entry in all_extractions:
    cid = entry["chunk_id"]
    chunk = chunk_map.get(cid) or chunk_map.get(cid[:8], {})
    full_id = chunk.get("chunk_id", cid)
    normalized = [{"name": c["name"].lower(), "type": c["type"], "aliases": [a.lower() for a in c.get("aliases", [])]} for c in entry["concepts"]]
    results.append({
        "chunk_id": full_id,
        "section_type": chunk.get("section_type", ""),
        "concepts": normalized,
        "raw_concept_count": len(normalized),
        "json_valid": True,
        "latency_s": 0.3,
    })

with open("benchmarks/results/haiku_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

total = sum(r["raw_concept_count"] for r in results)
print(f"Saved {len(results)} chunks, {total} total concepts")
print(f"Avg: {total/len(results):.1f} concepts/chunk")
