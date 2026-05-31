import pytest
import json
import csv
from pathlib import Path
from storage.repository import Repository
from telephony.lead_importer import CampaignLeadImporter


@pytest.fixture
def temp_csv_leads(tmp_path):
    csv_file = tmp_path / "leads.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["first_name", "last_name", "phone_number", "state", "priority"])
        writer.writerow(["John", "Doe", "+15551234567", "TN", "10"])
        writer.writerow(["Jane", "Smith", "555-987-6543", "NY", "5"])
        writer.writerow(["Invalid", "Number", "123", "CA", "1"])  # Invalid phone
    return csv_file


@pytest.fixture
def temp_json_leads(tmp_path):
    json_file = tmp_path / "leads.json"
    data = {
        "campaign_id": "test_campaign",
        "leads": [
            {"first_name": "Mary", "last_name": "Smith", "phone_number": "+15559990001", "state": "CA"},
            {"first_name": "Bob", "last_name": "Jones", "phone_number": "+15559990002", "state": "TX"}
        ]
    }
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return json_file


@pytest.mark.asyncio
async def test_import_csv_leads(temp_csv_leads, tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_campaign", name="Test Campaign", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    res = await importer.import_file("test_campaign", temp_csv_leads)
    assert res.total_rows == 3
    assert res.imported_count == 2  # 2 valid, 1 invalid
    assert res.failed_count == 1
    assert len(res.lead_ids) == 2

    # Check imported leads
    leads = await repository.query_campaign_leads({"campaign_id": "test_campaign"})
    assert len(leads) == 2
    phones = [l["phone_number"] for l in leads]
    assert "+15551234567" in phones
    assert "+15559876543" in phones  # normalized


@pytest.mark.asyncio
async def test_import_json_leads(temp_json_leads, tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_campaign_json", name="Test Campaign JSON", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    res = await importer.import_file("test_campaign_json", temp_json_leads)
    assert res.total_rows == 2
    assert res.imported_count == 2
    assert len(res.lead_ids) == 2


@pytest.mark.asyncio
async def test_invalid_phone_rejected(tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_camp", name="Test Campaign", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    row = {"phone_number": "abc", "first_name": "Test"}
    res = await importer.import_rows("test_camp", [row])
    assert res.failed_count == 1
    assert res.imported_count == 0


@pytest.mark.asyncio
async def test_duplicate_phone_skipped(tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_dup", name="Test Duplicate", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    row = {"phone_number": "+15554440001", "first_name": "First"}
    # First import
    res1 = await importer.import_rows("test_dup", [row])
    assert res1.imported_count == 1

    # Second import (duplicate phone)
    res2 = await importer.import_rows("test_dup", [row])
    assert res2.imported_count == 0
    assert res2.duplicate_count == 1


@pytest.mark.asyncio
async def test_phone_hash_created(tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_hash", name="Test Hash", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    row = {"phone_number": "+15554449999", "first_name": "HashTest"}
    res = await importer.import_rows("test_hash", [row])
    assert res.imported_count == 1

    leads = await repository.query_campaign_leads({"campaign_id": "test_hash"})
    assert leads[0]["metadata"].get("phone_hash") is not None


@pytest.mark.asyncio
async def test_dnc_or_wrong_number_suppressed(tmp_path):
    repository = Repository(data_dir=tmp_path)
    await repository.save_outbound_campaign(id="test_dnc_suppress", name="Test DNC Suppress", status="draft")
    importer = CampaignLeadImporter(repository=repository)

    # Put a phone number on DNC list
    phone = "+15553330000"
    await repository.save_dnc_request(phone_e164=phone, reason="Test DNC")

    row = {"phone_number": phone, "first_name": "DNC lead"}
    res = await importer.import_rows("test_dnc_suppress", [row])
    assert res.imported_count == 0
    assert res.suppressed_count == 1
