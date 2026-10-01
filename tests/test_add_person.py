"""Exercise the notebook's add-person callback without an AiiDA database."""

import ast
from contextlib import nullcontext
from datetime import datetime
import json
from pathlib import Path
import re
import time
from types import SimpleNamespace


def make_add_person_form(notebook=None):
    """Load the real callback, replacing only widgets and database operations."""
    if notebook is None:
        path = Path(__file__).resolve().parents[1] / "coffee.ipynb"
        notebook = json.loads(path.read_text())

    created = []

    class TestMember(dict):
        pk = "test-only"

        def __init__(self, **values):
            super().__init__(values)
            self.extras = {}

        def set_extra(self, key, value):
            self.extras[key] = value

    def new_person(name, email, started):
        member = TestMember(name=name.value, email=email.value, started=started.value)
        created.append(member)
        return member

    def check_people():
        # The real refresh returns three values. Reading saved members here
        # also checks that saving/label assignment happens before the refresh.
        assert all(person.label == "nanotech@coffee_member" for person in created)
        return [person["email"] for person in created], 40.0, -15.0

    namespace = {
        "re": re,
        "time": time,
        "datetime": datetime,
        "output": nullcontext(),
        "clear_output": lambda: None,
        "name": SimpleNamespace(value="Test Person"),
        "email": SimpleNamespace(value="test.person@example.org"),
        "startdate": SimpleNamespace(value=datetime(2026, 1, 5)),
        "emails": [],
        "cash": 0.0,
        "cost_correction": 0.0,
        "Str": lambda value: SimpleNamespace(value=value),
        "Float": lambda value: SimpleNamespace(value=value),
        "new_person": new_person,
        "check_people": check_people,
    }
    needed = {"validemail", "timeconversion", "on_add_person_clicked"}
    found = set()
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if cell["cell_type"] != "code" or source.lstrip().startswith("%"):
            continue
        for statement in ast.parse(source).body:
            if isinstance(statement, ast.FunctionDef) and statement.name in needed:
                module = ast.Module(body=[statement], type_ignores=[])
                exec(compile(module, "coffee.ipynb", "exec"), namespace)
                found.add(statement.name)
    assert found == needed
    return namespace, created


def test_add_person_refreshes_all_three_report_values(capsys):
    namespace, created = make_add_person_form()
    namespace["on_add_person_clicked"](None)

    assert len(created) == 1
    assert created[0]["name"] == "Test Person"
    assert created[0].extras["coeff"] == 1
    assert namespace["emails"] == ["test.person@example.org"]
    assert namespace["cash"] == 40.0
    assert namespace["cost_correction"] == -15.0
    assert "Added Test Person" in capsys.readouterr().out


def test_second_click_does_not_add_the_same_person_again(capsys):
    namespace, created = make_add_person_form()
    namespace["on_add_person_clicked"](None)
    namespace["on_add_person_clicked"](None)

    assert len(created) == 1
    assert namespace["emails"] == ["test.person@example.org"]
    assert "person already present" in capsys.readouterr().out


def test_invalid_email_does_not_create_a_member():
    namespace, created = make_add_person_form()
    namespace["email"].value = "invalid"
    namespace["on_add_person_clicked"](None)

    assert created == []
    assert namespace["emails"] == []
