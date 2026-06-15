import os
import re
import pytest
from pathlib import Path
from pydantic import BaseModel

from storage.postgres_store import TABLE_COLUMNS
import storage.schemas as schemas


def get_migrations_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "migrations"


def test_migration_sequence_and_names():
    """
    Verify migration filenames in migrations/ are strictly sequential
    starting at 001_ and have no gaps or duplicates.
    """
    migrations_dir = get_migrations_dir()
    assert migrations_dir.is_dir(), f"Migrations directory not found at {migrations_dir}"
    
    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
    assert len(files) > 0, "No SQL migration files found"
    
    prefixes = []
    for f in files:
        match = re.match(r"^(\d+)_", f)
        assert match is not None, f"Migration filename does not start with digits: {f}"
        prefixes.append(int(match.group(1)))
        
    # Check that prefixes start at 1 and are contiguous
    assert prefixes[0] == 1, "Migration prefixes do not start at 001"
    for i in range(len(prefixes)):
        assert prefixes[i] == i + 1, f"Missing or out-of-order migration prefix: expected {i + 1:03d}, got {prefixes[i]:03d}"


def test_schema_compatibility_between_migrations_and_table_columns():
    """
    Parse SQL migrations to extract tables and columns, and prove that
    every table and column in TABLE_COLUMNS is defined in the migrations.
    """
    migrations_dir = get_migrations_dir()
    files = sorted([f for f in os.listdir(migrations_dir) if f.endswith(".sql")])
    
    parsed_tables = {}  # table_name -> set of columns
    
    create_table_regex = re.compile(r"(?i)CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-zA-Z_0-9]+)\s*\((.*?)\);", re.DOTALL)
    alter_table_regex = re.compile(r"(?i)ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\w+)")
    
    for f in files:
        content = (migrations_dir / f).read_text(encoding="utf-8")
        
        # Parse CREATE TABLE
        for match in create_table_regex.finditer(content):
            table_name = match.group(1).lower()
            body = match.group(2)
            
            if table_name not in parsed_tables:
                parsed_tables[table_name] = set()
                
            # Parse column lines
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                
                # Check for SQL constraints or primary key declarations that aren't columns
                # Typically, they start with CONSTRAINT, PRIMARY KEY, FOREIGN KEY, etc.
                upper_line = line.upper()
                if any(upper_line.startswith(kw) for kw in ["PRIMARY KEY", "FOREIGN KEY", "CONSTRAINT", "UNIQUE", "CHECK"]):
                    continue
                
                # Extract first word as column name
                words = line.split()
                if words:
                    col_name = words[0].replace('"', '').replace('`', '').lower()
                    # Filter out SQL keywords that might be parsed
                    if col_name not in ["primary", "foreign", "constraint", "unique", "check"]:
                        parsed_tables[table_name].add(col_name)
                        
        # Parse ALTER TABLE ADD COLUMN
        for match in alter_table_regex.finditer(content):
            table_name = match.group(1).lower()
            col_name = match.group(2).lower()
            if table_name not in parsed_tables:
                parsed_tables[table_name] = set()
            parsed_tables[table_name].add(col_name)

    # Assert that all tables in TABLE_COLUMNS exist in migrations
    for table, expected_cols in TABLE_COLUMNS.items():
        assert table in parsed_tables, f"Table '{table}' defined in TABLE_COLUMNS is missing from migrations"
        
        # Assert that all expected columns in TABLE_COLUMNS exist in the migrations
        for col in expected_cols:
            assert col in parsed_tables[table], f"Column '{col}' in table '{table}' (TABLE_COLUMNS) is missing from migrations"


def test_pydantic_model_field_compatibility_with_table_columns():
    """
    Ensure every field in Pydantic schemas matches the columns in TABLE_COLUMNS
    for its corresponding database table.
    """
    # Map schemas.py models to database tables
    model_to_table = {
        schemas.Call: "calls",
        schemas.CallTurn: "call_turns",
        schemas.ToolEvent: "tool_events",
        schemas.QAReport: "qa_reports",
        schemas.TrainingNote: "training_notes",
        schemas.Transfer: "transfers",
        schemas.Callback: "callbacks",
        schemas.DncRequest: "dnc_requests",
        schemas.ConsentRecord: "consent_records",
        schemas.LatencyMetric: "latency_metrics",
        schemas.Campaign: "campaigns",
        schemas.CallCost: "call_costs",
        schemas.OutcomeMetric: "outcome_metrics",
        schemas.TrainingSource: "training_sources",
        schemas.TrainingExample: "training_examples",
        schemas.EvalCase: "eval_cases",
        schemas.PromptVersion: "prompt_versions",
        schemas.HumanReviewItem: "human_review_items",
        schemas.DeploymentExperiment: "deployment_experiments",
        schemas.CallOutcomeLabel: "call_outcome_labels",
        schemas.TelephonyProviderConfig: "telephony_provider_configs",
        schemas.OutboundCampaign: "outbound_campaigns",
        schemas.CampaignLead: "campaign_leads",
        schemas.CallAttempt: "call_attempts",
        schemas.LiveCallSession: "live_call_sessions",
        schemas.CampaignControlEvent: "campaign_control_events",
        schemas.CostRateCard: "cost_rate_cards",
    }
    
    for model_cls, table_name in model_to_table.items():
        assert table_name in TABLE_COLUMNS, f"Table '{table_name}' mapped to model {model_cls.__name__} is missing from TABLE_COLUMNS"
        
        # Get model fields
        fields = model_cls.model_fields.keys()
        
        # Check that every field in the Pydantic model is defined in TABLE_COLUMNS
        table_cols = TABLE_COLUMNS[table_name]
        for field in fields:
            # We skip 'timestamp' fields since some tables use 'created_at' or specific timestamp columns
            if field == "timestamp":
                continue
            # Some models have 'id' but the DB generates it or it's mapped differently,
            # but usually it's in table_cols. If 'id' is not in table_cols, skip it.
            if field == "id" and "id" not in table_cols:
                continue
                
            # Skip fields in TrainingNote that are mapped dynamically to other columns on save
            if model_cls == schemas.TrainingNote and field in ("good_response_example", "bad_response_example", "extracted_at"):
                continue
                
            assert field in table_cols, f"Field '{field}' in Pydantic model '{model_cls.__name__}' is missing from TABLE_COLUMNS['{table_name}']"
