from pathlib import Path


def test_initial_migration_is_frozen_schema() -> None:
    migration = Path("migrations/versions/20260602_0001_initial_schema.py")
    content = migration.read_text()

    assert "app.storage.models" not in content
    assert "Base.metadata" not in content
    assert "create_all" not in content
    assert "drop_all" not in content
    assert "op.create_table" in content
    assert "op.create_index" in content
