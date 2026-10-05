import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

def run_cli(*args):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    return subprocess.run(
        [sys.executable, "-m", "migrator", *args],
        cwd=PROJECT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_cli_help_without_arguments():
    result = run_cli()

    assert result.returncode == 0
    assert "python -m migrator discover" in result.stdout
    assert "python -m migrator analyze" in result.stdout
    assert "python -m migrator plan" in result.stdout
    assert "python -m migrator apply" in result.stdout


def test_cli_unknown_command():
    result = run_cli("does-not-exist")

    assert result.returncode == 0
    assert "Unknown command: does-not-exist" in result.stdout

def test_cli_analyze_arbitrary_table(tmp_path):
    sql_file = tmp_path / "securitizations.sql"

    sql_file.write_text(
        """
        CREATE TABLE offloading.securitizations (
            id integer,
            migration_test_2 text
        );
        """,
        encoding="utf-8",
    )

    result = run_cli("analyze", str(sql_file))

    assert result.returncode == 0
    assert "ROOT: offloading.securitizations" in result.stdout
    assert "BREAKING:" in result.stdout


def test_cli_plan_arbitrary_table(tmp_path):
    sql_file = tmp_path / "securitizations.sql"

    sql_file.write_text(
        """
        CREATE TABLE offloading.securitizations (
            id integer,
            migration_test_2 text
        );
        """,
        encoding="utf-8",
    )

    result = run_cli("plan", str(sql_file))

    assert result.returncode == 0
    assert "MIGRATION TARGET: offloading.securitizations" in result.stdout
    assert "OBJECT TYPE: TABLE" in result.stdout


def test_cli_discover_arbitrary_sql_file():
    result = run_cli(
        "discover",
        str(PROJECT_ROOT / "offloading_securitization_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "TARGET:" in result.stdout
    assert "offloading.securitizations" in result.stdout
    assert "TABLE" in result.stdout


def test_cli_analyze_arbitrary_sql_file():
    result = run_cli(
        "analyze",
        str(PROJECT_ROOT / "offloading_securitization_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "ROOT: offloading.securitizations" in result.stdout
    assert "BREAKING: False" in result.stdout
    assert "COLUMN CHANGES:" in result.stdout


def test_cli_plan_arbitrary_sql_file():
    result = run_cli(
        "plan",
        str(PROJECT_ROOT / "offloading_securitization_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "MIGRATION TARGET: offloading.securitizations" in result.stdout
    assert "OBJECT TYPE: TABLE" in result.stdout


def test_cli_discover_managed_family_sql_file():
    result = run_cli(
        "discover",
        str(PROJECT_ROOT / "v_collection_base_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "TARGET:" in result.stdout
    assert "auto_views.v_collection_base" in result.stdout


def test_cli_analyze_managed_family_sql_file():
    result = run_cli(
        "analyze",
        str(PROJECT_ROOT / "v_collection_base_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "auto_views.v_collection_base" in result.stdout
    assert "IMPACTED OBJECTS:" in result.stdout


def test_cli_plan_managed_family_sql_file():
    result = run_cli(
        "plan",
        str(PROJECT_ROOT / "v_collection_base_changed.sql"),
    )

    assert result.returncode == 0, result.stderr
    assert "auto_views.v_collection_base" in result.stdout

    # Managed physical table must never be dropped or recreated.
    assert 'DROP TABLE "auto_views"."t_collection_base"' not in result.stdout
    assert 'CREATE TABLE "auto_views"."t_collection_base"' not in result.stdout

    # Explicit source-view SQL must be present.
    assert "migration_test" in result.stdout
    assert "migration_test2" in result.stdout
    assert "migration_test3" in result.stdout

    # The published view is generated from the supplied source definition.
    assert 'CREATE VIEW "public"."mv_collection_base"' in result.stdout


def test_discover_managed_family_dependency_tree():
    result = run_cli(
        "discover",
        "v_collection_base_changed.sql",
    )

    assert result.returncode == 0, result.stderr

    assert (
        "TARGET: auto_views.v_collection_base [VIEW]"
        in result.stdout
    )

    assert "OID:" in result.stdout

    assert "DEPENDENCY TREE:" in result.stdout

    assert (
        "auto_views.v_collection_base [VIEW]"
        in result.stdout
    )

    assert (
        "public.mv_collection_base [VIEW]"
        in result.stdout
    )

    assert (
        "auto_views.v_collection_segmentation_v0 [VIEW]"
        in result.stdout
    )

    assert (
        "auto_views.v_consumer_delinquency [VIEW]"
        in result.stdout
    )