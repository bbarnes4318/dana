# Telnyx DID Inventory Sync

This guide explains how to sync phone numbers owned/authorized in your Telnyx Mission Control Portal into Dana's central DID pool.

## Key Features

1. **Secure API Integration**: Retrieves phone numbers securely using `TELNYX_API_KEY` from the unified environment resolver (`get_runtime_env()`).
2. **Defensive Processing**: Automatically skips invalid E.164 formats, isolates provider namespaces, and ensures SignalWire or BulkVS numbers are not mixed into the Telnyx pool.
3. **Comprehensive Sync Logging**: Writes detailed sync reports (JSON and Markdown formats) containing fetched, updated, imported, and failed metrics to `data/telephony_reports/`.
4. **Per-number Call Caps**: Sets default hourly/daily caps on newly imported numbers.

---

## 1. Running the Sync via CLI

Run the sync script to pull all owned phone numbers from your Telnyx account:

```bash
python scripts/sync_telnyx_dids.py
```

### Options

* **Dry Run**: Print the summary of fetched numbers without writing to the database.
  ```bash
  python scripts/sync_telnyx_dids.py --dry-run
  ```
* **Cap Adjustments**: Customize default caps on imported DIDs.
  ```bash
  python scripts/sync_telnyx_dids.py --daily-cap 75 --hourly-cap 15
  ```
* **Initial Status**: Set numbers to import as paused.
  ```bash
  python scripts/sync_telnyx_dids.py --sync-status paused
  ```

---

## 2. Syncing via Web Console

1. Navigate to the **Telephony** tab on the Training Console.
2. Locate the **Sync Telnyx DID Inventory** section.
3. Set your preferred **Default Daily Cap** and **Default Hourly Cap**.
4. Check **Dry-run Sync** if you want to preview fetched metrics first.
5. Click **Sync Telnyx DIDs**.
6. The UI will display the fetched, imported, updated, and skipped metrics. The **DIDs Table** will automatically refresh to show the new pool.

---

## 3. Provider Rules & Isolation

* **Telnyx Only**: The sync engine strictly configures fetched numbers with `provider="telnyx"` and `verified_for_provider=true`.
* **No BulkVS Mix**: BulkVS numbers are strictly isolated. Do not run cross-provider DID pools unless `DANA_ALLOW_CROSS_PROVIDER_CALLER_ID=true` is set.
* **No SignalWire Mix**: SignalWire numbers are completely ignored when the active telephony provider is Telnyx.
