from __future__ import annotations

import numpy as np

from wikify.bundle.wiki.store import (
    WikiVectorIndex,
    active_wiki_space_id,
    apply_navigation_categories,
    export_navigation_json,
    list_wiki_categories,
    list_wiki_category_memberships,
    list_wiki_embedding_spaces,
    open_wiki_store,
    replace_wiki_category_tree,
    upsert_wiki_embedding_space,
    upsert_wiki_embeddings,
    upsert_wiki_page,
)


def test_wiki_embedding_upsert_search_and_vector_lookup(tmp_path):
    con = open_wiki_store(tmp_path / "wiki.db")
    try:
        upsert_wiki_page(
            con,
            page_id="Atomic Layer Deposition",
            slug="atomic-layer-deposition",
            title="Atomic Layer Deposition",
            kind="article",
            body="Thin-film growth by sequential surface reactions.",
        )
        upsert_wiki_page(
            con,
            page_id="Photocatalysis",
            slug="photocatalysis",
            title="Photocatalysis",
            kind="article",
            body="Light-driven catalytic reactions.",
        )
        upsert_wiki_embedding_space(con, "hash:dim3", "hash", None, 3)
        base = np.array([3.0, 0.0, 0.0], dtype="float32")
        upsert_wiki_embeddings(
            con,
            "hash:dim3",
            [
                ("Atomic Layer Deposition", base),
                ("Photocatalysis", np.array([0.0, 2.0, 0.0], dtype="float32")),
            ],
        )

        assert active_wiki_space_id(con) == "hash:dim3"
        assert [s["space_id"] for s in list_wiki_embedding_spaces(con)] == ["hash:dim3"]

        index = WikiVectorIndex(con, "hash:dim3")
        hits = index.search(base, top_k=2)
        assert hits[0][0] == "Atomic Layer Deposition"
        assert abs(hits[0][1] - 1.0) < 1e-5
        stored = index.vector("Atomic Layer Deposition")
        assert stored is not None
        assert abs(float(np.linalg.norm(stored)) - 1.0) < 1e-5

        upsert_wiki_embeddings(
            con,
            "hash:dim3",
            [
                ("Atomic Layer Deposition", np.array([0.0, 4.0, 0.0], dtype="float32")),
                ("Photocatalysis", np.array([4.0, 0.0, 0.0], dtype="float32")),
            ],
        )
        assert index.search(base, top_k=1)[0][0] == "Atomic Layer Deposition"
        index.invalidate()
        assert index.search(base, top_k=1)[0][0] == "Photocatalysis"
    finally:
        con.close()


def test_replace_wiki_category_tree_persists_hierarchy_and_memberships(tmp_path):
    con = open_wiki_store(tmp_path / "wiki.db")
    try:
        upsert_wiki_page(
            con,
            page_id="Atomic Layer Deposition",
            slug="atomic-layer-deposition",
            title="Atomic Layer Deposition",
            kind="article",
            body="Thin-film growth by sequential surface reactions.",
        )
        replace_wiki_category_tree(
            con,
            [
                {
                    "category_id": "materials",
                    "name": "Materials",
                    "confidence": 0.9,
                    "source": "test",
                    "rationale": {"seed": True},
                },
                {
                    "category_id": "thin-films",
                    "name": "Thin films",
                    "parent_id": "materials",
                    "confidence": 0.8,
                    "source": "test",
                },
            ],
            [
                {
                    "category_id": "thin-films",
                    "page_id": "Atomic Layer Deposition",
                    "confidence": 0.7,
                    "source": "test",
                    "rationale": {"match": "body"},
                },
            ],
        )

        categories = {r["category_id"]: r for r in list_wiki_categories(con)}
        assert categories["materials"]["parent_id"] is None
        assert categories["thin-films"]["parent_id"] == "materials"
        assert categories["materials"]["confidence"] == 0.9
        assert categories["materials"]["source"] == "test"
        assert categories["materials"]["rationale_json"] == '{"seed": true}'

        memberships = list_wiki_category_memberships(con)
        assert memberships == [
            {
                "category_id": "thin-films",
                "page_id": "Atomic Layer Deposition",
                "confidence": 0.7,
                "source": "test",
                "rationale_json": '{"match": "body"}',
                "created_at": memberships[0]["created_at"],
                "updated_at": memberships[0]["updated_at"],
            },
        ]

        replace_wiki_category_tree(
            con,
            [{"category_id": "methods", "name": "Methods"}],
            [],
        )
        assert [r["category_id"] for r in list_wiki_categories(con)] == ["methods"]
        assert list_wiki_category_memberships(con) == []
    finally:
        con.close()


def test_navigation_categories_round_trip_as_render_navigation(tmp_path):
    con = open_wiki_store(tmp_path / "wiki.db")
    try:
        upsert_wiki_page(
            con,
            page_id="Atomic Layer Deposition",
            slug="atomic-layer-deposition",
            title="Atomic Layer Deposition",
            kind="article",
            body="Thin-film growth by sequential surface reactions.",
        )
        apply_navigation_categories(
            con,
            {
                "groups": [
                    {
                        "id": "methods",
                        "title": "Methods",
                        "description": "Process pages.",
                        "page_ids": [],
                        "children": [
                            {
                                "id": "thin-films",
                                "title": "Thin films",
                                "description": "Film-growth pages.",
                                "page_ids": ["Atomic Layer Deposition"],
                                "children": [],
                            }
                        ],
                    }
                ]
            },
        )

        exported = export_navigation_json(con)

        assert exported["groups"][0]["id"] == "methods"
        assert exported["groups"][0]["children"][0]["id"] == "thin-films"
        assert exported["groups"][0]["children"][0]["page_ids"] == [
            "Atomic Layer Deposition"
        ]
        assert exported["ungrouped_page_ids"] == []
    finally:
        con.close()
