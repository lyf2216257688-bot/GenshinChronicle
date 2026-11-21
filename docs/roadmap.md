# Roadmap

This roadmap describes project direction. The [current stage](current-phase.md) owns the immediate action, adopted choices and authorization.

## Build on the evidence foundation

The original sequence established [Raw Collection](phases/phase-01-raw-collection.md), [Parsed](phases/phase-02-parsed-schema.md) and [Canonical](phases/phase-03-canonical-schema.md) over official OBC text. The [Legacy Phase04](phases/phase-04-retrieval-evidence-assembly.md#basic-rag-v1-delivery-contract) baseline established source-backed Retrieval Units, isolated lexical and Dense search, deterministic Assembly, provider-neutral Generation and local presentation. Historical milestones and operating points are in the [Phase04 register](research/phase-04/p04-rag-historical-checkpoint-register.md).

The Clean-based path uses direct Raw-to-Clean reconstruction, structural chunking, a common index input, bounded retrieval, separate reranking and evidence-grounded Generation. Development follows the [architecture](architecture-overview.md) and [stage contracts](current-phase.md#阶段合同). The [workbench](rag-workbench.md) routes the delivered components and operator entry points.

## Establish end-to-end quality from observed use

Evaluate the assembled evidence, traceable citations and answer support on real questions. Distinguish the responsible layer before scoping any optimization. Preserve the existing reproducible baselines and record limits of coverage, restart behavior and resource use before any Serving or broader adoption decision. The existing [retrieval, ordering and Generation reports](research/README.md) provide starting evidence for those decisions.

## Keep semantic expansion conditional

The [Phase05 navigation contract](phases/phase-05-source-bound-semantic-navigation.md) preserves source-bound hierarchy, Card/Link and event/relation experiments. Further work needs a demonstrated RAG bottleneck and a separate, bounded source-based acceptance scope. Global entity identity, claims, timelines and cross-language alignment need their own evidence and authorization before implementation.
