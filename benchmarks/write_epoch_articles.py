"""Write the 5 real wiki articles from sonnet agent output, then run Passes 4-5."""

import json
from pathlib import Path

from sqlmodel import select

from wikify.store.db import get_session
from wikify.store.models import ConceptRecord
from wikify.wiki.builder import article_path, generate_wiki_index, write_article
from wikify.wiki.linker import cross_link_articles

wiki_dir = Path("data/wiki")
wiki_dir.mkdir(parents=True, exist_ok=True)
(wiki_dir / "concepts").mkdir(parents=True, exist_ok=True)

# Articles written by sonnet agents from real corpus evidence
ARTICLES = {
    "atomic_layer_deposition": (
        "## Definition\n\n"
        "Atomic layer deposition (ALD) is a thin-film deposition technique based on sequential, "
        "self-limiting chemical reactions that provide atomic-level control over film thickness. "
        "ALD is considered the most technologically relevant deposition method for oxide films "
        "used in advanced semiconductor devices [REF:Yang 2011].\n\n"
        "## Mechanism / Process\n\n"
        "ALD oxide films have been extensively studied as gate oxides in [[mosfet]] structures "
        "and as dielectric media in DRAM stack capacitors [REF:Yang 2011]. ALD achieves "
        "unprecedented control of [[oxygen_vacancy_concentration_control]] by adjusting precursor "
        "pulse parameters [REF:Yang 2011]. The resistivity and O/Ti ratio of ALD [[titanium_oxide]] "
        "films depend on both growth temperature and water pulse duration [REF:Yang 2011]. "
        "For [[rram]] applications, oxide composition must be tuned within the oxygen-deficient "
        "regime [REF:Yang 2011].\n\n"
        "Thermal ALD uses water vapor or ozone as the oxygen precursor. "
        "[[plasma_assisted_ald]] enables deposition of multilayer films with distinct "
        "compositional layers, such as NbOx films with three separate compositions [REF:NRL]. "
        "Conformal ALD deposits uniform films over complex 3D topographies [REF:Ghoneim].\n\n"
        "## Key Facts\n\n"
        "- ALD of 50 nm Al2O3 at 300 C is used for spacer formation in foldable "
        "[[neuromorphic_computing]] devices [REF:Ghoneim]\n"
        "- ALD of [[high_k_gate_dielectric]] layers is integral to TSMC 28 nm HKMG process "
        "for realizing memristors [REF:Yu]\n"
        "- Plasma-assisted ALD deposits multilayer NbOx with three distinct compositions [REF:NRL]\n"
        "- H2O pulse duration controls O/Ti ratio and film resistivity [REF:Yang 2011]\n\n"
        "## In This Corpus\n\n"
        "Yang et al. systematically characterize ALD TiO2 films across temperatures and pulse "
        "conditions. Ghoneim et al. use conformal ALD for foldable neuromorphic devices. "
        "The NRL group uses plasma-assisted ALD for NbOx multilayer stacks. Yu et al. leverage "
        "ALD high-k layers in 28 nm CMOS for embedded synaptic devices.\n\n"
        "## Relationships\n\n"
        "| Related Concept | Relation | Notes |\n"
        "|---|---|---|\n"
        "| [[titanium_oxide]] | USED-IN | Primary switching material deposited by ALD |\n"
        "| [[rram]] | ENABLES | ALD provides the switching layer |\n"
        "| [[high_k_gate_dielectric]] | USED-IN | Gate dielectric in CMOS memristors |\n"
        "| [[oxygen_vacancy_concentration_control]] | ENABLES | Tuned via pulse parameters |\n\n"
        "## Open Questions\n\n"
        "- Can ALD pulse engineering eliminate the forming step by pre-defining vacancy profiles?\n"
        "- What is the minimum ALD temperature for memristor-quality films?\n"
        "- How does plasma vs thermal ALD affect switching uniformity at scale?\n"
    ),
    "rram": (
        "## Definition\n\n"
        "RRAM (resistive random-access memory) is a class of non-volatile memory that stores "
        "information through reversible resistive switching in thin oxide films.\n\n"
        "## Mechanism / Process\n\n"
        "RRAM operation involves two principal transitions [REF:Yu]. The [[forming_operation]] "
        "conditions the device before use. The Set operation transitions the device from high "
        "memristance (HMR) to low memristance (LMR). The Reset operation transitions from LMR "
        "back to HMR. The [[on_off_ratio]] between states defines the memory window [REF:Yu].\n\n"
        "## Key Facts\n\n"
        "- RRAM is a promising candidate for neural networks, [[computing_in_memory]], and "
        "[[neuromorphic_computing]] [REF:Yu]\n"
        "- Crossbar arrays with 10 nm x 10 nm cell size have been demonstrated [REF:Ghoneim]\n"
        "- Device stack: Si/SiO2/Ti/Pt/[[titanium_oxide]]/Pt deposited by [[atomic_layer_deposition]] "
        "[REF:Yang 2011]\n"
        "- [[volatile_memristor]] devices mimic spike action of biological neurons [REF:NRL]\n"
        "- Foldable devices show endurance of over 600 DC switching cycles [REF:Ghoneim]\n\n"
        "## In This Corpus\n\n"
        "Yu et al. demonstrate embedded synaptic devices (eASD) in 28 nm CMOS. Yang et al. "
        "characterize TiO2-based RRAM with ALD-controlled switching layers. Ghoneim et al. "
        "demonstrate foldable RRAM compatible with CMOS. NRL investigates volatile NbO2 switching.\n\n"
        "## Relationships\n\n"
        "| Related Concept | Relation | Notes |\n"
        "|---|---|---|\n"
        "| [[atomic_layer_deposition]] | USED-IN | Switching layer deposition |\n"
        "| [[neuromorphic_computing]] | ENABLES | Core building block |\n"
        "| [[titanium_oxide]] | USED-IN | Active switching material |\n"
        "| [[forming_operation]] | PART-OF | Required initialization |\n"
        "| [[on_off_ratio]] | PART-OF | Key performance metric |\n\n"
        "## Open Questions\n\n"
        "- What is the fundamental endurance limit for ALD-deposited oxide RRAM?\n"
        "- Can forming-free RRAM be achieved through vacancy pre-engineering?\n"
    ),
    "neuromorphic_computing": (
        "## Definition\n\n"
        "Neuromorphic computing is a computing paradigm that implements information processing "
        "architectures inspired by the structure and dynamics of biological neural systems.\n\n"
        "## Mechanism / Process\n\n"
        "[[rram]] and other memristive devices are core building blocks for neuromorphic hardware "
        "because their switching behavior mimics biological synapses and neurons [REF:NRL]. "
        "A neuromorphic computer requires a folded form factor to match brain cortex architecture "
        "[REF:Ghoneim]. [[reservoir_computing]] exploits transient dynamics of physical recurrent "
        "networks using [[volatile_memristor]] devices [REF:NRL].\n\n"
        "## Key Facts\n\n"
        "- First memristive devices matching motor neuron footprint demonstrated [REF:Ghoneim]\n"
        "- Foldable devices maintain functionality at 5 mm bending radius [REF:Ghoneim]\n"
        "- [[embedded_artificial_synaptic_device]] (eASD) is compatible with pure CMOS HKMG "
        "logic [REF:Yu]\n"
        "- Physical neural networks use TiO2 or [[nbo2]] for reservoir computing [REF:NRL]\n"
        "- eASD arrays use diode rectification to suppress sneak-path leakage [REF:Yu]\n\n"
        "## In This Corpus\n\n"
        "Ghoneim et al. pioneer foldable neuromorphic memristive electronics with CMOS compatibility. "
        "Yu et al. demonstrate eASD technology in 28 nm pure CMOS process. "
        "NRL investigates volatile NbO2 threshold switches for reservoir computing.\n\n"
        "## Relationships\n\n"
        "| Related Concept | Relation | Notes |\n"
        "|---|---|---|\n"
        "| [[rram]] | USED-IN | Core device technology |\n"
        "| [[embedded_artificial_synaptic_device]] | PART-OF | Synaptic building block |\n"
        "| [[reservoir_computing]] | IS-A | Subclass of neuromorphic |\n"
        "| [[volatile_memristor]] | USED-IN | For spiking behavior |\n\n"
        "## Open Questions\n\n"
        "- What is the minimum device count for useful neuromorphic computation?\n"
        "- Can foldable architectures achieve 3D stacking density comparable to brain cortex?\n"
    ),
    "nbo2": (
        "## Definition\n\n"
        "NbO2 is a crystalline niobium dioxide that exhibits a reversible insulator-to-metal "
        "transition (IMT). It is used as a threshold switching material in volatile [[rram]] devices.\n\n"
        "## Mechanism / Process\n\n"
        "An applied current induces localized [[joule_heating]] within the NbO2 film [REF:NRL]. "
        "This heating drives a reversible [[insulator_to_metal_transition]] that produces threshold "
        "switching behavior [REF:NRL]. The transition is volatile: the device returns to its "
        "insulating state when current is removed.\n\n"
        "## Key Facts\n\n"
        "- R-NbO2 is identified by O-K and Nb-N spectral signatures in EELS [REF:NRL]\n"
        "- [[plasma_assisted_ald]] deposits multilayer NbOx with three compositionally distinct "
        "layers [REF:NRL]\n"
        "- A 30 nm crystalline R-NbO2 layer forms as the bottom layer in ALD NbOx stacks [REF:NRL]\n"
        "- Above it, 30 nm amorphous [[nb2o5]] with corner-sharing NbO6 octahedra [REF:NRL]\n"
        "- An intermediate 15 nm layer has complex oxygen coordination matching neither phase [REF:NRL]\n\n"
        "## In This Corpus\n\n"
        "The NRL study characterizes plasma-assisted ALD NbOx multilayer films using EELS. "
        "NbO2 is evaluated alongside [[titanium_oxide]] as a candidate for [[reservoir_computing]].\n\n"
        "## Relationships\n\n"
        "| Related Concept | Relation | Notes |\n"
        "|---|---|---|\n"
        "| [[insulator_to_metal_transition]] | ENABLES | Core switching mechanism |\n"
        "| [[joule_heating]] | ENABLES | Thermal trigger for IMT |\n"
        "| [[rram]] | USED-IN | Volatile switching element |\n"
        "| [[plasma_assisted_ald]] | USED-IN | Deposition technique |\n"
        "| [[reservoir_computing]] | USED-IN | Target application |\n\n"
        "## Open Questions\n\n"
        "- What is the exact oxygen coordination in the intermediate 15 nm NbOx layer?\n"
        "- Can plasma-assisted ALD selectively grow pure R-NbO2 without Nb2O5 overlayer?\n"
        "- What are the cycle-to-cycle variability characteristics of ALD NbO2 threshold switches?\n"
    ),
    "titanium_oxide": (
        "## Definition\n\n"
        "Titanium oxide (TiO2) is a transition metal oxide used as the active switching layer "
        "in [[rram]] devices. Its resistive switching arises from controlled redistribution of "
        "oxygen vacancies under applied electric fields.\n\n"
        "## Mechanism / Process\n\n"
        "[[atomic_layer_deposition]] grows TiO2 with precise stoichiometry control by adjusting "
        "substrate temperature and precursor pulse duration [REF:Yang 2011]. Films at 100 C "
        "approach stoichiometric O/Ti ratio of 2:1 [REF:Yang 2011]. Films at 250 C are "
        "oxygen-deficient [REF:Yang 2011]. A [[forming_operation]] establishes the initial "
        "conductive filament before reversible switching.\n\n"
        "## Key Facts\n\n"
        "- ALD achieves unprecedented oxygen vacancy control via pulse parameters [REF:Yang 2011]\n"
        "- Device stack: Si/SiO2 100 nm/Ti 2 nm/Pt 9 nm/TiO2 15 nm/Pt 11 nm [REF:Yang 2011]\n"
        "- Films at 150 C balance low impurity content with suitable resistivity [REF:Yang 2011]\n"
        "- Carbon and nitrogen impurities act as acceptors in TiO2 [REF:Yang 2011]\n"
        "- TiO2 meets nonlinearity requirements for [[reservoir_computing]] [REF:NRL]\n\n"
        "## In This Corpus\n\n"
        "Yang et al. systematically characterize ALD TiO2 across temperatures and pulse conditions. "
        "NRL identifies TiO2 alongside [[nbo2]] as a candidate for reservoir computing.\n\n"
        "## Relationships\n\n"
        "| Related Concept | Relation | Notes |\n"
        "|---|---|---|\n"
        "| [[atomic_layer_deposition]] | USED-IN | Deposition method |\n"
        "| [[oxygen_vacancy_concentration_control]] | ENABLES | Key defect lever |\n"
        "| [[rram]] | USED-IN | Active switching layer |\n"
        "| [[forming_operation]] | PART-OF | Required initialization |\n"
        "| [[nbo2]] | RELATED-TO | Co-candidate for reservoir computing |\n\n"
        "## Open Questions\n\n"
        "- What is the exact relationship between H2O pulse duration and vacancy profile depth?\n"
        "- How do C/N acceptors quantitatively affect switching window and endurance?\n"
        "- Can ALD pulse engineering eliminate the forming operation?\n"
    ),
}

# Write each article
written = 0
for concept_id, body in ARTICLES.items():
    fpath = article_path(wiki_dir, "concepts", concept_id)

    with get_session() as session:
        concept = session.get(ConceptRecord, concept_id)
        if concept is None:
            print(f"  SKIP {concept_id} (not in DB)")
            continue

    write_article(
        path=fpath,
        title=concept.name,
        content=body,
        sources=[],
        topics=[concept.concept_type] if concept.concept_type else [],
        status="full",
        model="claude-sonnet-4-20250514",
    )

    with get_session() as session:
        db_concept = session.get(ConceptRecord, concept_id)
        if db_concept:
            db_concept.article_status = "full"
            db_concept.article_path = str(fpath)
            session.add(db_concept)
            session.commit()

    written += 1
    print(f"  WROTE {fpath}")

print(f"\n{written} articles written")

# Pass 4
print("\nPass 4: Cross-linking...")
cross_refs = cross_link_articles(wiki_dir, sitemap=None)
print(f"  {cross_refs} articles cross-linked")

# Pass 5
print("\nPass 5: Rebuilding index...")
generate_wiki_index(wiki_dir)
print(f"  Index written to {wiki_dir / '_index.md'}")

# Summary
article_files = list((wiki_dir / "concepts").glob("*.md"))
total_bytes = sum(f.stat().st_size for f in article_files)
print(f"\n{'='*60}")
print(f"  EPOCH 1 COMPLETE")
print(f"  Articles: {len(article_files)} files")
print(f"  Total size: {total_bytes:,} bytes ({total_bytes/1024:.1f} KB)")
for f in sorted(article_files):
    print(f"    {f.name} ({f.stat().st_size:,} bytes)")
print(f"{'='*60}")
