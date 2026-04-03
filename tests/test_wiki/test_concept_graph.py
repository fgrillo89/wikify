"""Tests for wikify.wiki.concept_graph."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import networkx as nx

import wikify.wiki.concept_graph as mod
from wikify.store.models import ConceptRecord, ConceptRelation, SourceCoverage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_concept(id: str, name: str, concept_type: str = "", domain: str = "ald") -> ConceptRecord:
    return ConceptRecord(
        id=id,
        name=name,
        concept_type=concept_type,
        domain=domain,
        article_status="none",
    )


def _make_coverage(source_id: str, article_slug: str, domain: str = "ald") -> SourceCoverage:
    return SourceCoverage(source_id=source_id, article_slug=article_slug, domain=domain)


def _make_session_mock(exec_results: list) -> MagicMock:
    """Return a context-manager-compatible session mock that cycles through exec_results."""
    session = MagicMock()
    session.__enter__ = lambda s: session
    session.__exit__ = MagicMock(return_value=False)

    call_iter = iter(exec_results)

    def exec_side(stmt):
        result = MagicMock()
        try:
            rows = next(call_iter)
        except StopIteration:
            rows = []
        result.all.return_value = rows
        return result

    session.exec.side_effect = exec_side
    return session


def _make_test_graph(
    nodes: list[tuple[str, dict]],
    edges: list[tuple[str, str, float]],
) -> nx.DiGraph:
    """Build a DiGraph from node/edge specs."""
    g = nx.DiGraph()
    for nid, attrs in nodes:
        g.add_node(nid, **attrs)
    for src, tgt, weight in edges:
        g.add_edge(src, tgt, weight=weight)
        g.add_edge(tgt, src, weight=weight)  # bidirectional
    return g


# ---------------------------------------------------------------------------
# build_concept_graph
# ---------------------------------------------------------------------------


class TestBuildConceptGraph:
    def test_build_concept_graph_empty(self):
        """No ConceptRecords -> empty DiGraph."""
        session = _make_session_mock(exec_results=[[]])  # concepts query returns nothing

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            graph = mod.build_concept_graph(domain="ald", epoch=1)

        assert isinstance(graph, nx.DiGraph)
        assert graph.number_of_nodes() == 0
        assert graph.number_of_edges() == 0

    def test_build_concept_graph_with_cooccurrence(self):
        """3 concepts + SourceCoverage co-occurrences -> correct nodes and edges."""
        concepts = [
            _make_concept("ald", "ALD"),
            _make_concept("hfo2", "HfO2"),
            _make_concept("memristor", "Memristor"),
        ]
        coverage_rows = [
            # paper1 covers ald and hfo2
            _make_coverage("paper1", "ald"),
            _make_coverage("paper1", "hfo2"),
            # paper2 covers ald and memristor
            _make_coverage("paper2", "ald"),
            _make_coverage("paper2", "memristor"),
        ]

        # build_concept_graph opens two sessions: first for concepts, second for coverage
        # _make_session_mock iterates exec_results once per .exec() call; each session
        # makes exactly one exec() call, so each list has one entry.
        session1 = _make_session_mock([concepts])
        session2 = _make_session_mock([coverage_rows])

        call_count = 0
        sessions = [session1, session2]

        def get_session_factory():
            nonlocal call_count
            s = sessions[call_count % 2]
            call_count += 1
            return s

        with patch("wikify.wiki.concept_graph.get_session", side_effect=get_session_factory):
            graph = mod.build_concept_graph(domain="ald", epoch=1)

        assert graph.number_of_nodes() == 3
        assert set(graph.nodes()) == {"ald", "hfo2", "memristor"}

        # ald <-> hfo2 edge (paper1), ald <-> memristor edge (paper2)
        assert graph.has_edge("ald", "hfo2")
        assert graph.has_edge("hfo2", "ald")
        assert graph.has_edge("ald", "memristor")
        assert graph.has_edge("memristor", "ald")

        # Each co-occurrence happened once -> weight == 1.0
        assert graph["ald"]["hfo2"]["weight"] == 1.0
        assert graph["ald"]["memristor"]["weight"] == 1.0

    def test_build_concept_graph_no_cross_edges_for_isolated_sources(self):
        """When each source covers only one concept, no edges should appear."""
        concepts = [
            _make_concept("ald", "ALD"),
            _make_concept("hfo2", "HfO2"),
        ]
        coverage_rows = [
            _make_coverage("paper1", "ald"),   # only ald — no pair
            _make_coverage("paper2", "hfo2"),  # only hfo2 — no pair
        ]

        sessions = [
            _make_session_mock([concepts]),
            _make_session_mock([coverage_rows]),
        ]
        call_count = 0

        def get_session_factory():
            nonlocal call_count
            s = sessions[call_count % 2]
            call_count += 1
            return s

        with patch("wikify.wiki.concept_graph.get_session", side_effect=get_session_factory):
            graph = mod.build_concept_graph(domain="ald", epoch=1)

        assert graph.number_of_nodes() == 2
        assert graph.number_of_edges() == 0


# ---------------------------------------------------------------------------
# score_importance
# ---------------------------------------------------------------------------


class TestScoreImportance:
    def test_score_importance_empty_graph(self):
        """Empty graph -> empty dict."""
        graph = nx.DiGraph()
        # score_importance returns early before touching DB for empty graph
        with patch("wikify.wiki.concept_graph.get_session"):
            result = mod.score_importance(graph)
        assert result == {}

    def test_score_importance_normalized(self):
        """All scores must be in [0, 1] and max == 1.0."""
        graph = _make_test_graph(
            nodes=[
                ("ald", {"name": "ALD", "concept_type": "technique"}),
                ("hfo2", {"name": "HfO2", "concept_type": "material"}),
                ("tma", {"name": "TMA", "concept_type": "material"}),
            ],
            edges=[
                ("ald", "hfo2", 3.0),
                ("ald", "tma", 2.0),
            ],
        )

        # Each concept appears in some number of sources (source diversity)
        coverage_map = {
            "ald": [_make_coverage("p1", "ald"), _make_coverage("p2", "ald")],
            "hfo2": [_make_coverage("p1", "hfo2")],
            "tma": [_make_coverage("p2", "tma")],
        }

        node_ids = ["ald", "hfo2", "tma"]
        call_count = 0

        def get_session_factory():
            session = MagicMock()
            session.__enter__ = lambda s: session
            session.__exit__ = MagicMock(return_value=False)

            nonlocal call_count
            current = call_count
            call_count += 1

            def exec_side(stmt):
                result = MagicMock()
                # The session is used in a loop over node_ids
                # We derive which concept from the call order within the session
                idx = session.exec.call_count - 1
                cid = node_ids[idx] if idx < len(node_ids) else "ald"
                result.all.return_value = coverage_map.get(cid, [])
                return result

            session.exec.side_effect = exec_side
            return session

        with patch("wikify.wiki.concept_graph.get_session", side_effect=get_session_factory):
            scores = mod.score_importance(graph)

        assert len(scores) == 3
        for v in scores.values():
            assert 0.0 <= v <= 1.0, f"Score out of range: {v}"
        assert max(scores.values()) == 1.0

    def test_score_importance_single_node_no_edges(self):
        """Single isolated node -> score of 1.0 after normalisation."""
        graph = nx.DiGraph()
        graph.add_node("ald", name="ALD", concept_type="technique")

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)
        exec_result = MagicMock()
        exec_result.all.return_value = [_make_coverage("p1", "ald")]
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            scores = mod.score_importance(graph)

        assert "ald" in scores
        assert scores["ald"] == 1.0


# ---------------------------------------------------------------------------
# classify_node_roles
# ---------------------------------------------------------------------------


class TestClassifyNodeRoles:
    def test_classify_node_roles_empty_graph(self):
        """Empty graph -> empty dict."""
        graph = nx.DiGraph()
        result = mod.classify_node_roles(graph, {})
        assert result == {}

    def test_classify_node_roles_basic(self):
        """High-importance + high-degree node gets 'core'; low-degree/importance gets 'peripheral'."""
        # hub connects to all others; spokes are peripheral
        graph = _make_test_graph(
            nodes=[
                ("hub", {"name": "Hub", "concept_type": "technique"}),
                ("a", {"name": "A", "concept_type": "material"}),
                ("b", {"name": "B", "concept_type": "material"}),
                ("c", {"name": "C", "concept_type": "material"}),
            ],
            edges=[
                ("hub", "a", 1.0),
                ("hub", "b", 1.0),
                ("hub", "c", 1.0),
            ],
        )
        # hub has degree 3 (highest); others have degree 1
        scores = {
            "hub": 0.9,   # high importance
            "a": 0.1,
            "b": 0.1,
            "c": 0.1,
        }

        roles = mod.classify_node_roles(graph, scores)

        assert roles["hub"] == "core"
        # spokes should be peripheral (low importance, low degree)
        for node in ("a", "b", "c"):
            assert roles[node] in ("peripheral", "bridge")

    def test_classify_node_roles_all_results_valid(self):
        """Every node must receive a valid role label."""
        graph = _make_test_graph(
            nodes=[
                ("x", {"name": "X", "concept_type": "method"}),
                ("y", {"name": "Y", "concept_type": "phenomenon"}),
                ("z", {"name": "Z", "concept_type": "theory"}),
            ],
            edges=[
                ("x", "y", 1.0),
                ("y", "z", 1.0),
            ],
        )
        scores = {"x": 0.4, "y": 0.6, "z": 0.2}
        valid_roles = {"core", "bridge", "peripheral"}

        roles = mod.classify_node_roles(graph, scores)

        assert set(roles.keys()) == {"x", "y", "z"}
        for role in roles.values():
            assert role in valid_roles

    def test_classify_node_roles_no_edges(self):
        """Nodes with no edges have 0 degree and 0 betweenness -> all peripheral."""
        graph = nx.DiGraph()
        graph.add_node("a")
        graph.add_node("b")
        graph.add_node("c")
        scores = {"a": 0.3, "b": 0.3, "c": 0.3}

        roles = mod.classify_node_roles(graph, scores)

        assert set(roles.keys()) == {"a", "b", "c"}
        for role in roles.values():
            assert role in ("peripheral", "bridge", "core")


# ---------------------------------------------------------------------------
# detect_communities
# ---------------------------------------------------------------------------


class TestDetectCommunities:
    def test_detect_communities_empty_graph(self):
        """Empty graph -> empty dict."""
        graph = nx.DiGraph()
        result = mod.detect_communities(graph)
        assert result == {}

    def test_detect_communities_isolated_nodes(self):
        """3 isolated nodes (no edges) -> all assigned to community 0."""
        graph = nx.DiGraph()
        graph.add_node("a")
        graph.add_node("b")
        graph.add_node("c")

        result = mod.detect_communities(graph)

        assert set(result.keys()) == {"a", "b", "c"}
        assert all(v == 0 for v in result.values())

    def test_detect_communities_two_clusters(self):
        """Two dense clusters with no cross-edges -> 2 distinct communities."""
        # Cluster 1: a-b-c fully connected; Cluster 2: d-e-f fully connected
        graph = _make_test_graph(
            nodes=[
                ("a", {}), ("b", {}), ("c", {}),
                ("d", {}), ("e", {}), ("f", {}),
            ],
            edges=[
                ("a", "b", 1.0), ("a", "c", 1.0), ("b", "c", 1.0),
                ("d", "e", 1.0), ("d", "f", 1.0), ("e", "f", 1.0),
            ],
        )

        result = mod.detect_communities(graph)

        assert set(result.keys()) == {"a", "b", "c", "d", "e", "f"}

        # All nodes should have a community index
        community_ids = set(result.values())
        assert len(community_ids) == 2

        # Nodes within each cluster must share the same community
        cluster1 = {result["a"], result["b"], result["c"]}
        cluster2 = {result["d"], result["e"], result["f"]}
        assert len(cluster1) == 1, "Cluster 1 nodes should all share one community"
        assert len(cluster2) == 1, "Cluster 2 nodes should all share one community"
        assert cluster1 != cluster2

    def test_detect_communities_single_node(self):
        """Single-node graph -> that node gets community 0."""
        graph = nx.DiGraph()
        graph.add_node("solo")

        result = mod.detect_communities(graph)

        assert result == {"solo": 0}


# ---------------------------------------------------------------------------
# extract_relations
# ---------------------------------------------------------------------------


class TestExtractRelations:
    def test_extract_relations_infers_type(self):
        """method->material gets USED-IN, technique->dataset gets USED-IN, theory->phenomenon gets ENABLES."""
        graph = _make_test_graph(
            nodes=[
                ("cvd", {"name": "CVD", "concept_type": "method"}),
                ("hfo2", {"name": "HfO2", "concept_type": "material"}),
                ("xps", {"name": "XPS", "concept_type": "technique"}),
                ("dataset_a", {"name": "Dataset A", "concept_type": "dataset"}),
                ("band_theory", {"name": "Band Theory", "concept_type": "theory"}),
                ("leakage", {"name": "Leakage", "concept_type": "phenomenon"}),
            ],
            edges=[
                ("cvd", "hfo2", 1.0),           # method -> material = USED-IN
                ("xps", "dataset_a", 1.0),       # technique -> dataset = USED-IN
                ("band_theory", "leakage", 1.0), # theory -> phenomenon = ENABLES
            ],
        )

        relations = mod.extract_relations(graph, epoch=1)

        # Build a lookup: (source, target) -> relation_type
        rel_map = {(r.source_concept, r.target_concept): r.relation_type for r in relations}

        assert rel_map[("cvd", "hfo2")] == "USED-IN"
        assert rel_map[("xps", "dataset_a")] == "USED-IN"
        assert rel_map[("band_theory", "leakage")] == "ENABLES"

    def test_extract_relations_same_type_related_to(self):
        """Two nodes of the same concept_type -> RELATED-TO."""
        graph = _make_test_graph(
            nodes=[
                ("ald", {"name": "ALD", "concept_type": "technique"}),
                ("cvd", {"name": "CVD", "concept_type": "technique"}),
            ],
            edges=[("ald", "cvd", 2.0)],
        )

        relations = mod.extract_relations(graph, epoch=1)
        rel_map = {(r.source_concept, r.target_concept): r.relation_type for r in relations}

        assert rel_map[("ald", "cvd")] == "RELATED-TO"
        assert rel_map[("cvd", "ald")] == "RELATED-TO"

    def test_extract_relations_stores_epoch_and_weight(self):
        """ConceptRelation rows must carry the correct epoch and edge weight."""
        graph = _make_test_graph(
            nodes=[
                ("a", {"name": "A", "concept_type": "method"}),
                ("b", {"name": "B", "concept_type": "material"}),
            ],
            edges=[("a", "b", 5.0)],
        )

        relations = mod.extract_relations(graph, epoch=3)

        for rel in relations:
            assert rel.epoch == 3
            assert rel.weight == 5.0

    def test_extract_relations_unknown_types_related_to(self):
        """Nodes with empty concept_type strings fall back to RELATED-TO."""
        graph = _make_test_graph(
            nodes=[
                ("x", {"name": "X", "concept_type": ""}),
                ("y", {"name": "Y", "concept_type": ""}),
            ],
            edges=[("x", "y", 1.0)],
        )

        relations = mod.extract_relations(graph, epoch=1)
        for rel in relations:
            assert rel.relation_type == "RELATED-TO"

    def test_extract_relations_returns_concept_relation_instances(self):
        """All returned objects must be ConceptRelation instances."""
        graph = _make_test_graph(
            nodes=[
                ("ald", {"name": "ALD", "concept_type": "technique"}),
                ("hfo2", {"name": "HfO2", "concept_type": "material"}),
            ],
            edges=[("ald", "hfo2", 1.0)],
        )

        relations = mod.extract_relations(graph, epoch=2)

        assert len(relations) > 0
        for rel in relations:
            assert isinstance(rel, ConceptRelation)


# ---------------------------------------------------------------------------
# update_concept_importance
# ---------------------------------------------------------------------------


class TestUpdateConceptImportance:
    def test_update_concept_importance_writes_db(self):
        """Importance score should be written and session.commit() should be called."""
        record = _make_concept("ald", "ALD")
        assert record.importance == 0.0

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        exec_result = MagicMock()
        exec_result.all.return_value = [record]
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            mod.update_concept_importance({"ald": 0.85})

        assert record.importance == 0.85
        session.add.assert_called_once_with(record)
        session.commit.assert_called_once()

    def test_update_concept_importance_skips_missing(self):
        """A concept_id not found in DB should not cause an error or spurious add."""
        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        exec_result = MagicMock()
        exec_result.all.return_value = []  # concept not in DB
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            mod.update_concept_importance({"unknown_concept": 0.5})

        session.add.assert_not_called()
        session.commit.assert_called_once()

    def test_update_concept_importance_no_op_on_empty_scores(self):
        """Empty scores dict -> no DB access at all."""
        with patch("wikify.wiki.concept_graph.get_session") as mock_gs:
            mod.update_concept_importance({})
        mock_gs.assert_not_called()

    def test_update_concept_importance_multiple_concepts(self):
        """All provided concept_ids should be updated in a single transaction."""
        rec_a = _make_concept("ald", "ALD")
        rec_b = _make_concept("hfo2", "HfO2")

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        lookup = {"ald": rec_a, "hfo2": rec_b}

        def exec_side(stmt):
            result = MagicMock()
            # Determine which concept is being queried by checking add call count
            cid = list(lookup.keys())[session.exec.call_count - 1]
            result.all.return_value = [lookup[cid]]
            return result

        session.exec.side_effect = exec_side

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            mod.update_concept_importance({"ald": 0.9, "hfo2": 0.7})

        assert rec_a.importance == 0.9
        assert rec_b.importance == 0.7
        session.commit.assert_called_once()


# ---------------------------------------------------------------------------
# save_relations
# ---------------------------------------------------------------------------


class TestSaveRelations:
    def _make_relation(
        self, source: str, target: str, epoch: int = 1, rel_type: str = "RELATED-TO"
    ) -> ConceptRelation:
        return ConceptRelation(
            source_concept=source,
            target_concept=target,
            relation_type=rel_type,
            weight=1.0,
            epoch=epoch,
        )

    def test_save_relations_replaces_epoch(self):
        """Existing rows for an epoch must be deleted; new rows must be inserted."""
        old_rel = self._make_relation("ald", "hfo2", epoch=1)
        new_rel = self._make_relation("ald", "memristor", epoch=1)

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        exec_result = MagicMock()
        exec_result.all.return_value = [old_rel]
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            count = mod.save_relations([new_rel], epoch=1)

        session.delete.assert_called_once_with(old_rel)
        session.flush.assert_called_once()
        session.add.assert_called_once_with(new_rel)
        session.commit.assert_called_once()
        assert count == 1

    def test_save_relations_no_op_on_empty_list(self):
        """Empty relations list -> no DB access; returns 0."""
        with patch("wikify.wiki.concept_graph.get_session") as mock_gs:
            count = mod.save_relations([], epoch=1)

        mock_gs.assert_not_called()
        assert count == 0

    def test_save_relations_returns_inserted_count(self):
        """Return value equals the number of new relations inserted."""
        new_rels = [
            self._make_relation("ald", "hfo2", epoch=2),
            self._make_relation("ald", "memristor", epoch=2),
            self._make_relation("hfo2", "memristor", epoch=2),
        ]

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        exec_result = MagicMock()
        exec_result.all.return_value = []  # no pre-existing rows
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            count = mod.save_relations(new_rels, epoch=2)

        assert count == 3
        assert session.add.call_count == 3

    def test_save_relations_deletes_all_old_rows(self):
        """All pre-existing epoch rows must be deleted, not just the first."""
        old_rels = [
            self._make_relation("ald", "hfo2", epoch=1),
            self._make_relation("ald", "tma", epoch=1),
            self._make_relation("hfo2", "tma", epoch=1),
        ]
        new_rel = self._make_relation("ald", "memristor", epoch=1)

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)

        exec_result = MagicMock()
        exec_result.all.return_value = old_rels
        session.exec.return_value = exec_result

        with patch("wikify.wiki.concept_graph.get_session", return_value=session):
            mod.save_relations([new_rel], epoch=1)

        assert session.delete.call_count == 3
        for old in old_rels:
            session.delete.assert_any_call(old)
