"""Opt-in two-stage semantic navigation experiment.

Stage A builds a high-recall inventory. Stage B verifies that inventory against
the original source projection and emits a compact navigation view. Both stages
use the existing source-bound output contract; the inventory is never a source
of truth and is persisted as an intermediate experiment artifact only.
"""

from __future__ import annotations

import json
import argparse
import gzip
import os
from html.parser import HTMLParser
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

from genshin_corpus.canonical.fingerprints import canonical_json_bytes, sha256_json

from .semantic_live_runner import (
    B_V4_EXPERIMENT_REVISION,
    STRICT_SOURCE_BINDING_POLICY,
    b_v4_experiment_contract,
    b_v4_prompt_contract,
    b_v5_prompt_contract,
)
from .semantic_compiler_u1 import (
    semantic_input_identity,
    semantic_segment_binding_metadata,
    validate_semantic_output_envelope,
)
from .semantic_live_runner import validate_b_v2_navigation_references
from .semantic_sdk_runner import frozen_v5_gate_units, run_sdk_route_pair, _wire_body_for_surface
from .semantic_tokenmetro_profile import route_profile


TWO_STAGE_REVISION = "phase05-w2-two-stage-navigation-0.1"
TWO_STAGE_CURATOR_PROMPT_VERSION = "phase05-w2-two-stage-curator-prompt-0.3"
GROUPED_NAVIGATION_PROMPT_VERSION = "phase05-w2-inventory-navigation-view-prompt-0.1"
ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.7"
V06_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.6"
V05_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.5"
PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.4"
PREVIOUS_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.3"
OLDER_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.2"
LEGACY_ATOMIC_INVENTORY_PROMPT_VERSION = "phase05-w2-atomic-inventory-prompt-0.1"


def atomic_inventory_prompt_contract(version: str = ATOMIC_INVENTORY_PROMPT_VERSION) -> dict[str, Any]:
    """High-recall Stage A with one source-anchored proposition per item."""
    if version not in {LEGACY_ATOMIC_INVENTORY_PROMPT_VERSION, OLDER_ATOMIC_INVENTORY_PROMPT_VERSION,
                        PREVIOUS_ATOMIC_INVENTORY_PROMPT_VERSION,
                        PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION,
                        V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                        V06_ATOMIC_INVENTORY_PROMPT_VERSION,
                        ATOMIC_INVENTORY_PROMPT_VERSION}:
        raise ValueError("unsupported atomic inventory prompt version")
    prompt = b_v4_prompt_contract()
    prompt["version"] = version
    prompt["task"] = (
        "Build a complete source-bound inventory of atomic propositions for every supplied stage. "
        "This is the recall ledger consumed by a later navigation index; do not optimize for a small "
        "item count and do not group propositions merely because they share an actor or sentence. "
        "Each item must represent one independently stated proposition with one clear qualifier scope. "
        "The inventory may be verbose; correctness of proposition boundaries and qualifiers takes priority "
        "over compactness."
    )
    prompt["extraction_rules"] = [
        "Read every source stage completely. Preserve every source-supported plot, event, state, goal, identity, location, relationship, memory, emotional state, attribution, uncertainty, negation, and temporal change that is useful for later navigation.",
        "Emit one item per atomic proposition. Split coordinated clauses whenever they have a different actor, action, object, state, time, condition, attribution, modality, polarity, or epistemic target. A single source sentence may produce several items. Do not merge separate claims to reduce count.",
        "Every item.qualifiers.source_quote must be an exact contiguous substring of the supplied original source text and must cover the proposition in that item. The quote is an audit anchor, not a new evidence source. Do not cite inventory, summaries, or paraphrases as quotes.",
        "Use only qualifiers.attribution, qualifiers.modality, qualifiers.polarity, qualifiers.time, qualifiers.condition, and qualifiers.source_quote. Do not use negation, epistemic, negated_states, or other substitute fields. Qualifier scope is the single proposition represented by the item.",
        "Use modality=uncertain for unknown identity, target, or sound and for tentative/epistemic language. Use modality=rhetorical for a question whose wording does not assert that the asked proposition occurred. Use condition only for a genuine if/when/purpose condition, never as a label for a rhetorical question. Use polarity=negative only for an explicitly negated action, denial, refusal, prohibition, or non-existence. Inability or not daring is modality; not knowing is not negative. For example, 我的心未曾晓得这声音 means the sound is present but its nature is unknown, so use modality=uncertain and omit polarity. A statement that something was not previously seen/known is a distinct negated experience and must not be merged with another claim.",
        "Preserve attribution and reported speech at the smallest proposition scope. Split reported commands into affirmative waiting and separate prohibitions when their polarity differs. Split exterior absence of light from interior presence of light. Split purpose from the action it serves. Split rhetorical questions from any surrounding state. Split coordinated objects whenever each can receive a different qualifier: 我不能忘记我的哀情 and 除去我的苦楚 are two items, even though they occur in one clause. Also split crying, tears, duration, uncertain mourning target, memory/name state, and inability to overcome grief when they are separately stated.",
        "Choose a source_quote that is an exact contiguous substring and is long enough to identify the proposition and its local occurrence. Repeated text at different source positions may produce separate candidates; preserve each occurrence rather than deduplicating by quote text. Do not use one shared short quote for multiple propositions when a longer local quote can distinguish them.",
        "Mentions may be emitted only when referenced by an event/relation or independently useful for navigation, but a mention cannot absorb a fact or qualifier-bearing proposition. Relations must use explicit local references and predicates; do not duplicate an event or invent a generic relation.",
        "The inventory is candidate-only. Unsupported or genuinely ambiguous material retains explicit segment disposition; ambiguity must not be silently converted into certainty. Segment coverage is input accounting, not semantic completeness.",
    ]
    prompt["item_contract"]["rules"] = [
        *prompt["item_contract"]["rules"],
        "qualifiers.source_quote is required for every fact/event/relation/mention/topic item and must be an exact source substring",
        "each item contains one proposition and one qualifier scope; local references do not merge proposition scopes",
    ]
    prompt["source_binding_policy"] = STRICT_SOURCE_BINDING_POLICY
    if version == LEGACY_ATOMIC_INVENTORY_PROMPT_VERSION:
        prompt["task"] = (
            "Build a complete source-bound inventory of atomic propositions for every supplied stage. "
            "This is the recall ledger consumed by a later navigation index; do not optimize for a small "
            "item count and do not group propositions merely because they share an actor or sentence. "
            "Each item must represent one independently stated proposition with one clear qualifier scope."
        )
        prompt["extraction_rules"] = [
            rule for rule in prompt["extraction_rules"]
            if not rule.startswith("Choose a source_quote that is an exact contiguous substring")
        ]
        prompt["extraction_rules"][4] = (
            "Use modality=uncertain for unknown identity, target, or sound and for tentative/epistemic language. "
            "Use polarity=negative only for an explicitly negated action, denial, refusal, prohibition, or non-existence. "
            "Inability or not daring is modality; not knowing is not negative. A statement that something was not "
            "previously seen/known is a distinct negated experience and must not be merged with another claim."
        )
        prompt["extraction_rules"][5] = (
            "Preserve attribution and reported speech at the smallest proposition scope. Split reported commands "
            "into affirmative waiting and separate prohibitions when their polarity differs. Split exterior "
            "absence of light from interior presence of light. Split crying, tears, duration, uncertain mourning "
            "target, memory/name state, and inability to overcome grief when they are separately stated."
        )
    elif version in {PREVIOUS_ATOMIC_INVENTORY_PROMPT_VERSION,
                     PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION,
                     V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                     V06_ATOMIC_INVENTORY_PROMPT_VERSION, ATOMIC_INVENTORY_PROMPT_VERSION}:
        prompt["extraction_rules"][4] = (
            "Qualify the smallest independently stated proposition. For a known observation with an unknown identity, "
            "write a certain claim about the observer's knowledge state and use modality=unknown_identity; for an "
            "unknown recipient or object use modality=unknown_target. These are not doubts that the observed event "
            "happened. Use modality=tentative only when the source itself makes the proposition uncertain (似乎、或许、"
            "可能); use modality=rhetorical for a question that does not assert the asked proposition. Use condition "
            "only for a genuine if/when/purpose clause, never as a substitute for modality. Use polarity=negative "
            "only for an explicitly denied action, refusal, prohibition, absence, or negated experience. "
            "我的心未曾晓得这声音 says a sound was heard but its identity is unknown: label the unknown sound identity, "
            "use modality=unknown_identity, and omit polarity. 我的眼未曾见过这殿宇 states a negated prior seeing "
            "experience: use polarity=negative without implying that the temple does not exist. "
            "虽不知是谁的嘱咐 is unknown speaker identity; 却不知是在为何人举哀 is unknown mourning target. "
            "Inability and not daring remain distinct modalities."
        )
        prompt["extraction_rules"].append(
            "Before returning, inspect every item for independent truth and qualifier scope. A shared source_quote "
            "may include a shared modal verb or nearby context, but the label and qualifiers must describe only one "
            "of its coordinated predicates. Split 我不能忘记我的哀情 from 我不能除去我的苦楚, even when both quote "
            "the shared 不能. Never treat a source quote overlapping two claims as permission to fuse their labels. "
            "If the scope of 竟忘记了我的名字与所行的 cannot be resolved from the sentence, mark that claim "
            "modality=ambiguous instead of asserting a certain completed forgetting. Do not invent a definite "
            "identity or event from a rhetorical image. Preserve every source-supported independent claim."
        )
    if version in {PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION,
                   V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                   V06_ATOMIC_INVENTORY_PROMPT_VERSION, ATOMIC_INVENTORY_PROMPT_VERSION}:
        prompt["extraction_rules"][4] += (
            " Distinguish knowledge/recognition from prior experience: 不认识它们 and 未曾晓得这声音 describe "
            "unknown identity or nature, so use unknown_identity and omit polarity; 未曾见过这殿宇 describes a "
            "negative prior-seeing experience, so polarity=negative is allowed. Do not generalize negative polarity "
            "from one clause to another."
        )
        prompt["extraction_rules"].append(
            "Use the shortest exact contiguous source_quote that fully supports the one labeled proposition. Do not "
            "quote a complete sentence or paragraph when a clause is sufficient, and do not let a quote cross into "
            "the next independently stated predicate merely to provide context. Shared grammar may require nearby "
            "context, but the item label and qualifier still cover one predicate only; adjacent claims must be emitted "
            "as separate items with separately scoped labels and qualifiers."
        )
    if version in {V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                   V06_ATOMIC_INVENTORY_PROMPT_VERSION, ATOMIC_INVENTORY_PROMPT_VERSION}:
        prompt["extraction_rules"].append(
            "Before assigning unknown_identity or unknown_target to an omitted noun or object, resolve its "
            "antecedent in the immediately adjacent source clauses. If the source identifies the referent, name it "
            "in the one-proposition label and do not mark it unknown. For example, 有人把石头立作壁柱，用金子包裹 "
            "separately states that the stone wall pillars are wrapped with gold; the wrapping target is known. "
            "When the shortest predicate quote omits that referent, source_quote may include only the contiguous "
            "antecedent clause needed to audit it, while the label and qualifiers remain scoped to the wrapping "
            "predicate alone. If more than one referent remains plausible, preserve that uncertainty without "
            "guessing. Do not change unknown_identity for 未曾晓得这声音 or 不认识它们, or unknown_target for "
            "不知是在为何人举哀."
        )
    if version in {V06_ATOMIC_INVENTORY_PROMPT_VERSION, ATOMIC_INVENTORY_PROMPT_VERSION}:
        prompt["extraction_rules"][4] = prompt["extraction_rules"][4].replace(
            "Use condition only for a genuine if/when/purpose clause, never as a substitute for modality.",
            "Use condition only for an explicit if/when dependency, never as a substitute for modality or a goal.")
        prompt["extraction_rules"].append(
            "A purpose clause such as 好叫我心中畅快 is an intended outcome, not an achieved state: label it "
            "as the goal of making the heart glad, use modality=intended if needed, and do not use condition to "
            "make a false completed-state label. Keep the goal separate from the action that aims at it. "
            "qualifiers.attribution names a reported speaker or quoted source voice, not the action's actor; "
            "someone/有人 is an unspecified actor in a label, not attribution. In the context 我看不见穹苍的光亮；"
            "没有太阳发光，没有明月行在空中, 看不见 is a negated observation of visible light, not a claim "
            "that the narrator is physically unable to see. Scope each qualifier to its own predicate."
        )
    if version == ATOMIC_INVENTORY_PROMPT_VERSION:
        prompt["extraction_rules"].append(
            "A condition clause does not assert that its antecedent action happened. If the source says "
            "长按斯露莎的「互动」技能使其停驻于此的话, candidates for pressing and stopping must carry "
            "modality=conditional (or remain explicitly conditional in the label), while the resulting passage "
            "keeps its own condition and any tentative wording. Do not label the antecedent an achieved action. "
            "When a speaker immediately corrects a claim with 不对, preserve the first utterance as a "
            "self-corrected claim, with modality=self_corrected and a source_quote covering the local correction; "
            "keep the corrected proposition separate and asserted only within its source attribution. "
            "For 托马's 你果然还是和当初一样…不对，比当初更强了啊, the earlier same-as-before "
            "claim must not survive as an unqualified fact. Do not spread the correction to unrelated claims."
        )
    return prompt


def grouped_navigation_prompt_contract() -> dict[str, Any]:
    """A compact index over preserved candidates, not a rewritten fact ledger."""
    prompt = b_v5_prompt_contract()
    prompt["version"] = GROUPED_NAVIGATION_PROMPT_VERSION
    prompt["task"] = (
        "Verify the supplied candidate inventory against the entire original source. Build a compact "
        "navigation index OVER that preserved inventory, not a replacement semantic fact list. "
        "All substantive independent source-supported claims remain accessible as group members. "
        "Group labels are concise navigation headings, never fact authority. Original source stages "
        "are authoritative; inventory is fallible candidate material. Account explicitly for every "
        "inventory item and preserve source uncertainty, negation, attribution and state changes."
    )
    prompt["extraction_rules"] = [
        "Read source completely; check every candidate against it. Keep every substantive plot/state/goal/identity/location/relationship/emotional/memory/epistemic claim in the index, even when poetically phrased. Never silently drop a supported independent claim to shorten the view.",
        "Use only topic items in the existing output envelope. Each navigation group has qualifiers.role=navigation_group, qualifiers.group_kind equal to event, state, identity, location, or topic, qualifiers.member_ids listing exact inventory local_id values, and qualifiers.annotations (possibly empty). The label is a short source-supported navigation heading, not a paragraph. Group closely related candidates while keeping their separate original records accessible. A group may contain positive/negative/uncertain members because it has NO group-wide semantic qualifier.",
        "Represent a substantive candidate exactly once as a group member. Combine all equivalent singing/refrain candidates into one performance group; preserve distinct grief, tears, day/night duration, uncertain mourning target, warning, memory/name state and state/temporal changes as accessible members of appropriate groups. Use no fixed group count.",
        "A member is not a fact merely because it belongs to a navigation group. Correct inventory mistakes with scoped annotations checked against source, never by editing the original inventory. An annotation has ONLY member_ids, source_quote, qualifiers. source_quote must be an exact contiguous substring of the original source text, not inventory wording. member_ids must be members of this group. qualifiers applies ONLY to that quoted proposition, using attribution, modality, polarity, time, condition as supported. Do not emit group-wide modality/polarity or a qualifier implying all members share one scope.",
        "Use modality=uncertain for unknown identity/target/sound; reported/tentative/conditional/intended/inability/not_daring as applicable. Use polarity=negative for explicitly negated action, prohibition or non-existence. Not knowing is not negative. A simile does not make the underlying observed event tentative. Separate source quotes for affirmative waiting, prohibited crossing/outside, missing exterior light, bright interior, inability, and uncertain commander. Correct every inherited epistemic/polarity error and preserve nested reported speech attribution. Ambiguous source syntax stays ambiguous (modality=ambiguous), never silently resolved into a definite event.",
        "Emit one final topic item with qualifiers.role=inventory_accounting and qualifiers.omissions. Each omission has ONLY member_id, disposition, reason; disposition is decorative_only, redundant_node, or unsupported. Omit standalone decorative comparisons/noun nodes and courtesy text lacking navigation value. Equivalent substantive propositions stay as members of a single group, not omissions. Do not classify memory, grief, uncertainty, negation, actor identity or state change as decorative. reason must explain the source-grounded decision. If a substantive source claim is absent from inventory, put its exact source quote in qualifiers.missing_source_claims on this accounting item; this fails the recall gate and requires a fresh inventory revision, not a made-up member.",
        "Groups cite only the smallest sufficient ORIGINAL source segment IDs needed by their members and annotations. Never cite inventory IDs as source IDs. The accounting topic cites original segments being reviewed. segment_coverage retains the existing exact input accounting. No event/relation/mention output, no synthetic facts, global entity identity, invented spans or guessed causality. This is an opt-in navigation-view experiment, not production adoption or semantic acceptance.",
    ]
    prompt["minimal_example"] = {
        "warning": "Use actual input IDs; this shape is illustrative only.",
        "group": {"local_id": "g1", "kind": "topic", "label": "Waiting and departure constraints",
                  "source_segment_ids": ["s1"], "topic_path": [],
                  "qualifiers": {"role": "navigation_group", "group_kind": "event", "member_ids": ["e1", "e2"],
                                 "annotations": [{"member_ids": ["e2"], "source_quote": "Do not leave",
                                                  "qualifiers": {"modality": "reported", "polarity": "negative"}}]}},
        "accounting": {"local_id": "audit", "kind": "topic", "label": "Inventory review",
                       "source_segment_ids": ["s1"], "topic_path": [],
                       "qualifiers": {"role": "inventory_accounting", "omissions": [], "missing_source_claims": []}},
    }
    return prompt


def two_stage_curator_prompt_contract(version: str = TWO_STAGE_CURATOR_PROMPT_VERSION) -> dict[str, Any]:
    """Prompt for source-verified curation from a high-recall inventory."""

    prompt = dict(b_v5_prompt_contract())
    if version in {LEGACY_ATOMIC_INVENTORY_PROMPT_VERSION, OLDER_ATOMIC_INVENTORY_PROMPT_VERSION,
                   PREVIOUS_ATOMIC_INVENTORY_PROMPT_VERSION,
                   PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION,
                   V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                   V06_ATOMIC_INVENTORY_PROMPT_VERSION,
                   ATOMIC_INVENTORY_PROMPT_VERSION}:
        return atomic_inventory_prompt_contract(version)
    if version == GROUPED_NAVIGATION_PROMPT_VERSION:
        return grouped_navigation_prompt_contract()
    if version not in {"phase05-w2-two-stage-curator-prompt-0.2", TWO_STAGE_CURATOR_PROMPT_VERSION}:
        raise ValueError("unsupported two-stage curator prompt version")
    prompt["version"] = version
    prompt["task"] = (
        "You are the second stage of a source-bound semantic compiler. The supplied source stages "
        "are authoritative for content. The inventory is a recall-oriented intermediate aid, not "
        "a fact source. Verify each inventory proposition against the source text, retain every "
        "supported independent plot, state, goal, identity, location, relationship, attribution, "
        "uncertainty, negation, or state change, and then emit a compact navigation representation. "
        "Compact means merge equivalent or closely linked propositions into source-faithful event/fact "
        "records, remove redundant graph nodes and decorative-only records, and retain every supported "
        "substantive distinction. It does not permit dropping a plot, state, attribution, modality, "
        "polarity, temporal step, or causal distinction merely to reduce item count."
    )
    prompt["extraction_rules"] = [
        "Read the source stages completely before using the inventory as a checklist.",
        "Treat inventory items as candidate propositions only. If an inventory item is unsupported or conflicts with the source, omit it and record the source segment disposition; never repair it by inventing facts.",
        "Preserve every supported checklist proposition, but it need not have one output item per proposition: combine propositions expressed as coordinated parts of the same event or state into one concise label when actor, attribution, time, modality, and polarity are shared. Keep separate records when a proposition changes actor, event, state, temporal step, attribution, modality, polarity, or causal role.",
        "Merge repeated singing statements into one singing event unless a later clause adds a distinct state, time, or attribution. Preserve the linked grief, tears, target uncertainty, duration, and memory propositions in that event or a separate fact; do not drop them as poetic wording.",
        "Do not emit a standalone mention merely because it is named in the inventory. Emit mentions only when referenced by an event or relation, or for a core protagonist, location, or topic needed to navigate the record. Remove all unreferenced decorative, sensory, and incidental noun phrases.",
        "Prefer one event/fact item over an event plus a relation that repeats the same proposition. Emit a relation only when it adds a distinct traversal link not already stated by an event/fact and its endpoints have local references.",
        "Expressions including 不知、未知、未曾晓得、未曾见过、似乎、仿佛、或许、不一定、好像 and equivalent epistemic language express uncertainty/modality, not negative polarity. Negative polarity is only for a proposition explicitly denied, refused, prohibited, or stated not to exist; distinguish inability or reluctance from non-existence.",
        "Every emitted item must cite only the supplied source segment IDs. Segment coverage accounts for every original source segment and does not assert completeness.",
    ]
    prompt["source_binding_policy"] = STRICT_SOURCE_BINDING_POLICY
    prompt["intermediate_artifact_contract"] = {
        "kind": "high_recall_inventory",
        "authority": "candidate_only",
        "source_verification_required": True,
        "schema_identity": "phase05-w2-semantic-output-0.1",
    }
    if version == TWO_STAGE_CURATOR_PROMPT_VERSION:
        prompt["extraction_rules"] = [
            "Read every original source stage completely. The inventory is a fallible recall checklist: verify its claims, correct source-inconsistent interpretations, and recover supported substantive claims it missed. Never cite inventory items as evidence.",
            "Retain each independently stated substantive plot, state, goal, identity, relationship, location, memory, emotional state, or epistemic distinction. Express sensory or poetic wording as the underlying source-supported state or action; do not remove substantive content because it is written poetically. Omit purely ornamental comparisons, repeated refrains, incidental noun nodes, and courtesy closings with no independent navigation value.",
            "Compactness comes from removing equivalent propositions, redundant nodes, and ornamental wording. Each event/fact label describes one coherent action or state with concise source-faithful wording, not a copied paragraph or a catch-all list of everything one actor does. Closely coordinated construction details can share a record; different states, temporal steps, causes, speakers, or qualifier scopes cannot. Do not target an item count.",
            "Qualifiers apply to the entire proposition in their item. Split affirmative waiting from prohibited crossing/going outside; split absence of outside light from presence of inside light; split known crying from an unknown mourning target. Never assign global negative polarity or uncertainty to a record mixing positive, negative, and unknown clauses.",
            "Use qualifiers.attribution for source voice, qualifiers.modality for reported, uncertain, tentative, intended, conditional, inability, reluctance, or rhetorical content, and qualifiers.polarity=negative for an explicitly negated proposition. Put the negated predicate clearly in the label. Do not replace these fields with negation, epistemic, negated_states, or invented substitute fields. Additional time/condition fields may supplement, not replace, modality. Reported requests and plans do not assert completed events.",
            "Not knowing an identity, target, or sound (不知、未知、未曾晓得), and tentative expressions (似乎、或许、不一定), express uncertainty, not negative polarity. An explicit statement of not previously seeing/entering is a negated experience, not a denial of the place's existence. Inability or not daring is modality; a negated action may also have negative polarity but never implies non-existence. 好像 used as a simile does not make the underlying observed event uncertain.",
            "Preserve emotional, memory, duration, and state distinctions around music without repeating the singing action in every record. Use one performance record for playing/singing and its goal; separate grief, crying duration, uncertain mourning target, unplanned singing, memory state, and inability to overcome sorrow when their scope differs. Preserve actual state/temporal changes; repeated identical refrains are not new events.",
            "Source syntax may leave scope ambiguous: retain that ambiguity with modality and attribution instead of resolving it into a newly certain completed action. In particular, a goal, rhetorical question, or self-correction must not silently become an objective fact.",
            "Mention nodes are for concrete actors, objects, or locations used by an event or explicit relation, and independently central identities. Do not create a noun node for every sensory phrase. Preserve explicitly stated identity/name/location relationships as relation records when they are useful traversal links; make that relation the sole representation of its predicate rather than also restating it in an event/fact. Relations must use explicit source-supported predicates and local endpoints; never infer causality, global identity, inverse/transitive links, or generic related_to.",
            "Every event participant and relation endpoint must resolve to an appropriate local_id in this response. Equal labels across units are not shared identities. Cite only the smallest sufficient set of original source segment IDs; never bind to the inventory or add an image-only segment to a text claim.",
            "Before returning, compare each substantive source/inventory proposition to the result, then check for duplicate propositions, overlong multi-scope records, decorative-only records, and mis-scoped modality/polarity. Segment coverage accounts for every source segment and does not certify completeness. Unsupported/ambiguous/no-navigation segment dispositions retain the existing fail-closed contract.",
        ]
    return prompt


def build_curation_payload(source_payload: Mapping[str, Any], inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Attach an inventory side input while preserving the original segments."""

    segments = source_payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError("source segments are required")
    validate_semantic_output_envelope(
        inventory,
        expected_segment_ids=[segment["segment_id"] for segment in segments],
        segment_metadata=semantic_segment_binding_metadata(source_payload),
    )
    validate_b_v2_navigation_references(inventory)
    payload = deepcopy(dict(source_payload))
    payload["two_stage_context"] = {
        "revision": TWO_STAGE_REVISION,
        "inventory_role": "candidate_only",
        "inventory": deepcopy(dict(inventory)),
        "inventory_identity": sha256_json(inventory),
    }
    return payload


def build_two_stage_manifest(
    *,
    gate_manifest_path: Path,
    gate_manifest: Mapping[str, Any],
    inventory_artifacts: Sequence[Mapping[str, Any]],
    point1_only: bool = False,
    prompt_version: str = GROUPED_NAVIGATION_PROMPT_VERSION,
) -> dict[str, Any]:
    """Freeze source, candidate artifacts, prompt and config before live output."""
    verified_gate, units = frozen_v5_gate_units(gate_manifest_path)
    if verified_gate != gate_manifest:
        raise ValueError("gate manifest changed")
    if point1_only:
        units = units[:1]
    if len(inventory_artifacts) != len(units):
        raise ValueError("inventory count differs from selected frozen units")
    inventory_contract = b_v4_experiment_contract()
    curator = two_stage_curator_prompt_contract(prompt_version)
    rows = []
    for unit, artifact in zip(units, inventory_artifacts):
        path = Path(artifact["path"])
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != artifact["sha256"]:
            raise ValueError("inventory artifact hash mismatch")
        payload = build_curation_payload(unit["payload"], json.loads(raw))
        # Verify the inventory's recorded producer and exact source input.
        request = json.loads((path.parent / "request.json").read_bytes())
        if (json.loads(request["messages"][0]["content"]) != inventory_contract.prompt_contract
                or json.loads(request["messages"][1]["content"]) != unit["payload"]):
            raise ValueError("inventory producer prompt/source mismatch")
        rows.append({
            "compilation_unit_id": unit["compilation_unit_id"],
            "original_semantic_input_identity": unit["semantic_input_identity"],
            "semantic_input_identity": semantic_input_identity(payload),
            "payload_sha256": sha256(canonical_json_bytes(payload)).hexdigest(),
            "payload": payload,
            "segment_ids": unit["segment_ids"],
            "inventory_artifact": dict(artifact),
            "inventory_request_sha256": sha256((path.parent / "request.json").read_bytes()).hexdigest(),
        })
    contract: dict[str, Any] = {
        "schema_version": "phase05-w2-two-stage-gate-manifest-0.1",
        "revision": TWO_STAGE_REVISION,
        "gate_manifest_path": str(gate_manifest_path).replace("\\", "/"),
        "gate_manifest_identity": gate_manifest["identity"],
        "source_binding_policy": STRICT_SOURCE_BINDING_POLICY,
        "stage_a": {
            "role": "high_recall_inventory",
            "experiment_revision": B_V4_EXPERIMENT_REVISION,
            "experiment_identity": inventory_contract.identity,
            "prompt_identity": inventory_contract.prompt_identity,
            "authority": "candidate_only",
            "outputs": [row["inventory_artifact"] for row in rows],
        },
        "stage_b": {
            "role": "source_verified_compact_navigation",
            "prompt_identity": sha256_json(curator),
            "prompt": curator,
            "units": rows,
        },
        "operating_point": gate_manifest["contract"]["operating_point"],
        "point1_only": point1_only,
    }
    return {"identity": sha256_json(contract), "contract": contract}


def build_atomic_inventory_manifest(
    *, gate_manifest_path: Path, gate_manifest: Mapping[str, Any],
    unit_indexes: Sequence[int] = (0, 1, 2, 3),
) -> dict[str, Any]:
    """Freeze an opt-in atomic Stage A request over existing gate units."""
    verified_gate, units = frozen_v5_gate_units(gate_manifest_path)
    if verified_gate != gate_manifest:
        raise ValueError("gate manifest changed")
    selected = [units[index] for index in unit_indexes]
    prompt = atomic_inventory_prompt_contract()
    source_identity = gate_manifest["contract"].get("source_identity")
    if not isinstance(source_identity, str) or not source_identity:
        raise ValueError("v5 gate source identity is missing")
    rows = []
    for unit in selected:
        messages = [
            {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(unit["payload"]).decode("utf-8")},
        ]
        operating = gate_manifest["contract"]["operating_point"]
        logical_body = {"model": operating["model"], "messages": messages,
                        "max_tokens": operating["max_tokens"], "stream": True,
                        **operating["generation_parameters"]}
        primary_config = operating.get("primary_route_config", {"api_surface": "chat_completions"})
        fallback_config = operating.get("fallback_route_config", {"api_surface": "chat_completions"})
        body = _wire_body_for_surface(logical_body, primary_config["api_surface"])
        fallback_body = _wire_body_for_surface(logical_body, fallback_config["api_surface"])
        request_identity = sha256_json({
            "source_identity": source_identity, "prompt_identity": sha256_json(prompt),
            "schema_identity": "phase05-w2-semantic-output-0.1",
            "compilation_unit_id": unit["compilation_unit_id"],
            "semantic_input_identity": unit["semantic_input_identity"],
            "model": logical_body["model"], "messages": messages, "payload": unit["payload"],
            "stream": True, "max_tokens": logical_body["max_tokens"],
            "generation_parameters": operating["generation_parameters"],
        })
        rows.append({"compilation_unit_id": unit["compilation_unit_id"],
                     "semantic_input_identity": unit["semantic_input_identity"],
                     "payload": unit["payload"], "segment_ids": unit["segment_ids"],
                     "request_identity": request_identity,
                     "wire_request_identity": sha256_json(body),
                     "route_wire_request_identities": {
                         "tokenmetro": sha256_json(body), "jizhi": sha256_json(fallback_body),
                     }})
    contract = {
        "schema_version": "phase05-w2-atomic-inventory-manifest-0.1",
        "revision": TWO_STAGE_REVISION,
        "gate_manifest_path": str(gate_manifest_path).replace("\\", "/"),
        "gate_manifest_identity": gate_manifest["identity"],
        "source_identity": source_identity,
        "prompt_version": ATOMIC_INVENTORY_PROMPT_VERSION,
        "prompt": prompt, "prompt_identity": sha256_json(prompt),
        "schema_identity": "phase05-w2-semantic-output-0.1",
        "operating_point": gate_manifest["contract"]["operating_point"],
        "units": rows,
    }
    return {"identity": sha256_json(contract), "contract": contract}


def build_historical_atomic_inventory_manifest(
    *, historical_run_root: Path, current_source_manifest_path: Path,
    gate_manifest_path: Path, gate_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the unchanged historical 200-unit payloads under the current Prompt."""
    historical_manifest_path = historical_run_root / "manifest.json"
    historical_manifest = json.loads(historical_manifest_path.read_bytes())
    historical_contract = historical_manifest["contract"]
    expected_source = gate_manifest["contract"].get("source_identity")
    if historical_contract.get("source_identity") != expected_source:
        raise ValueError("historical source identity differs from current semantic build")
    source_manifest = json.loads(current_source_manifest_path.read_bytes())
    if not isinstance(source_manifest.get("semantic_build_identity"), str):
        raise ValueError("current source manifest semantic build identity is missing")
    request_rows = []
    for old_unit in historical_contract.get("units", []):
        old_key = sha256(old_unit["unit_id"].encode()).hexdigest()
        request_path = historical_run_root / "units" / old_key / "attempt-001" / "request.json"
        if not request_path.is_file():
            raise ValueError(f"historical request missing: {old_unit['unit_id']}")
        request = json.loads(request_path.read_bytes())
        payload = json.loads(next(message["content"] for message in request["messages"]
                                  if message["role"] == "user"))
        sid = semantic_input_identity(payload)
        if sid != old_unit.get("semantic_input_identity"):
            raise ValueError(f"historical semantic input mismatch: {old_unit['unit_id']}")
        request_rows.append((old_unit, request_path, payload, sid))
    target_ids = {row[3] for row in request_rows}
    current_units: dict[str, Mapping[str, Any]] = {}
    units_path = current_source_manifest_path.parent / "compilation_units.jsonl.gz"
    sidecars_path = current_source_manifest_path.parent / "projection_sidecar.jsonl.gz"
    sidecars: dict[str, Mapping[str, Any]] = {}
    with gzip.open(sidecars_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            sidecar = json.loads(line)
            if isinstance(sidecar, Mapping) and isinstance(sidecar.get("segment_id"), str):
                sidecars[sidecar["segment_id"]] = sidecar
    with gzip.open(units_path, "rt", encoding="utf-8") as stream:
        for line in stream:
            unit = json.loads(line)
            if unit.get("semantic_input_identity") in target_ids:
                current_units[unit["semantic_input_identity"]] = unit

    # A projection revision intentionally changes semantic_input_identity.  Map
    # the frozen historical members by their stable source segment IDs and
    # record/title, while refusing ambiguous or missing matches.  This keeps
    # the exact 200 logical members and their original historical identities.
    if len(current_units) != len(target_ids):
        current_rows: list[Mapping[str, Any]] = []
        with gzip.open(units_path, "rt", encoding="utf-8") as stream:
            for line in stream:
                current_rows.append(json.loads(line))
        visible_segments: dict[str, Mapping[str, Any]] = {}
        for row in current_rows:
            for segment in row.get("provider_payload", {}).get("segments", []):
                if isinstance(segment, Mapping) and isinstance(segment.get("segment_id"), str):
                    visible_segments[segment["segment_id"]] = segment
        current_units = {}
        for old_unit, _request_path, old_payload, old_sid in request_rows:
            selected: list[Mapping[str, Any]] = []
            selected_ids: set[str] = set()
            omitted = 0
            for old_segment in old_payload.get("segments", []):
                old_segment_id = str(old_segment.get("segment_id"))
                candidates = [
                    (segment_id, segment) for segment_id, segment in visible_segments.items()
                    if segment_id == old_segment_id or segment_id.startswith(old_segment_id + ":p")
                ]
                if not candidates:
                    sidecar = sidecars.get(old_segment_id)
                    if sidecar is not None and sidecar.get("provider_visible") is False:
                        omitted += 1
                        continue
                    raise ValueError(f"historical sample source remap is not unique: {old_unit.get('unit_id')}")
                for segment_id, segment in sorted(candidates, key=lambda pair: pair[0]):
                    if segment_id not in selected_ids:
                        selected_ids.add(segment_id)
                        selected.append(segment)
            selected.sort(key=lambda segment: (old_payload.get("segments", []).index(next(
                item for item in old_payload.get("segments", [])
                if str(item.get("segment_id")) == str(segment.get("segment_id")).split(":p", 1)[0]
            )) if any(str(item.get("segment_id")) == str(segment.get("segment_id")).split(":p", 1)[0] for item in old_payload.get("segments", [])) else 10**9, str(segment.get("segment_id"))))
            payload = {
                "schema_version": old_payload.get("schema_version"),
                "record_key": old_payload.get("record_key"),
                "title": old_payload.get("title"),
                "segments": [dict(segment) for segment in selected],
                "omission_summary": {
                    "count": omitted,
                    "reasons": {"pure_media_map_desc": omitted} if omitted else {},
                    "partial_input": False,
                },
            }
            current_units[old_sid] = {
                "compilation_unit_id": old_unit.get("unit_id"),
                "record_id": old_unit.get("record_id"),
                "semantic_input_identity": semantic_input_identity(payload),
                "segment_ids": [str(segment.get("segment_id")) for segment in selected],
                "accounted_segment_ids": list(old_unit.get("segment_ids", [])),
                "provider_payload": payload,
            }
    prompt = atomic_inventory_prompt_contract()
    operating = gate_manifest["contract"]["operating_point"]
    rows = []
    for old_unit, request_path, payload, sid in request_rows:
        current = current_units[sid]
        if set(current.get("accounted_segment_ids", current["segment_ids"])) != set(old_unit.get("segment_ids", [])):
            raise ValueError(f"historical segment binding mismatch: {old_unit['unit_id']}")
        new_payload = current["provider_payload"]
        new_sid = current["semantic_input_identity"]
        messages = [
            {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(new_payload).decode("utf-8")},
        ]
        logical_body = {"model": operating["model"], "messages": messages,
                        "max_tokens": operating["max_tokens"], "stream": True,
                        **operating["generation_parameters"]}
        primary_config = operating.get("primary_route_config", {"api_surface": "chat_completions"})
        fallback_config = operating.get("fallback_route_config", {"api_surface": "chat_completions"})
        body = _wire_body_for_surface(logical_body, primary_config["api_surface"])
        fallback_body = _wire_body_for_surface(logical_body, fallback_config["api_surface"])
        request_identity = sha256_json({
            "source_identity": source_manifest["semantic_build_identity"], "prompt_identity": sha256_json(prompt),
            "schema_identity": "phase05-w2-semantic-output-0.1",
            "compilation_unit_id": current["compilation_unit_id"],
            "semantic_input_identity": new_sid, "model": logical_body["model"], "messages": messages,
            "payload": new_payload, "stream": True, "max_tokens": logical_body["max_tokens"],
            "generation_parameters": operating["generation_parameters"],
        })
        rows.append({
            "compilation_unit_id": current["compilation_unit_id"],
            "semantic_input_identity": new_sid,
            "payload": new_payload,
            "segment_ids": current["segment_ids"],
            "accounted_segment_ids": current.get("accounted_segment_ids", current["segment_ids"]),
            "request_identity": request_identity,
            "wire_request_identity": sha256_json(body),
            "route_wire_request_identities": {
                "tokenmetro": sha256_json(body), "jizhi": sha256_json(fallback_body),
            },
            "historical_unit_id": old_unit["unit_id"],
            "historical_request_sha256": sha256(request_path.read_bytes()).hexdigest(),
            "provider_omitted": not bool(current["segment_ids"])
                and current["provider_payload"].get("omission_summary", {}).get("reasons", {}).get("pure_media_map_desc", 0) > 0,
        })
    contract = {
        "schema_version": "phase05-w2-atomic-inventory-manifest-0.2",
        "revision": TWO_STAGE_REVISION,
        "unit_source": "historical_scoped_semantic_run",
        "gate_manifest_path": str(gate_manifest_path).replace("\\", "/"),
        "gate_manifest_identity": gate_manifest["identity"],
        "historical_run_root": str(historical_run_root).replace("\\", "/"),
        "historical_run_manifest_sha256": sha256(historical_manifest_path.read_bytes()).hexdigest(),
        "historical_run_manifest_identity": historical_manifest["identity"],
        "historical_sample_identity": "72e1ec582cfb98c06c4ff15d74c2abe5f50d8c7f7e53381d3d224b5d7434577a",
        "current_source_manifest_path": str(current_source_manifest_path).replace("\\", "/"),
        "current_source_manifest_sha256": sha256(current_source_manifest_path.read_bytes()).hexdigest(),
        "source_identity": source_manifest["semantic_build_identity"],
        "source_revision": {
            "kind": "provider_projection_revision",
            "removed_provider_media": "pure_media_map_desc",
            "previous_source_identity": expected_source,
        },
        "prompt_version": ATOMIC_INVENTORY_PROMPT_VERSION,
        "prompt": prompt,
        "prompt_identity": sha256_json(prompt),
        "schema_identity": "phase05-w2-semantic-output-0.1",
        "operating_point": operating,
        "units": rows,
    }
    return {"identity": sha256_json(contract), "identity_contract": "sha256_json(contract)", "contract": contract}


def load_two_stage_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_bytes())
    contract = manifest["contract"]
    gate_path = Path(contract["gate_manifest_path"])
    rebuilt = build_two_stage_manifest(
        gate_manifest_path=gate_path,
        gate_manifest=json.loads(gate_path.read_bytes()),
        inventory_artifacts=contract["stage_a"]["outputs"],
        point1_only=contract["point1_only"],
        prompt_version=contract["stage_b"]["prompt"]["version"],
    )
    if manifest != rebuilt:
        raise ValueError("two-stage freeze/source/prompt/config mismatch")
    return manifest


class _TextProjection(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _source_texts(payload: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for segment in payload["segments"]:
        parts: list[str] = []
        def visit(value):
            if isinstance(value, Mapping):
                for key, child in value.items():
                    if key in {"text", "rich_text", "dialogue", "speaker", "option"} and isinstance(child, str):
                        if key == "rich_text":
                            # The supplied Canonical value also retains the literal HTML field.
                            # Accept an exact raw-source quote as well as its text projection.
                            parts.append(child)
                        parser = _TextProjection()
                        parser.feed(child)
                        parts.append("".join(parser.parts))
                    elif isinstance(child, (Mapping, list)):
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(segment)
        result[segment["segment_id"]] = "\n".join(parts)
    return result


def validate_atomic_inventory(output: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Provider-free source-quote and qualifier-scope contract for Stage A."""
    validate_semantic_output_envelope(output, expected_segment_ids=[s["segment_id"] for s in payload["segments"]],
                                      segment_metadata=semantic_segment_binding_metadata(payload))
    validate_b_v2_navigation_references(output)
    texts = _source_texts(payload)
    allowed = {"source_quote", "attribution", "modality", "polarity", "time", "condition"}
    seen_quotes: set[str] = set()
    for item in output["items"]:
        qualifiers = item["qualifiers"]
        quote = qualifiers.get("source_quote")
        if not isinstance(quote, str) or not quote:
            raise ValueError(f"atomic item {item['local_id']} lacks source_quote")
        if not set(qualifiers).issubset(allowed):
            raise ValueError(f"atomic item {item['local_id']} uses unsupported qualifier field")
        # Short exact phrases can occur at multiple source positions (for example a repeated refrain).
        # The schema has no occurrence-span ID, so quote presence is validated here and duplicate text
        # is retained for semantic review instead of being treated as a mechanical error.
        seen_quotes.add(quote)
        refs = item["source_segment_ids"]
        if not any(quote in texts[segment_id] for segment_id in refs):
            raise ValueError(f"atomic item {item['local_id']} source_quote is not in source")
    return {"status": "PASS", "semantic_acceptance": "UNASSESSED", "item_count": len(output["items"]),
            "quoted_items": len(seen_quotes),
            "duplicate_quote_items": len(output["items"]) - len(seen_quotes),
            "provider_calls": 0, "network_calls": 0}


def load_atomic_inventory_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_bytes())
    contract = manifest.get("contract")
    if not isinstance(contract, Mapping) or manifest.get("identity") != sha256_json(contract):
        raise ValueError("atomic inventory manifest identity mismatch")
    if contract.get("prompt_version") not in {LEGACY_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              OLDER_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              PREVIOUS_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              PRIOR_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              V05_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              V06_ATOMIC_INVENTORY_PROMPT_VERSION,
                                              ATOMIC_INVENTORY_PROMPT_VERSION}:
        raise ValueError("atomic inventory prompt identity mismatch")
    prompt = atomic_inventory_prompt_contract(contract["prompt_version"])
    if contract.get("prompt_identity") != sha256_json(prompt) or contract.get("prompt") != prompt:
        raise ValueError("atomic inventory prompt changed")
    operating = contract.get("operating_point")
    if not isinstance(operating, Mapping):
        raise ValueError("atomic inventory operating point is missing")
    for route, key in (("tokenmetro", "primary_route_config"), ("jizhi", "fallback_route_config")):
        config = operating.get(key)
        if not isinstance(config, Mapping) or config.get("route") != route:
            raise ValueError("atomic inventory route configuration is invalid")
        if config.get("api_surface") not in {"responses", "chat_completions"}:
            raise ValueError("atomic inventory route API surface is invalid")
    gate = json.loads(Path(contract["gate_manifest_path"]).read_bytes())
    if gate["identity"] != contract.get("gate_manifest_identity"):
        raise ValueError("atomic inventory gate binding mismatch")
    if contract.get("unit_source", "v5_gate") == "historical_scoped_semantic_run":
        source_path = Path(contract["current_source_manifest_path"])
        if sha256(source_path.read_bytes()).hexdigest() != contract.get("current_source_manifest_sha256"):
            raise ValueError("historical source manifest changed")
        source_manifest = json.loads(source_path.read_bytes())
        if source_manifest.get("semantic_build_identity") != contract.get("source_identity"):
            raise ValueError("historical source build identity mismatch")
        current_path = source_path.parent / "compilation_units.jsonl.gz"
        if isinstance(contract.get("source_revision"), Mapping):
            sidecar_path = source_path.parent / "projection_sidecar.jsonl.gz"
            available = set()
            with gzip.open(sidecar_path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    if isinstance(row, Mapping) and isinstance(row.get("segment_id"), str):
                        available.add(row["segment_id"])
            for row in contract.get("units", []):
                if semantic_input_identity(row.get("payload", {})) != row.get("semantic_input_identity"):
                    raise ValueError("historical revised atomic inventory payload identity mismatch")
                if not set(row.get("segment_ids", [])).issubset(available):
                    raise ValueError("historical revised atomic inventory source segment missing")
            # Continue below so route/API-surface wire identities are audited
            # for revised historical manifests as well.
        else:
            target_ids = {row.get("semantic_input_identity") for row in contract.get("units", [])}
            indexed: dict[str, Mapping[str, Any]] = {}
            with gzip.open(current_path, "rt", encoding="utf-8") as stream:
                for line in stream:
                    source = json.loads(line)
                    if source.get("semantic_input_identity") in target_ids:
                        indexed[source["semantic_input_identity"]] = source
                        if len(indexed) == len(target_ids):
                            break
            if len(indexed) != len(target_ids):
                raise ValueError("historical source units missing")
            for row in contract.get("units", []):
                source = indexed.get(row.get("semantic_input_identity"))
                if (source is None or source["compilation_unit_id"] != row.get("compilation_unit_id")
                        or source["provider_payload"] != row.get("payload")
                        or source["segment_ids"] != row.get("segment_ids")
                        or source.get("accounted_segment_ids", source["segment_ids"]) != row.get("accounted_segment_ids", row.get("segment_ids"))):
                    raise ValueError("historical atomic inventory source binding mismatch")
                expected_omitted = (not bool(source["segment_ids"])
                                    and source["provider_payload"].get("omission_summary", {}).get("reasons", {}).get("pure_media_map_desc", 0) > 0)
                if bool(row.get("provider_omitted")) != expected_omitted:
                    raise ValueError("historical atomic inventory omission accounting mismatch")
    else:
        verified_gate, frozen = frozen_v5_gate_units(Path(contract["gate_manifest_path"]))
        if verified_gate != gate:
            raise ValueError("atomic inventory gate binding mismatch")
        indexed = {unit["compilation_unit_id"]: unit for unit in frozen}
        for row in contract.get("units", []):
            source = indexed.get(row.get("compilation_unit_id"))
            if source is None or source["payload"] != row.get("payload") or source["segment_ids"] != row.get("segment_ids"):
                raise ValueError("atomic inventory source binding mismatch")
    # The persisted wire identity must agree with the persisted API surface.
    # This prevents a nominal Responses manifest from silently reusing a Chat
    # Completions body when it reaches the SDK runner.
    for row in contract.get("units", []):
        messages = [
            {"role": "system", "content": canonical_json_bytes(prompt).decode("utf-8")},
            {"role": "user", "content": canonical_json_bytes(row["payload"]).decode("utf-8")},
        ]
        logical = {"model": operating["model"], "messages": messages,
                   "max_tokens": operating["max_tokens"], "stream": True,
                   **operating["generation_parameters"]}
        expected = {
            "tokenmetro": sha256_json(_wire_body_for_surface(logical, operating["primary_route_config"]["api_surface"])),
            "jizhi": sha256_json(_wire_body_for_surface(logical, operating["fallback_route_config"]["api_surface"])),
        }
        declared = row.get("route_wire_request_identities")
        if declared is not None and declared != expected:
            raise ValueError("atomic inventory route wire identity mismatch")
        if row.get("wire_request_identity") != expected["tokenmetro"]:
            raise ValueError("atomic inventory primary wire identity mismatch")
    return manifest


def validate_grouped_navigation(output: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Verify loss accounting and quotes; this is not a semantic judge."""
    validate_semantic_output_envelope(output, expected_segment_ids=[s["segment_id"] for s in payload["segments"]],
                                      segment_metadata=semantic_segment_binding_metadata(payload))
    inventory = payload["two_stage_context"]["inventory"]
    candidates = {item["local_id"]: item for item in inventory["items"]}
    texts = _source_texts(payload)
    accounted: set[str] = set()
    groups = []
    omissions = None
    annotations_count = 0
    missing = []
    for item in output["items"]:
        qualifiers = item["qualifiers"]
        role = qualifiers.get("role")
        if item["kind"] != "topic" or any(item.get(key) for key in ("participants", "subject_ref", "object_ref", "predicate", "event_type")):
            raise ValueError("grouped view contains an unsupported assertion/reference")
        if role == "navigation_group":
            if set(qualifiers) != {"role", "group_kind", "member_ids", "annotations"}:
                raise ValueError("group-wide qualifiers are forbidden")
            if qualifiers["group_kind"] not in {"event", "state", "identity", "location", "topic"}:
                raise ValueError("invalid navigation group kind")
            members = qualifiers["member_ids"]
            if not isinstance(members, list) or not members or len(set(members)) != len(members):
                raise ValueError("navigation members must be nonempty and unique")
            if not set(members).issubset(candidates) or accounted.intersection(members):
                raise ValueError("unknown or multiply accounted inventory member")
            accounted.update(members)
            expected_sources = {ref for member in members for ref in candidates[member]["source_segment_ids"]}
            if set(item["source_segment_ids"]) != expected_sources:
                raise ValueError("group source binding differs from inventory lineage")
            if not isinstance(qualifiers["annotations"], list):
                raise ValueError("group annotations must be a list")
            for annotation in qualifiers["annotations"]:
                if set(annotation) != {"member_ids", "source_quote", "qualifiers"}:
                    raise ValueError("invalid scoped annotation fields")
                refs = annotation["member_ids"]
                quote = annotation["source_quote"]
                if (not isinstance(refs, list) or not refs or not set(refs).issubset(members)
                        or not isinstance(quote, str) or not quote
                        or not isinstance(annotation["qualifiers"], Mapping)
                        or not set(annotation["qualifiers"]).issubset({"attribution", "modality", "polarity", "time", "condition"})):
                    raise ValueError("invalid scoped annotation/reference")
                sources = {ref for member in refs for ref in candidates[member]["source_segment_ids"]}
                if not any(quote in texts[source] for source in sources):
                    raise ValueError("annotation quote is not present in original source")
                annotations_count += 1
            groups.append({"local_id": item["local_id"], "label": item["label"], "member_ids": members,
                           "source_segment_ids": item["source_segment_ids"]})
        elif role == "inventory_accounting":
            if omissions is not None or set(qualifiers) != {"role", "omissions", "missing_source_claims"}:
                raise ValueError("exactly one inventory accounting item is required")
            omissions = qualifiers["omissions"]
            missing = qualifiers["missing_source_claims"]
            if not isinstance(omissions, list) or not isinstance(missing, list):
                raise ValueError("invalid inventory accounting lists")
        else:
            raise ValueError("unsupported grouped view role")
    if omissions is None:
        raise ValueError("missing inventory accounting")
    for omission in omissions:
        if (set(omission) != {"member_id", "disposition", "reason"}
                or omission["disposition"] not in {"decorative_only", "redundant_node", "unsupported"}
                or not isinstance(omission["reason"], str) or not omission["reason"]
                or omission["member_id"] not in candidates or omission["member_id"] in accounted):
            raise ValueError("invalid or duplicate inventory omission")
        accounted.add(omission["member_id"])
    if accounted != set(candidates):
        raise ValueError("inventory accounting is incomplete")
    if missing:
        raise ValueError("source claims missing from inventory; recall gate blocked")
    return {"status": "PASS", "semantic_acceptance": "UNASSESSED", "inventory_items": len(candidates),
            "navigation_groups": len(groups), "omitted_items": len(omissions), "scoped_annotations": annotations_count,
            "groups": groups, "omissions": omissions, "provider_calls": 0, "network_calls": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--gate-manifest", type=Path, required=True)
    prepare.add_argument("--inventory-root", type=Path, required=True)
    prepare.add_argument("--historical-point1", type=Path)
    prepare.add_argument("--point1-only", action="store_true")
    prepare.add_argument("--prompt-version", default=GROUPED_NAVIGATION_PROMPT_VERSION)
    prepare.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--output-root", type=Path, required=True)
    atomic_prepare = commands.add_parser("atomic-prepare")
    atomic_prepare.add_argument("--gate-manifest", type=Path, required=True)
    atomic_prepare.add_argument("--indexes", default="0,1,2,3")
    atomic_prepare.add_argument("--output", type=Path, required=True)
    historical_prepare = commands.add_parser("historical-atomic-prepare")
    historical_prepare.add_argument("--historical-run-root", type=Path, required=True)
    historical_prepare.add_argument("--current-source-manifest", type=Path, required=True)
    historical_prepare.add_argument("--gate-manifest", type=Path, required=True)
    historical_prepare.add_argument("--output", type=Path, required=True)
    atomic_run = commands.add_parser("atomic-run")
    atomic_run.add_argument("--manifest", type=Path, required=True)
    atomic_run.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "atomic-prepare":
        gate = json.loads(args.gate_manifest.read_bytes())
        indexes = tuple(int(value) for value in args.indexes.split(",") if value.strip())
        manifest = build_atomic_inventory_manifest(
            gate_manifest_path=args.gate_manifest, gate_manifest=gate, unit_indexes=indexes)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(canonical_json_bytes(manifest))
        print(json.dumps({"identity": manifest["identity"], "units": len(indexes),
                          "provider_calls": 0, "network_calls": 0}))
    elif args.command == "historical-atomic-prepare":
        gate = json.loads(args.gate_manifest.read_bytes())
        manifest = build_historical_atomic_inventory_manifest(
            historical_run_root=args.historical_run_root,
            current_source_manifest_path=args.current_source_manifest,
            gate_manifest_path=args.gate_manifest,
            gate_manifest=gate,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(canonical_json_bytes(manifest))
        print(json.dumps({"identity": manifest["identity"], "units": len(manifest["contract"]["units"]),
                          "provider_calls": 0, "network_calls": 0}))
    elif args.command == "atomic-run":
        if args.output_root.exists():
            raise ValueError("atomic output root already exists; no repeat invocation")
        manifest = load_atomic_inventory_manifest(args.manifest)
        contract = manifest["contract"]
        operating = contract["operating_point"]
        for route, key in (("tokenmetro", "primary_route_config"), ("jizhi", "fallback_route_config")):
            if route_profile(route, os.environ, require_environment=True).safe_dict() != operating[key]:
                raise ValueError("runtime route differs from frozen atomic operating point")
        result = run_sdk_route_pair(
            args.output_root, units=contract["units"], prompt=contract["prompt"],
            prompt_identity=contract["prompt_identity"], source_identity=contract["source_identity"],
            model=operating["model"], primary_route=operating["primary_route"],
            fallback_route=operating["fallback_route"], max_tokens=operating["max_tokens"],
            timeout_seconds=operating["timeout_seconds"], generation_parameters=operating["generation_parameters"],
        )
        print(json.dumps(result))
        if result["status"] == "complete":
            reviews = []
            for unit in contract["units"]:
                if unit.get("provider_omitted"):
                    reviews.append({
                        "status": "PASS",
                        "disposition": "provider_omitted",
                        "reason": "pure_media_map_desc",
                        "provider_calls": 0,
                        "network_calls": 0,
                    })
                    continue
                key = sha256(unit["compilation_unit_id"].encode()).hexdigest()
                output = json.loads((args.output_root / "units" / key / "attempt-001" / "canonical_output.json").read_bytes())
                reviews.append(validate_atomic_inventory(output, unit["payload"]))
            with (args.output_root / "atomic_contract_review.json").open("xb") as handle:
                handle.write(canonical_json_bytes({"manifest_identity": manifest["identity"], "reviews": reviews}))
            print(json.dumps({"atomic_contract": "PASS", "units": len(reviews), "provider_calls": 0, "network_calls": 0}))
    elif args.command == "prepare":
        gate, units = frozen_v5_gate_units(args.gate_manifest)
        if args.point1_only:
            units = units[:1]
        artifacts = []
        for index, unit in enumerate(units):
            key = sha256(unit["compilation_unit_id"].encode()).hexdigest()
            path = args.inventory_root / "units" / key / "attempt-001" / "canonical_output.json"
            if index == 0 and args.historical_point1:
                path = args.historical_point1
            artifacts.append({"path": path.as_posix(), "sha256": sha256(path.read_bytes()).hexdigest()})
        manifest = build_two_stage_manifest(
            gate_manifest_path=args.gate_manifest, gate_manifest=gate,
            inventory_artifacts=artifacts, point1_only=args.point1_only, prompt_version=args.prompt_version,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(canonical_json_bytes(manifest))
        print(json.dumps({"identity": manifest["identity"], "units": len(units),
                          "provider_calls": 0, "network_calls": 0}))
    else:
        if args.output_root.exists():
            raise ValueError("live output root already exists; no repeat invocation")
        manifest = load_two_stage_manifest(args.manifest)
        contract = manifest["contract"]
        operating = contract["operating_point"]
        for route, key in (("tokenmetro", "primary_route_config"), ("jizhi", "fallback_route_config")):
            if route_profile(route, os.environ, require_environment=True).safe_dict() != operating[key]:
                raise ValueError("runtime route differs from the frozen operating point")
        result = run_sdk_route_pair(
            args.output_root, units=contract["stage_b"]["units"],
            prompt=contract["stage_b"]["prompt"], prompt_identity=contract["stage_b"]["prompt_identity"],
            source_identity=manifest["identity"], model=operating["model"],
            primary_route=operating["primary_route"], fallback_route=operating["fallback_route"],
            max_tokens=operating["max_tokens"], timeout_seconds=operating["timeout_seconds"],
            generation_parameters=operating["generation_parameters"],
        )
        print(json.dumps(result))
        if contract["stage_b"]["prompt"]["version"] == GROUPED_NAVIGATION_PROMPT_VERSION and result["status"] == "complete":
            reviews = []
            for unit in contract["stage_b"]["units"]:
                key = sha256(unit["compilation_unit_id"].encode()).hexdigest()
                output = json.loads((args.output_root / "units" / key / "attempt-001" / "canonical_output.json").read_bytes())
                reviews.append(validate_grouped_navigation(output, unit["payload"]))
            with (args.output_root / "grouped_contract_review.json").open("xb") as handle:
                handle.write(canonical_json_bytes({"manifest_identity": manifest["identity"], "reviews": reviews}))
            print(json.dumps({"grouped_contract": "PASS", "units": len(reviews), "provider_calls": 0, "network_calls": 0}))


__all__ = [
    "TWO_STAGE_CURATOR_PROMPT_VERSION",
    "ATOMIC_INVENTORY_PROMPT_VERSION",
    "TWO_STAGE_REVISION",
    "build_curation_payload",
    "build_two_stage_manifest",
    "load_two_stage_manifest",
    "load_atomic_inventory_manifest",
    "build_atomic_inventory_manifest",
    "build_historical_atomic_inventory_manifest",
    "atomic_inventory_prompt_contract",
    "validate_atomic_inventory",
    "grouped_navigation_prompt_contract",
    "validate_grouped_navigation",
    "two_stage_curator_prompt_contract",
]


if __name__ == "__main__":
    main()
