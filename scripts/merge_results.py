#!/usr/bin/env python3
"""Merge agent output into the graph, then verify, gate and report.

    python scripts/merge_results.py -w runs/demo --evidence out/researcher.json

Accepted input files (repeatable), each either

    {"evidence": [ {evidence object}, ... ],
     "graph_update": {"add_nodes": [...], "add_edges": [...], "remove_ids": [...]}}

or a bare list of evidence objects. Everything is written through the runtime - the
only writer of the graph - so schema validation, dedupe and propagation still apply.
PRP.md §4 entry point; exits non-zero when the quality gate fails.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from runtime.cli import Session, main  # noqa: E402
from runtime.models import Evidence, GraphUpdate  # noqa: E402
from runtime.util import read_json  # noqa: E402


def _evidence_items(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        items = payload.get("evidence") or payload.get("items") or []
        return [item for item in items if isinstance(item, dict)]
    return []


def _update(payload: object) -> GraphUpdate | None:
    if not isinstance(payload, dict):
        return None
    body = payload.get("graph_update") or payload.get("update")
    if not isinstance(body, dict):
        return None
    return GraphUpdate(
        add_nodes=list(body.get("add_nodes") or []),
        add_edges=list(body.get("add_edges") or []),
        remove_ids=list(body.get("remove_ids") or []),
        reason=str(body.get("reason") or "merged by scripts/merge_results.py"),
    )


def merge(session: Session, paths: list[pathlib.Path], agent: str) -> dict[str, int]:
    from runtime.evidence_store import EvidenceStore
    from runtime.graph_engine import ResearchGraph

    graph = ResearchGraph.load(session.workspace.path("graph"))
    store = EvidenceStore.open(session.workspace.path("evidence"))
    added_evidence = 0
    added_nodes = 0
    rejected = 0
    for path in paths:
        payload = read_json(path, None)
        if payload is None:
            print(f"skip unreadable file: {path}", file=sys.stderr)
            continue
        for item in _evidence_items(payload):
            try:
                record = Evidence.from_dict(item)
            except Exception as error:  # noqa: BLE001 - report and keep merging
                rejected += 1
                print(f"invalid evidence in {path.name}: {error}", file=sys.stderr)
                continue
            stored = store.add(record)
            added_evidence += 1
            for node_id in stored.supports or ["goal"]:
                if node_id in graph.nodes:
                    graph.attach_evidence(stored, node_id, store)
        update = _update(payload)
        if update is not None:
            before = len(graph.nodes)
            _apply(graph, update, agent)
            added_nodes += len(graph.nodes) - before
    store.save()
    graph.propagate(store)
    graph.save()
    session.record("results_merged", files=[str(path) for path in paths], evidence=added_evidence, nodes=added_nodes, rejected=rejected)
    return {"evidence": added_evidence, "nodes": added_nodes, "rejected": rejected}


def _apply(graph, update: GraphUpdate, agent: str) -> None:
    for node in update.add_nodes:
        graph.add_node(
            str(node.get("type") or "topic"),
            str(node.get("title") or ""),
            node_id=node.get("id"),
            body=str(node.get("body") or ""),
            created_by=agent,
            **{key: value for key, value in node.items() if key not in {"type", "title", "id", "body"}},
        )
    for edge in update.add_edges:
        graph.add_edge(
            str(edge.get("from") or ""),
            str(edge.get("to") or ""),
            str(edge.get("type") or "relates_to"),
            float(edge.get("weight", 1.0) or 1.0),
            str(edge.get("note") or ""),
            created_by=agent,
            strict=False,
        )
    for node_id in update.remove_ids:
        graph.remove_node(str(node_id))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--evidence", "-e", action="append", default=[], help="agent output file (repeatable)")
    parser.add_argument("--agent", default="host-agent")
    parser.add_argument("--skip-report", action="store_true")
    args = parser.parse_args(argv)
    root = pathlib.Path(args.workspace).expanduser()
    session = Session.open(root)
    paths = [pathlib.Path(item).expanduser() for item in args.evidence]
    if paths:
        summary = merge(session, paths, args.agent)
        print("merged: " + ", ".join(f"{key}={value}" for key, value in summary.items()))
    base = ["--workspace", str(root.resolve())]
    code = main([*base, "verify", "--apply"])
    code = main([*base, "gate", "--save"]) or code
    if not args.skip_report:
        code = main([*base, "report"]) or code
    return int(code)


if __name__ == "__main__":
    raise SystemExit(cli())
