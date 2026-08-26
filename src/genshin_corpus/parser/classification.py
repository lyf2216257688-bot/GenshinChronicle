from __future__ import annotations

from .contracts import Classification


CLASSIFICATION_TAXONOMY_VERSION = "phase02-draft-0.1"
OBC_COMPONENT_ROLE_RULE = "obc-component-role"
OBC_COMPONENT_ROLE_RULE_VERSION = "0.1"


def classify_obc_component(component_id: str | None) -> tuple[Classification, Classification]:
    """Return only source-structure rules supported by current evidence.

    Provenance is deliberately not inferred from the OBC component name.  The
    role rule describes the page component's observed function, not a
    Canonical semantic type or an author classification.
    """

    if component_id == "interactive_dialogue":
        return (
            Classification(taxonomy_version=CLASSIFICATION_TAXONOMY_VERSION),
            Classification(
                state="classified",
                labels=("dialogue",),
                basis=("source_component_id:interactive_dialogue",),
                taxonomy_version=CLASSIFICATION_TAXONOMY_VERSION,
                rule_id=OBC_COMPONENT_ROLE_RULE,
                rule_version=OBC_COMPONENT_ROLE_RULE_VERSION,
            ),
        )
    return (
        Classification(taxonomy_version=CLASSIFICATION_TAXONOMY_VERSION),
        Classification(taxonomy_version=CLASSIFICATION_TAXONOMY_VERSION),
    )
