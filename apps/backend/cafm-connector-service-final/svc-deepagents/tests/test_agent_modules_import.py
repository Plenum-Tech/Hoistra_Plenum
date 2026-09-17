"""Every agent module imports, and every @tool in it still has its docstring.

The orchestrator imports the agent modules lazily, inside the app lifespan — so a module
that fails at import time passes every unit test and takes the service down at startup
instead (revision 0000062: a line placed above a tool's docstring turned the docstring
into a plain expression, and LangChain refused the tool without one). This imports each
module the way the lifespan does and checks the tools are whole.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest
from langchain_core.tools import BaseTool

import src.agents as agents_pkg

MODULES = sorted(m.name for m in pkgutil.iter_modules(agents_pkg.__path__, "src.agents.")
                 if not m.name.rsplit(".", 1)[-1].startswith("_"))


@pytest.mark.parametrize("name", MODULES)
def test_agent_module_imports(name):
    mod = importlib.import_module(name)
    for attr, value in vars(mod).items():
        if isinstance(value, BaseTool):
            assert value.description, f"{name}.{attr} has no description"


def test_the_two_compliance_tools_kept_their_docstrings():
    from src.agents import compliance_agent as m
    assert "Check compliance status" in m.check_requirements.description
    assert "portfolio-wide compliance summary" in m.generate_compliance_report.description
