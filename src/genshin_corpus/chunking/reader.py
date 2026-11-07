"""Read an entire natural source parent, independently of retrieval admission."""
from copy import deepcopy

from ..clean import iter_blocks


def read_natural_parent(parent: dict, record: dict, *, blocks=None):
    if parent["observation_id"] != record["observation_id"]:
        raise ValueError("Natural parent observation mismatch")
    if parent["kind"] == "document_description":
        return deepcopy(record["document"]["description"])
    if parent["kind"] == "section":
        section = next(section for section in record["sections"] if section["section_id"] == parent["section_id"])
        return deepcopy(section["components"][parent["component_index"]])
    if blocks is None:
        blocks = {block["block_id"]: block for block in iter_blocks(record)}

    def original(block_id):
        block = deepcopy(blocks[block_id])
        block.pop("context", None)
        return block

    if parent["kind"] == "dialogue_group":
        block = original(parent["context"]["dialogue_block_id"])
        return next(group for group in block["groups"] if group["group_id"] == parent["context"]["group_id"])
    if parent["kind"] == "heading":
        return [original(block_id) for block_id in parent["readback_block_ids"]]
    return original(parent["source_container"].split("/")[0])
