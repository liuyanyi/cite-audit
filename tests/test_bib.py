import hashlib

import pytest

from cite_audit.bib import (
    MAX_BYTES,
    BibLimitError,
    ExportSelection,
    parse_bib,
    preview_export,
    serialize_entry,
)


def patch(entry_id="entry-1", **fields):
    return ExportSelection(entry_id, "same_version_patch", fields)


def switch(**fields):
    return ExportSelection(
        "entry-1",
        "whole_version_switch",
        {"title": "A {BERT} Paper", "author": "Doe, Jane", "year": "2019", **fields},
        "inproceedings",
        "formal-v2",
    )


def codes(document):
    return {diagnostic.code for diagnostic in document.diagnostics}


def test_noop_retains_bom_crlf_comments_preamble_macros_and_unknown_fields():
    raw = (
        b'\xef\xbb\xbf% annotation\r\n@string{conf = "Proceedings of " # {ACL}}\r\n'
        b'@preamble{"\\newcommand{\\myname}{X}"}\r\n'
        b"@comment{A {nested} comment}\r\n"
        b"@InProceedings{UserKey,\r\n  title = {A {BERT} paper},\r\n"
        b"  booktitle = conf,\r\n  month = jan,\r\n  custom-tag = {mine},\r\n}\r\n"
    )
    doc = parse_bib(raw)
    assert doc.original == raw
    assert doc.line_ending == "\r\n"
    assert len(doc.entries) == 1
    assert doc.entries[0].fields["booktitle"] == "Proceedings of ACL"
    assert doc.entries[0].fields["month"] == "January"
    result = preview_export(doc, [])
    assert result.content == raw
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert not result.changes
    assert not result.blocked


def test_patch_replaces_only_selected_value_and_preserves_original():
    raw = b'\xef\xbb\xbf% heading\r\n@article{Key,\r\n title = "Original", % comment\r\n year=2023,\r\n custom={keep}\r\n}\r\n'
    doc = parse_bib(raw)
    result = preview_export(doc, [patch(year="2024")])
    assert result.content == raw.replace(b"year=2023", b"year={2024}")
    assert doc.original == raw
    assert doc.entries[0].fields["year"] == "2023"
    assert result.changes == [
        {
            "entry_id": "entry-1",
            "key": "Key",
            "proposal_type": "same_version_patch",
            "field": "year",
            "before": "2023",
            "after": "2024",
        }
    ]


def test_equivalent_selected_value_is_byte_identical_not_pretty_printed():
    raw = b'@article{K, year=2024, title="Original"}'
    result = preview_export(parse_bib(raw), [patch(year="2024", title="Original")])
    assert result.content == raw
    assert not result.changes


@pytest.mark.parametrize(
    "raw",
    [
        b"@misc{key}",
        b"@misc{key,}",
        b"@misc{key,title={X}}",
        b"@misc{key,title={X}\n}",
        b"@misc{key,title={X},\n}",
        b"@misc(key,title={X})",
        b"@misc{key, title={X} % last comment\r\n}",
    ],
)
def test_completion_insertion_is_valid_with_or_without_trailing_comma(raw):
    result = preview_export(parse_bib(raw), [patch(year="2024", pages="1--9")])
    assert not result.blocked
    parsed = parse_bib(result.content)
    assert not parsed.diagnostics
    assert parsed.entries[0].fields["year"] == "2024"
    assert parsed.entries[0].fields["pages"] == "1--9"


def test_existing_macro_field_unchanged_when_other_field_changes():
    raw = b"@string{J={Journal}}\n@article{K,journal=J,year=2023}"
    result = preview_export(parse_bib(raw), [patch(year="2024")])
    assert not result.blocked
    assert result.content == raw.replace(b"year=2023", b"year={2024}")
    blocked = preview_export(parse_bib(raw), [patch(journal="Other", year="2024")])
    assert blocked.content == raw
    assert "macro" in blocked.blocked[0]["message"]
    assert not blocked.changes


def test_macro_scope_concatenation_and_unknown_macro_are_explicit():
    doc = parse_bib(
        '@string{a="First"}\n@article{k1,title=a # " work"}\n'
        '@string{a="Second"}\n@article{k2,title=a, journal=unknown}\n'
    )
    assert doc.entries[0].fields["title"] == "First work"
    assert doc.entries[1].fields["title"] == "Second"
    assert {"redefined_macro", "undefined_macro"} <= codes(doc)


def test_duplicates_are_separate_diagnosed_entries_and_never_edited():
    raw = b"@article{same,title={A}}\n@article{same,title={B}}"
    doc = parse_bib(raw)
    assert len(doc.entries) == 2
    assert [e.entry_id for e in doc.entries] == ["entry-1", "entry-2"]
    assert all(not entry.editable for entry in doc.entries)
    assert all("duplicate_key" in {d.code for d in e.diagnostics} for e in doc.entries)
    result = preview_export(doc, [patch(title="C")])
    assert result.content == raw
    assert result.blocked


def test_duplicate_fields_keep_raw_and_block_ambiguous_edit():
    raw = b"@article{k,title={A},TITLE={B}}"
    doc = parse_bib(raw)
    assert "duplicate_field" in codes(doc)
    assert doc.entries[0].raw.encode() == raw
    assert preview_export(doc, [patch(year="2024")]).blocked


def test_partial_malformed_entries_recover_without_silently_dropping_entries():
    raw = (
        b"@article{bad,title={Unclosed\n"
        b"@article{good,title={Valid},year=2024}\n"
        b"@article{other,title {Missing equals}}\n"
        b"@misc{last,title={Another valid}}\n"
    )
    doc = parse_bib(raw)
    assert [e.key for e in doc.entries] == ["bad", "good", "other", "last"]
    assert [e.editable for e in doc.entries] == [False, True, False, True]
    assert doc.entries[0].diagnostics[0].line == 1
    assert doc.entries[2].diagnostics[0].line == 3
    result = preview_export(doc, [patch("entry-2", year="2025")])
    assert not result.blocked
    assert result.content == raw.replace(b"year=2024", b"year={2025}")


def test_malformed_macro_diagnostic_does_not_swallow_next_entry():
    doc = parse_bib('@string{x = "unclosed\n@article{k,title={Valid}}')
    assert len(doc.entries) == 1
    assert doc.entries[0].key == "k"
    assert "malformed_entry" in codes(doc)


def test_crossref_expands_for_comparison_and_blocks_hidden_changes():
    raw = (
        b"@inproceedings{child,title={Paper},crossref={parent}}\n"
        b"@proceedings{parent,booktitle={Proceedings},year={2024}}"
    )
    doc = parse_bib(raw)
    child = doc.entries[0]
    assert child.fields["year"] == "2024"
    assert child.inherited_fields == {"year", "booktitle"}
    assert preview_export(doc, [patch(year="2025")]).blocked
    assert preview_export(doc, [patch("entry-2", year="2025")]).blocked
    assert preview_export(doc, [switch()]).blocked
    result = preview_export(doc, [patch(title="Safe local title")])
    assert not result.blocked
    assert b"crossref={parent}" in result.content
    assert parse_bib(result.content).entries[0].fields["year"] == "2024"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("@misc{a,crossref={missing}}", "crossref_missing"),
        ("@misc{a,crossref={b}}\n@misc{b,crossref={a}}", "crossref_cycle"),
        ("@misc{a,crossref={b}}\n@misc{b}\n@misc{b}", "crossref_ambiguous"),
        ("@misc{a,xdata={common}}", "unsupported_inheritance"),
    ],
)
def test_unresolved_inheritance_is_explicit(raw, expected):
    doc = parse_bib(raw)
    assert expected in codes(doc)
    assert preview_export(doc, [patch(title="No silent guessing")]).blocked


def test_whole_version_removes_obsolete_metadata_keeps_key_customs_and_comments():
    raw = (
        b"% header\n@misc{MyStableKey,\n"
        b"title={Old title}, author={Doe, Jane}, year={2018},\n"
        b"doi={10.0000/old}, eprint={1801.00001}, archivePrefix={arXiv}, primaryClass={cs.CL},\n"
        b"journal={arXiv preprint}, url={https://arxiv.org/abs/1801.00001},\n"
        b"pages={99--101}, volume={1}, % author comment\n"
        b"my-custom={private note},\n}\n"
        b"@misc{untouched,title={Leave me alone}}\n"
    )
    doc = parse_bib(raw)
    result = preview_export(doc, [switch(booktitle="NAACL", pages="1--9")])
    assert not result.blocked
    parsed = parse_bib(result.content)
    entry = parsed.entries[0]
    assert entry.key == "MyStableKey"
    assert entry.entry_type == "inproceedings"
    assert entry.fields == {
        "title": "A {BERT} Paper",
        "author": "Doe, Jane",
        "year": "2019",
        "booktitle": "NAACL",
        "pages": "1--9",
        "my-custom": "private note",
    }
    assert b"% header" in result.content and b"% author comment" in result.content
    assert parsed.entries[1].raw == doc.entries[1].raw
    assert doc.original == raw
    removed = {change["field"] for change in result.changes if change["after"] is None}
    assert {"doi", "eprint", "archiveprefix", "primaryclass", "journal", "url", "volume"} <= removed


def test_patch_does_not_remove_other_version_fields_implicitly():
    raw = b"@misc{k,title={Preprint},year=2018,eprint={1801.00001}}"
    result = preview_export(parse_bib(raw), [patch(title="Corrected preprint title")])
    assert parse_bib(result.content).entries[0].fields["eprint"] == "1801.00001"
    assert parse_bib(result.content).entries[0].entry_type == "misc"


@pytest.mark.parametrize(
    "choice",
    [
        ExportSelection("entry-1", "whole_version_switch", {"year": "2024"}, "article"),
        ExportSelection("entry-1", "same_version_patch", {"title": "X"}, "inproceedings"),
        ExportSelection("entry-1", "made_up", {"year": "2024"}),
        ExportSelection("entry-1", "same_version_patch", {"citation_key": "new-key"}),
        ExportSelection("entry-1", "same_version_patch", {"year": "2024", "Year": "2025"}),
        ExportSelection("entry-1", "same_version_patch", {"crossref": "parent"}),
        ExportSelection("entry-1", "same_version_patch", {"year, title": "injected"}),
        ExportSelection("not-here", "same_version_patch", {"year": "2024"}),
    ],
)
def test_defensively_invalid_selection_is_blocked(choice):
    raw = b"@article{k,title={Original},year=2023}"
    result = preview_export(parse_bib(raw), [choice])
    assert result.content == raw
    assert result.blocked
    assert not result.changes


@pytest.mark.parametrize(
    "value",
    [
        r"\input{/etc/passwd}",
        r"\write18{touch /tmp/not-allowed}",
        r"\csname input\endcsname",
        r"\directlua{os.execute('bad')}",
        r"\catcode`\@=0",
        r"^^5cinput{evil}",
        "Unbalanced}",
        "{Unbalanced",
        "ends with \\",
        "100% hidden closing brace",
        "has\x00nul",
        r"\def\attack{\input{file}}",
        "bad\udcffunicode",
    ],
)
def test_unsafe_tex_or_boundary_injection_is_never_serialized(value):
    raw = b"@article{k,title={Original}}"
    result = preview_export(parse_bib(raw), [patch(title=value)])
    assert result.content == raw
    assert result.blocked
    with pytest.raises(ValueError):
        serialize_entry("k", "article", {"title": value})


def test_regular_latex_unicode_and_case_protection_are_preserved():
    value = r"{BERT}, G\"{o}del, \textit{中文} and 50\%"
    snippet = serialize_entry("my:key", "article", {"title": value, "year": "2024"})
    assert parse_bib(snippet).entries[0].fields["title"] == value


def test_original_unsafe_tex_is_only_inert_text_never_executed():
    raw = b"@article{k,title={\\input{DO_NOT_OPEN}}}"
    doc = parse_bib(raw)
    assert "unsafe_tex" in codes(doc)
    assert preview_export(doc, []).content == raw


def test_invalid_utf8_roundtrips_and_affected_entry_is_blocked():
    raw = b"@article{k,title={Bad\xffbytes}}\n@misc{good,title={OK}}"
    doc = parse_bib(raw)
    assert "invalid_utf8" in codes(doc)
    assert preview_export(doc, []).content == raw
    assert preview_export(doc, [patch(year="2024")]).blocked
    assert not preview_export(doc, [patch("entry-2", year="2024")]).blocked


def test_selecting_patch_and_switch_for_same_entry_blocks_both():
    raw = b"@article{k,title={Old},year={2018},author={Doe}}"
    result = preview_export(parse_bib(raw), [patch(year="2019"), switch()])
    assert result.content == raw
    assert len(result.blocked) == 1
    assert not result.changes


def test_safe_entries_preview_but_blocked_entries_are_explicit():
    raw = b"@article{bad,title {malformed}}\n@misc{good,year=2023}"
    result = preview_export(parse_bib(raw), [patch(title="X"), patch("entry-2", year="2024")])
    assert len(result.blocked) == 1
    assert len(result.changes) == 1
    assert result.content == raw.replace(b"year=2023", b"year={2024}")


def test_mapping_selections_are_supported():
    result = preview_export(
        parse_bib("@misc{k,year=2023}"),
        [
            {
                "entry_id": "entry-1",
                "proposal_type": "same_version_patch",
                "fields": {"year": "2024"},
            }
        ],
    )
    assert not result.blocked
    assert b"year={2024}" in result.content


def test_resource_limits_are_explicit_not_silent_truncation():
    with pytest.raises(BibLimitError, match="8 MiB"):
        parse_bib(b" " * (MAX_BYTES + 1))
    doc = parse_bib("@misc{k,title={" + "{" * 130 + "x" + "}" * 131 + "}")
    assert "nesting_limit" in codes(doc)
    assert not doc.entries[0].editable


def test_original_integrity_check_prevents_mutated_document_use():
    doc = parse_bib("@misc{k,year=2023}")
    doc.text = "@misc{k,year=2024}"
    with pytest.raises(ValueError, match="integrity"):
        preview_export(doc, [])
