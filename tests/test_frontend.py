"""Tests for frontend workflows and syntax in static/app.js."""
from pathlib import Path
import re
import subprocess

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_JS = REPO_ROOT / "static" / "app.js"


def test_node_check_app_js():
    """Verify static/app.js passes node syntax checking."""
    res = subprocess.run(["node", "--check", str(APP_JS)], capture_output=True, text=True)
    assert res.returncode == 0, f"node --check failed:\n{res.stderr}"


def test_brew_workflow_and_routes():
    """Verify top-level #/brew workflow, route, and entry points in static/app.js."""
    code = APP_JS.read_text(encoding="utf-8")

    # Navigation entry point and active state
    assert '["#/brew", "＋ Brew", "add"]' in code
    assert 'h === "#/brew"' in code
    assert 'sect === "brew"' in code

    # Top-level route handling
    assert 'path === "brew"' in code
    assert 'reviewView(p.get("bag") ? +p.get("bag") : null, p.get("method"), true)' in code

    # Shelf header and bag hero entry points
    assert 'href="#/brew"' in code
    assert 'href="#/bag/${b.id}/review"' in code


def test_bag_selector_and_seamless_review():
    """Verify bag selector, optgroups, and review create/edit functionality."""
    code = APP_JS.read_text(encoding="utf-8")

    # Bag selector & include_finished=1
    assert 'shelf?include_finished=1' in code
    assert 'name="bag_id"' in code
    assert 'optgroup label="On the shelf"' in code
    assert 'optgroup label="Finished"' in code

    # Method option reviewed indicator without disabling
    assert '(reviewed)' in code
    # Ensure options are not disabled when reviewed
    assert not re.search(r'taken\.has\([^)]+\)\s*&&[^>]*disabled', code)


def test_in_store_grinder_ux():
    """Verify In Store grinder UX clears grind setting and deletes touched dataset."""
    code = APP_JS.read_text(encoding="utf-8")

    assert 'in store' in code.lower()
    assert 'fm.grinder' in code
    assert 'fm.grind.value = ""' in code
    assert 'delete fm.grind.dataset.touched' in code


def test_staged_upload_deletion():
    """Verify staged upload removal sends DELETE request."""
    code = APP_JS.read_text(encoding="utf-8")

    assert 'uploads/' in code
    assert 'DELETE' in code
    assert 'const [tok] = uploads.splice(+x.dataset.rm, 1);' in code


def test_brew_method_move_down():
    """Verify brew method move-down button and handler in moreView."""
    code = APP_JS.read_text(encoding="utf-8")

    assert 'data-dn=' in code
    assert 'data-dn="${i}"' in code
    assert 'rows[i + 1]' in code
