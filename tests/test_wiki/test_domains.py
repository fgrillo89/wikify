"""Tests for wikify.wiki.graph.domains."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import networkx as nx
import pytest

import wikify.wiki.graph.domains as mod
from wikify.store.models import ConceptRecord, DomainCluster, TopologySnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_concept(
    cid: str,
    name: str,
    definition: str = "A research concept.",
    concept_type: str = "technique",
    domain: str = "",
) -> ConceptRecord:
    return ConceptRecord(
        id=cid,
        name=name,
        definition=definition,
        concept_type=concept_type,
        domain=domain,
        article_status="none",
    )


def _make_cluster(
    cid: str,
    label: str,
    scope: str = "A research domain.",
    core_concept_ids: list[str] | None = None,
    bridge_concept_ids: list[str] | None = None,
    centroid_embedding: list[float] | None = None,
) -> DomainCluster:
    return DomainCluster(
        id=cid,
        label=label,
        scope=scope,
        epoch_created=1,
        epoch_last_updated=1,
        concept_count=3,
        core_concept_ids=json.dumps(core_concept_ids or []),
        bridge_concept_ids=json.dumps(bridge_concept_ids or []),
        centroid_embedding=json.dumps(centroid_embedding or []),
        modularity_contribution=0.1,
        persona_text="",
        merged_from=json.dumps([]),
    )


def _make_session_get(lookup: dict[str, ConceptRecord]) -> MagicMock:
    """Return a context-manager session where session.get(ConceptRecord, cid) uses lookup."""
    session = MagicMock()
    session.__enter__ = lambda s: session
    session.__exit__ = MagicMock(return_value=False)
    session.get.side_effect = lambda model_cls, key: lookup.get(key)
    return session


def _two_cluster_graph() -> nx.DiGraph:
    """Graph with 2 dense clusters (a,b,c) and (d,e,f), no cross-edges."""
    g = nx.DiGraph()
    for u, v in [("a", "b"), ("a", "c"), ("b", "c")]:
        g.add_edge(u, v, weight=1.0)
        g.add_edge(v, u, weight=1.0)
    for u, v in [("d", "e"), ("d", "f"), ("e", "f")]:
        g.add_edge(u, v, weight=1.0)
        g.add_edge(v, u, weight=1.0)
    return g


# ---------------------------------------------------------------------------
# validate_community
# ---------------------------------------------------------------------------


class TestValidateCommunity:
    def test_coherent(self):
        """LLM returns coherent=True -> returned dict has coherent=True and the label."""
        llm_response = {
            "coherent": True,
            "label": "ALD Process",
            "scope": "Processes used in atomic layer deposition.",
            "core_concepts": ["ALD", "CVD"],
            "split_proposal": None,
        }
        with patch("wikify.wiki.graph.domains.complete_json", return_value=llm_response):
            result = mod.validate_community(
                ["ALD", "CVD", "HfO2"],
                ["def1", "def2", "def3"],
                ["Paper 1"],
                model="test-model",
            )

        assert result["coherent"] is True
        assert result["label"] == "ALD Process"
        assert result["split_proposal"] is None
        assert "ALD" in result["core_concepts"]

    def test_incoherent_with_split_proposal(self):
        """LLM returns coherent=False with a split_proposal."""
        llm_response = {
            "coherent": False,
            "label": "Mixed",
            "scope": "Mixed bag of concepts.",
            "core_concepts": [],
            "split_proposal": [
                {"label": "Materials", "concepts": ["HfO2"]},
                {"label": "Methods", "concepts": ["ALD"]},
            ],
        }
        with patch("wikify.wiki.graph.domains.complete_json", return_value=llm_response):
            result = mod.validate_community(
                ["ALD", "HfO2"],
                ["def1", "def2"],
                ["Paper 1"],
                model="test-model",
            )

        assert result["coherent"] is False
        assert result["split_proposal"] is not None
        assert len(result["split_proposal"]) == 2

    def test_llm_failure_returns_fallback(self):
        """When complete_json raises, validate_community returns a safe fallback."""
        with patch(
            "wikify.wiki.graph.domains.complete_json",
            side_effect=RuntimeError("LLM timeout"),
        ):
            result = mod.validate_community(
                ["ALD", "CVD"],
                ["def1", "def2"],
                ["Paper 1"],
                model="test-model",
            )

        # Fallback must be a dict and must not raise
        assert isinstance(result, dict)
        assert result["coherent"] is True
        assert isinstance(result["label"], str)
        assert len(result["label"]) > 0
        assert result["split_proposal"] is None

    def test_llm_returns_non_dict_falls_back(self):
        """When complete_json returns something that is not a dict, fall back gracefully."""
        with patch("wikify.wiki.graph.domains.complete_json", return_value="not a dict"):
            result = mod.validate_community(
                ["ALD"],
                ["def1"],
                [],
                model="test-model",
            )

        # A non-dict causes a ValueError inside the try block -> fallback
        assert isinstance(result, dict)
        assert result["coherent"] is True

    def test_uses_fallback_label_when_label_missing(self):
        """If LLM response omits 'label', fallback_label is used."""
        llm_response = {
            "coherent": True,
            "label": "",  # empty
            "scope": "Scope here.",
            "core_concepts": [],
            "split_proposal": None,
        }
        with patch("wikify.wiki.graph.domains.complete_json", return_value=llm_response):
            result = mod.validate_community(
                ["ALD", "CVD"],
                ["def1", "def2"],
                [],
                model="test-model",
            )

        # _fallback_label("ALD") -> "Domain: ALD"
        assert "ALD" in result["label"]


# ---------------------------------------------------------------------------
# check_community_merge
# ---------------------------------------------------------------------------


class TestCheckCommunityMerge:
    def test_merge_true(self):
        """LLM returns merge=True -> function returns True."""
        llm_response = {"merge": True, "reason": "Strongly overlapping scope."}
        with patch("wikify.wiki.graph.domains.complete_json", return_value=llm_response):
            result = mod.check_community_merge(
                "ALD Process", "Scope A",
                "CVD Process", "Scope B",
                ["bridge_concept"],
                model="test-model",
            )
        assert result is True

    def test_merge_false(self):
        """LLM returns merge=False -> function returns False."""
        llm_response = {"merge": False, "reason": "Distinct research areas."}
        with patch("wikify.wiki.graph.domains.complete_json", return_value=llm_response):
            result = mod.check_community_merge(
                "ALD Process", "Scope A",
                "Machine Learning", "Scope B",
                [],
                model="test-model",
            )
        assert result is False

    def test_llm_failure_defaults_to_no_merge(self):
        """When LLM raises, defaults to False (no merge)."""
        with patch(
            "wikify.wiki.graph.domains.complete_json",
            side_effect=RuntimeError("network error"),
        ):
            result = mod.check_community_merge(
                "A", "scope a",
                "B", "scope b",
                [],
                model="test-model",
            )
        assert result is False

    def test_llm_returns_non_dict_defaults_false(self):
        """Non-dict LLM response -> False."""
        with patch("wikify.wiki.graph.domains.complete_json", return_value=["merge"]):
            result = mod.check_community_merge(
                "A", "",
                "B", "",
                [],
                model="test-model",
            )
        assert result is False


# ---------------------------------------------------------------------------
# compute_topology_metrics
# ---------------------------------------------------------------------------


class TestComputeTopologyMetrics:
    def test_two_clear_communities(self):
        """Two dense clusters produce a reasonable TopologySnapshot."""
        graph = _two_cluster_graph()
        communities = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}
        roles = {n: "core" for n in graph.nodes()}

        snapshot = mod.compute_topology_metrics(graph, communities, epoch=1, roles=roles)

        assert isinstance(snapshot, TopologySnapshot)
        assert snapshot.community_count == 2
        assert snapshot.total_concepts == 6
        assert snapshot.total_edges == graph.number_of_edges()
        assert snapshot.modularity_q > 0.0, "Two clear clusters should have positive Q"
        assert 0.0 <= snapshot.inter_community_edge_ratio <= 1.0
        assert 0.0 <= snapshot.bridge_density <= 1.0

    def test_empty_graph(self):
        """Empty graph returns a zero-filled TopologySnapshot."""
        graph = nx.DiGraph()
        communities: dict[str, int] = {}
        roles: dict[str, str] = {}

        snapshot = mod.compute_topology_metrics(graph, communities, epoch=3, roles=roles)

        assert isinstance(snapshot, TopologySnapshot)
        assert snapshot.epoch == 3
        assert snapshot.modularity_q == 0.0
        assert snapshot.inter_community_edge_ratio == 0.0
        assert snapshot.bridge_density == 0.0
        assert snapshot.community_gini == 0.0
        assert snapshot.spectral_gap == 0.0
        assert snapshot.community_count == 0
        assert snapshot.total_concepts == 0
        assert snapshot.total_edges == 0

    def test_epoch_is_recorded(self):
        """The epoch argument is stored on the snapshot."""
        graph = _two_cluster_graph()
        communities = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}
        roles = {n: "peripheral" for n in graph.nodes()}

        snapshot = mod.compute_topology_metrics(graph, communities, epoch=7, roles=roles)
        assert snapshot.epoch == 7

    def test_bridge_density_reflects_roles(self):
        """Bridge nodes in roles should inflate bridge_density."""
        graph = _two_cluster_graph()
        communities = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}
        # Mark one node as bridge
        roles = {n: "peripheral" for n in graph.nodes()}
        roles["a"] = "bridge"

        snapshot = mod.compute_topology_metrics(graph, communities, epoch=1, roles=roles)
        expected_density = 1 / 6
        assert abs(snapshot.bridge_density - expected_density) < 1e-9

    def test_roles_computed_internally_when_not_provided(self):
        """When roles=None, the function should compute roles without error."""
        graph = _two_cluster_graph()
        communities = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "f": 1}

        # score_importance calls get_session; patch it out
        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)
        exec_result = MagicMock()
        exec_result.all.return_value = []
        session.exec.return_value = exec_result

        # score_importance is imported inside the function body from concept_graph;
        # classify_node_roles is imported at module level in domains.py.
        with patch("wikify.wiki.graph.build.score_importance") as mock_si, \
             patch("wikify.wiki.graph.domains.classify_node_roles") as mock_cr:
            mock_si.return_value = {n: 0.5 for n in graph.nodes()}
            mock_cr.return_value = {n: "peripheral" for n in graph.nodes()}
            snapshot = mod.compute_topology_metrics(graph, communities, epoch=1, roles=None)

        assert isinstance(snapshot, TopologySnapshot)
        mock_si.assert_called_once_with(graph)
        mock_cr.assert_called_once()


# ---------------------------------------------------------------------------
# assign_concepts_to_domains
# ---------------------------------------------------------------------------


class TestAssignConceptsToDomains:
    def _build_scenario(self):
        """
        Two clusters:
          - cluster_0 contains concepts "a" (core) and "b" (core)
          - cluster_1 contains concept "d" (core)
          - concept "c" is a bridge with edges to both clusters

        Graph edges: a-b, a-c (c bridges into cluster_1 via edge c-d)
        """
        graph = nx.DiGraph()
        for u, v in [("a", "b"), ("b", "a"), ("a", "c"), ("c", "a"),
                     ("c", "d"), ("d", "c")]:
            graph.add_edge(u, v, weight=1.0)

        communities = {"a": 0, "b": 0, "c": 0, "d": 1}
        roles = {"a": "core", "b": "core", "c": "bridge", "d": "core"}

        # cluster_0 has core "a"; cluster_1 has core "d"
        cluster_0 = _make_cluster("cluster_0", "ALD Domain", core_concept_ids=["a"])
        cluster_1 = _make_cluster("cluster_1", "ML Domain", core_concept_ids=["d"])
        clusters = [cluster_0, cluster_1]

        records = {
            "a": _make_concept("a", "ConceptA"),
            "b": _make_concept("b", "ConceptB"),
            "c": _make_concept("c", "ConceptC"),
            "d": _make_concept("d", "ConceptD"),
        }

        return graph, communities, roles, clusters, records

    def test_interior_concept_gets_single_domain(self):
        """Non-bridge concepts are assigned to their own cluster only."""
        graph, communities, roles, clusters, records = self._build_scenario()

        session = _make_session_get(records)

        with patch("wikify.wiki.graph.domains.get_session", return_value=session):
            mod.assign_concepts_to_domains(communities, roles, clusters, graph)

        # "a" is core in cluster_0 -> domains = ["cluster_0"]
        rec_a = records["a"]
        assert json.loads(rec_a.domains) == ["cluster_0"]

    def test_bridge_concept_gets_multiple_domains(self):
        """Bridge concepts spanning two clusters receive both cluster IDs."""
        graph, communities, roles, clusters, records = self._build_scenario()

        session = _make_session_get(records)

        with patch("wikify.wiki.graph.domains.get_session", return_value=session):
            mod.assign_concepts_to_domains(communities, roles, clusters, graph)

        rec_c = records["c"]
        domains_c = json.loads(rec_c.domains)
        # "c" has neighbors in both community 0 and community 1
        assert len(domains_c) > 1, f"Bridge 'c' should span multiple domains: {domains_c}"
        assert "cluster_0" in domains_c
        assert "cluster_1" in domains_c

    def test_session_commit_called(self):
        """session.commit() must be called once."""
        graph, communities, roles, clusters, records = self._build_scenario()
        session = _make_session_get(records)

        with patch("wikify.wiki.graph.domains.get_session", return_value=session):
            mod.assign_concepts_to_domains(communities, roles, clusters, graph)

        session.commit.assert_called_once()

    def test_missing_concept_record_skipped(self):
        """If a concept_id has no matching ConceptRecord, it is silently skipped."""
        graph, communities, roles, clusters, records = self._build_scenario()
        # Remove "b" from the lookup so session.get returns None for it
        del records["b"]

        session = _make_session_get(records)

        with patch("wikify.wiki.graph.domains.get_session", return_value=session):
            # Should not raise
            mod.assign_concepts_to_domains(communities, roles, clusters, graph)

        # "b" was skipped, so it should not have been added
        assert session.add.call_count == len(records)  # 3 remaining concepts


# ---------------------------------------------------------------------------
# get_domain_for_query
# ---------------------------------------------------------------------------


class TestGetDomainForQuery:
    def test_routes_to_nearest_cluster(self):
        """Query closest to cluster_1's centroid -> primary is cluster_1."""
        cluster_0 = _make_cluster("cluster_0", "ALD Domain",
                                  centroid_embedding=[0.0, 1.0, 0.0])
        cluster_1 = _make_cluster("cluster_1", "ML Domain",
                                  centroid_embedding=[1.0, 0.0, 0.0])

        # Query points along x-axis -> closer to cluster_1
        query = [1.0, 0.0, 0.0]
        primary, expansion = mod.get_domain_for_query(query, [cluster_0, cluster_1])

        assert primary.id == "cluster_1"

    def test_expansion_includes_similar_clusters(self):
        """Clusters with similarity > 0.7 * primary_sim should appear in expansion."""
        # Three clusters: primary is very close to query, second is somewhat close
        cluster_a = _make_cluster("a", "A", centroid_embedding=[1.0, 0.0])
        cluster_b = _make_cluster("b", "B", centroid_embedding=[0.9, 0.4])  # cos ~ 0.91
        cluster_c = _make_cluster("c", "C", centroid_embedding=[0.0, 1.0])  # orthogonal

        query = [1.0, 0.0]
        primary, expansion = mod.get_domain_for_query(query, [cluster_a, cluster_b, cluster_c])

        assert primary.id == "a"
        expansion_ids = {cl.id for cl in expansion}
        # cluster_b should be close enough to be in expansion
        assert "b" in expansion_ids
        # cluster_c is orthogonal (sim=0) -> should NOT be in expansion
        assert "c" not in expansion_ids

    def test_single_cluster_no_expansion(self):
        """With a single cluster, expansion list should be empty."""
        cluster = _make_cluster("only", "Only Domain", centroid_embedding=[1.0, 0.0])
        primary, expansion = mod.get_domain_for_query([1.0, 0.0], [cluster])

        assert primary.id == "only"
        assert expansion == []

    def test_empty_clusters_raises(self):
        """Empty clusters list must raise ValueError."""
        with pytest.raises(ValueError, match="empty"):
            mod.get_domain_for_query([1.0, 0.0], [])

    def test_clusters_without_centroids_fall_back(self):
        """Clusters with empty centroid_embedding are skipped; fallback to first cluster."""
        cluster_no_centroid = _make_cluster("nc", "No Centroid", centroid_embedding=[])
        primary, expansion = mod.get_domain_for_query(
            [1.0, 0.0], [cluster_no_centroid]
        )
        # Falls back to first cluster in list
        assert primary.id == "nc"
        assert expansion == []


# ---------------------------------------------------------------------------
# expand_via_bridges
# ---------------------------------------------------------------------------


class TestExpandViaBridges:
    def test_returns_expansion_concepts_via_bridge(self):
        """Bridge node in primary with neighbour in expansion -> that neighbour is returned."""
        # primary cluster has bridge "X"; expansion cluster has core "Y"
        # graph: X -> Y, Y -> X
        graph = nx.DiGraph()
        graph.add_edge("X", "Y", weight=1.0)
        graph.add_edge("Y", "X", weight=1.0)
        # Also add some primary-internal node
        graph.add_edge("A", "X", weight=1.0)
        graph.add_edge("X", "A", weight=1.0)

        primary = _make_cluster(
            "primary", "Primary",
            bridge_concept_ids=["X"],
            core_concept_ids=["A"],
        )
        expansion = _make_cluster(
            "expansion", "Expansion",
            core_concept_ids=["Y"],
            bridge_concept_ids=[],
        )

        result = mod.expand_via_bridges(primary, [expansion], graph)

        assert "Y" in result

    def test_no_shared_bridges_returns_empty(self):
        """If bridge has no neighbours in expansion cluster, return empty list."""
        graph = nx.DiGraph()
        graph.add_edge("X", "A", weight=1.0)  # X connects only to A in primary

        primary = _make_cluster("primary", "Primary",
                                bridge_concept_ids=["X"],
                                core_concept_ids=["A"])
        expansion = _make_cluster("expansion", "Expansion",
                                  core_concept_ids=["D"],
                                  bridge_concept_ids=[])

        result = mod.expand_via_bridges(primary, [expansion], graph)
        assert result == []

    def test_empty_expansion_clusters(self):
        """No expansion clusters -> empty result."""
        graph = nx.DiGraph()
        graph.add_edge("X", "Y", weight=1.0)
        primary = _make_cluster("primary", "Primary", bridge_concept_ids=["X"])

        result = mod.expand_via_bridges(primary, [], graph)
        assert result == []

    def test_result_is_sorted_and_deduplicated(self):
        """Returned concept IDs should be sorted and deduplicated."""
        graph = nx.DiGraph()
        # Bridge "X" connects to both "Y" and "Z" in expansion
        for u, v in [("X", "Y"), ("Y", "X"), ("X", "Z"), ("Z", "X")]:
            graph.add_edge(u, v, weight=1.0)

        primary = _make_cluster("primary", "Primary", bridge_concept_ids=["X"])
        expansion = _make_cluster("expansion", "Expansion",
                                  core_concept_ids=["Y", "Z"],
                                  bridge_concept_ids=[])

        result = mod.expand_via_bridges(primary, [expansion], graph)
        # All results should be in expansion cluster's concept set
        assert set(result) <= {"Y", "Z"}
        assert result == sorted(result), "Result should be sorted"
        assert len(result) == len(set(result)), "Result should be deduplicated"


# ---------------------------------------------------------------------------
# discover_domains — low-modularity short-circuit
# ---------------------------------------------------------------------------


class TestDiscoverDomainsLowModularity:
    def test_low_modularity_creates_single_domain_no_llm(self):
        """When modularity Q < 0.3, a single catch-all domain is created without LLM calls."""
        # Build a highly connected graph where Q is expected to be low
        graph = nx.DiGraph()
        nodes = [f"n{i}" for i in range(6)]
        for i, u in enumerate(nodes):
            for v in nodes[i + 1:]:
                graph.add_edge(u, v, weight=1.0)
                graph.add_edge(v, u, weight=1.0)

        low_q_snapshot = TopologySnapshot(
            epoch=1,
            modularity_q=0.1,
            inter_community_edge_ratio=0.8,
            bridge_density=0.1,
            community_gini=0.0,
            spectral_gap=1.0,
            community_count=2,
            total_concepts=6,
            total_edges=graph.number_of_edges(),
        )
        communities = {n: 0 for n in nodes}
        roles = {n: "peripheral" for n in nodes}

        session = MagicMock()
        session.__enter__ = lambda s: session
        session.__exit__ = MagicMock(return_value=False)
        session.get.return_value = None  # no ConceptRecord rows

        with patch("wikify.wiki.graph.domains.detect_communities", return_value=communities), \
             patch("wikify.wiki.graph.build.score_importance", return_value={n: 0.5 for n in nodes}), \
             patch("wikify.wiki.graph.domains.classify_node_roles", return_value=roles), \
             patch("wikify.wiki.graph.domains.compute_topology_metrics",
                   return_value=low_q_snapshot), \
             patch("wikify.wiki.graph.domains.complete_json") as mock_llm, \
             patch("wikify.wiki.graph.domains.get_or_create_persona", return_value="persona"), \
             patch("wikify.wiki.graph.domains.get_session", return_value=session), \
             patch("wikify.wiki.graph.domains._persist_clusters"), \
             patch("wikify.wiki.graph.domains._persist_topology"), \
             patch("wikify.wiki.graph.domains.assign_concepts_to_domains"):
            clusters = mod.discover_domains(graph, epoch=1, model="test-model")

        # One catch-all cluster only
        assert len(clusters) == 1
        assert clusters[0].id == "general_domain"

        # LLM should NOT have been called for community validation
        mock_llm.assert_not_called()
