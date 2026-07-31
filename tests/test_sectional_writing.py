import copy
from pathlib import Path

import pytest

from seo_ops.services.sectional_writing import (
    ContractValidationError,
    build_contract_bundle,
    load_contract_bundle,
    persist_contract_bundle,
    stable_section_id,
    validate_contract_bundle,
    validate_section_link_contracts,
)


def _bundle():
    return build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Help contractors choose and use a suitable pointing tool.",
        outline=[
            "Why Ceiling Layout Needs Clear Pointing",
            "Compare Red vs Green Visibility",
            "Choosing a Tool for the Work Area",
            "Safety and Compliance Limits",
            "How to Use the Tool on Site",
        ],
        guidance="Keep recommendations evidence-bound.",
    )


def test_contract_bundle_is_stable_and_aligned():
    first = _bundle()
    second = _bundle()

    assert first == second
    blueprint = first["article_blueprint"]
    sections = first["section_contracts"]
    links = first["section_link_contracts"]
    assert blueprint["section_order"] == sections["section_order"] == links["section_order"]
    assert blueprint["section_order"][0] == stable_section_id(
        1, "Why Ceiling Layout Needs Clear Pointing"
    )
    assert len(set(blueprint["section_order"])) == 5


def test_section_ids_survive_outline_reordering():
    first = _bundle()["article_blueprint"]["sections"]
    reordered = build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Help contractors choose and use a suitable pointing tool.",
        outline=[
            "Safety and Compliance Limits",
            "Why Ceiling Layout Needs Clear Pointing",
            "Compare Red vs Green Visibility",
            "Choosing a Tool for the Work Area",
            "How to Use the Tool on Site",
        ],
    )["article_blueprint"]["sections"]

    first_ids = {section["heading"]: section["section_id"] for section in first}
    reordered_ids = {section["heading"]: section["section_id"] for section in reordered}
    assert first_ids == reordered_ids


def test_reader_stages_control_initial_product_link_policy():
    bundle = _bundle()
    sections = bundle["section_contracts"]["sections"]
    links = bundle["section_link_contracts"]["sections"]

    by_heading = {section["heading"]: section for section in sections}
    link_by_id = {section["section_id"]: section for section in links}

    choosing = by_heading["Choosing a Tool for the Work Area"]
    assert choosing["reader_stage"] == "select"
    assert choosing["product_link_allowed"] is True
    choosing_gate = link_by_id[choosing["section_id"]]["product_links"]
    assert choosing_gate["opportunity_state"] == "unassessed"
    assert choosing_gate["min_required"] is None

    comparison = by_heading["Compare Red vs Green Visibility"]
    assert comparison["reader_stage"] == "compare"
    assert comparison["product_link_allowed"] is True
    assert link_by_id[comparison["section_id"]]["product_links"][
        "opportunity_state"
    ] == "unassessed"

    safety = by_heading["Safety and Compliance Limits"]
    assert safety["reader_stage"] == "verify"
    assert safety["product_link_allowed"] is False
    safety_gate = link_by_id[safety["section_id"]]["product_links"]
    assert safety_gate["opportunity_state"] == "none"
    assert safety_gate["min_required"] == 0
    assert safety_gate["reason_code"] == "section_role_prohibits_product_link"


def test_unassessed_gate_cannot_silently_default_to_zero():
    bundle = _bundle()
    broken = copy.deepcopy(bundle["section_link_contracts"])
    broken["sections"][0]["article_links"]["min_required"] = 0

    with pytest.raises(ContractValidationError, match="min_required=null"):
        validate_section_link_contracts(broken, bundle["section_contracts"])


def test_required_gate_needs_candidate_and_minimum_one():
    bundle = _bundle()
    broken = copy.deepcopy(bundle["section_link_contracts"])
    gate = broken["sections"][0]["article_links"]
    gate.update({
        "opportunity_state": "required",
        "candidate_count": 0,
        "min_required": 1,
        "reason_code": "high_relevance_article_candidate",
    })

    with pytest.raises(ContractValidationError, match="needs a candidate"):
        validate_section_link_contracts(broken, bundle["section_contracts"])


def test_duplicate_outline_heading_fails_closed():
    with pytest.raises(ContractValidationError, match="duplicate outline heading"):
        build_contract_bundle(
            topic="Duplicate",
            tier="Cluster Content",
            intent="Test",
            outline=["Safety", " safety "],
        )


def test_bundle_round_trip_is_deterministic(tmp_path):
    bundle = _bundle()
    paths = persist_contract_bundle(tmp_path, "ceiling-layout", bundle)
    loaded = load_contract_bundle(tmp_path, "ceiling-layout")

    assert loaded == bundle
    assert set(paths) == {
        "article_blueprint",
        "section_contracts",
        "section_link_contracts",
    }
    first_bytes = {
        name: Path(path).read_bytes()
        for name, path in paths.items()
    }
    persist_contract_bundle(tmp_path, "ceiling-layout", bundle)
    second_bytes = {
        name: Path(path).read_bytes()
        for name, path in paths.items()
    }
    assert first_bytes == second_bytes


def test_bundle_validation_rejects_misaligned_section_order():
    bundle = _bundle()
    broken = copy.deepcopy(bundle)
    broken["section_contracts"]["section_order"] = list(
        reversed(broken["section_contracts"]["section_order"])
    )

    with pytest.raises(ContractValidationError, match="IDs/order"):
        validate_contract_bundle(broken)


def test_bundle_validation_rejects_cross_file_topic_mismatch():
    bundle = _bundle()
    broken = copy.deepcopy(bundle)
    broken["section_link_contracts"]["topic"] = "Different topic"

    with pytest.raises(ContractValidationError, match="topic does not match"):
        validate_contract_bundle(broken)


def test_persist_rolls_back_existing_bundle_on_partial_replace_failure(
    tmp_path, monkeypatch
):
    from seo_ops.services import sectional_writing as sw

    original = _bundle()
    paths = persist_contract_bundle(tmp_path, "ceiling-layout", original)
    snapshots = {name: Path(path).read_bytes() for name, path in paths.items()}

    changed = build_contract_bundle(
        topic="Laser Pointer for Ceiling Layout",
        tier="Cluster Content",
        intent="Changed intent.",
        outline=[
            "Why Ceiling Layout Needs Clear Pointing",
            "Choosing a Tool for the Work Area",
        ],
    )
    real_replace = sw.os.replace
    calls = 0

    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated contract bundle replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(sw.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="simulated contract bundle"):
        persist_contract_bundle(tmp_path, "ceiling-layout", changed)

    assert {name: Path(path).read_bytes() for name, path in paths.items()} == snapshots
