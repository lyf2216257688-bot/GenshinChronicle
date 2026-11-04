"""Local single-question CLI with the same runtime selectors as Streamlit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from genshin_corpus.rag.backend import RagBackendPreparationError
from genshin_corpus.ui.runtime import (
    UiConfigurationError,
    UiQuery,
    execute_query,
    prepare_production_state,
    production_artifact_paths,
    summarize_result,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--output-root", type=Path, default=Path("data/retrieval/p04-rag-cli"))
    parser.add_argument("--generate-answer", action="store_true", help="Explicitly enable configured Generation.")
    args = parser.parse_args(argv)
    mode = "generate_answer" if args.generate_answer else "evidence_only"
    try:
        owner = prepare_production_state(production_artifact_paths().resolved_key())
        try:
            with owner.lease() as state:
                result = execute_query(state, UiQuery(args.question, mode, args.output_root))
        finally:
            owner.close()
    except (RagBackendPreparationError, UiConfigurationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    summary = summarize_result(result)
    summary["embedding_profile"] = result["audit"]["embedding_profile"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
