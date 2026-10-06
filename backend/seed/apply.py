"""Apply and verify the reference seed world through application ports.

Seeding is idempotent: nodes and edges are upserted through the graph
repository port, and vector documents are upserted by stable id. Nothing here
imports a driver or opens a connection at module import time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.graph.schema import Edge, Node
from backend.ports import EmbeddingPort, GraphRepository, VectorStorePort
from backend.seed.manifest import SeedManifest

DOCUMENT_ID_PREFIX = "seed:"


@dataclass
class SeedReport:
    version: str
    checksum: str
    counts: dict[str, int]
    applied_nodes: int = 0
    applied_edges: int = 0
    indexed_documents: int = 0
    skipped_documents: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def apply_graph(graph: GraphRepository, manifest: SeedManifest) -> tuple[int, int]:
    for node in manifest.nodes:
        graph.add_node(Node(id=node.id, type=node.type, properties=dict(node.properties)))
    for edge in manifest.edges:
        graph.add_edge(
            Edge(
                source=edge.source,
                target=edge.target,
                type=edge.type,
                properties=dict(edge.properties),
            )
        )
    return len(manifest.nodes), len(manifest.edges)


def index_documents(
    vectors: VectorStorePort, embeddings: EmbeddingPort, manifest: SeedManifest
) -> tuple[int, int]:
    """Index prose snippets when embeddings are available; otherwise skip."""
    if not embeddings.available():
        return 0, len(manifest.documents)

    documents: list[str] = []
    metadatas: list[dict[str, object]] = []
    ids: list[str] = []
    for document in manifest.documents:
        metadata = dict(document.metadata)
        metadata["seed_version"] = manifest.version
        documents.append(document.text)
        metadatas.append(metadata)
        ids.append(f"{DOCUMENT_ID_PREFIX}{document.id}")

    vectors.add_documents(documents=documents, metadatas=metadatas, ids=ids)
    return len(ids), 0


def run_expected_queries(graph: GraphRepository, manifest: SeedManifest) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for query in manifest.queries:
        if query.kind == "name_search":
            found = [match["id"] for match in graph.search_nodes_by_name(query.argument)]
        elif query.kind == "node_type":
            found = [node.id for node in graph.get_nodes(node_type=query.argument)]
        elif query.kind == "neighbors":
            found = []
            for edge in graph.get_edges(query.argument):
                found.append(edge.target if edge.source == query.argument else edge.source)
        else:
            raise ValueError(f"Unknown seed query kind: {query.kind}")
        results[query.id] = sorted(set(found))
    return results


def verify_manifest(graph: GraphRepository, manifest: SeedManifest) -> list[str]:
    problems: list[str] = []

    for node in manifest.nodes:
        stored = graph.get_node(node.id)
        if stored is None:
            problems.append(f"missing node {node.id}")
        elif stored.type != node.type:
            problems.append(f"node {node.id} type {stored.type!r} != {node.type!r}")

    for edge in manifest.edges:
        edges = graph.get_edges(edge.source)
        if not any(
            candidate.target == edge.target and candidate.type == edge.type for candidate in edges
        ):
            problems.append(f"missing edge {edge.source}-[{edge.type}]->{edge.target}")

    for query in manifest.queries:
        expected = sorted(query.expected_ids)
        actual = run_expected_queries(graph, manifest).get(query.id, [])
        if actual != expected:
            problems.append(f"query {query.id} expected {expected} got {actual}")

    return problems


def apply_manifest(
    graph: GraphRepository,
    manifest: SeedManifest,
    vectors: VectorStorePort | None = None,
    embeddings: EmbeddingPort | None = None,
) -> SeedReport:
    report = SeedReport(
        version=manifest.version,
        checksum=manifest.checksum(),
        counts=manifest.counts(),
    )
    report.applied_nodes, report.applied_edges = apply_graph(graph, manifest)

    if vectors is not None and embeddings is not None:
        report.indexed_documents, report.skipped_documents = index_documents(
            vectors, embeddings, manifest
        )

    report.problems = verify_manifest(graph, manifest)
    return report
