"""Compare batch and individual writers. For the optional local papers, set MATHSHUTTLE_ARXIV_SAMPLE
to the corpus directory and run python -I -m pytest tests/test_batch_extract.py."""
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mathshuttle import pandoc_engine
from mathshuttle.convert import convert_equation, convert_equations
from mathshuttle.documents import read_document
from mathshuttle.pandoc_engine import ConversionError, Equation


FORMATS = ("mathml", "omml", "typst", "eqn")


def check_equations(eqs, macros=None):
    assert eqs
    for fmt in FORMATS:
        batch = convert_equations(eqs, fmt, macros=macros)
        assert len(batch) == len(eqs)
        for i, (eq, result) in enumerate(zip(eqs, batch), 1):
            try:
                expected = convert_equation(eq, fmt, macros=macros)
            except ConversionError as e:
                assert isinstance(result, ConversionError) and str(result) == str(e), (fmt, i, eq)
                assert getattr(result, "short", None) == getattr(e, "short", None)
            else:
                assert result == expected, (fmt, i, eq)


def check_document(path):
    doc = read_document(str(path))
    check_equations([found.equation for found in doc.found], doc.macros(None))


@pytest.mark.parametrize("name", ("paper.tex", "notes.md", "sample.docx", "sample.pptx"))
def test_batch_matches_fixture(name):
    check_document(Path(__file__).parent / "fixtures" / "documents" / name)


def test_batch_matches_office_math_fixtures():
    eqs = []
    for path in sorted((Path(__file__).parent / "fixtures" / "word").glob("*.xml")):
        fmt = path.name.split(".")[-2]
        eqs.extend(pandoc_engine.read_equations(path.read_text(encoding="utf-8"), fmt))
    check_equations(eqs)


def test_empty_renderings_and_preparation_errors():
    check_equations([Equation(r"\label{empty}"), Equation("", False), Equation(r"{\newcommand{\X}{a}\X}"),
                     Equation(r"\text{a @ b}"), Equation(r"x\label{eq/a}"), Equation("x", False)])


@pytest.mark.skipif(not (os.environ.get("MATHSHUTTLE_ARXIV_SAMPLE") and sys.flags.isolated),
                    reason="optional local arXiv corpus, run with python -I")
@pytest.mark.parametrize("paper", ("2611.34118", "2611.34701", "2611.34818"))
def test_batch_matches_arxiv_paper(paper):
    check_document(Path(os.environ["MATHSHUTTLE_ARXIV_SAMPLE"]) / paper / "main.tex")


@pytest.mark.parametrize("fmt", FORMATS)
def test_one_writer_call_and_only_rejected_equations_retried(fmt, monkeypatch):
    import json

    eqs = [Equation(r"x\label{eq/1}"), Equation(r"a_1", False), Equation(r"\UnknownCommand{x}"),
           Equation(r"\begin{aligned}a&=b\\c&=d\end{aligned}"), Equation(r"a_1", False)]
    pandoc_engine._api()
    pandoc_engine._mathml_flags()
    real_run = pandoc_engine._run
    calls = []

    def record(args, stdin, **kwargs):
        calls.append(json.loads(stdin)["blocks"])
        return real_run(args, stdin, **kwargs)

    monkeypatch.setattr(pandoc_engine, "_run", record)
    results = convert_equations(eqs, fmt)
    assert len(calls) == 2
    assert [block["t"] for block in calls[0]] == ["Div"] * 4
    assert calls[1][0]["c"][0]["c"][1] == eqs[2].tex
    assert isinstance(results[2], ConversionError)
    assert results[0][0] and results[1] == results[4] and results[3][0]
