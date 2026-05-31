// Vanilla Javascript client side logic for Dana Training Operations Console
document.addEventListener("DOMContentLoaded", () => {
  // Select DOM Elements
  const tabs = document.querySelectorAll(".tab-btn");
  const tabContents = document.querySelectorAll(".tab-content");
  const logConsole = document.getElementById("log-console");
  const statusBox = document.getElementById("action-status-box");
  const statusTitle = document.getElementById("action-status-title");
  const statusText = document.getElementById("action-status-text");

  // Summary Metrics Elements
  const statPending = document.getElementById("stat-pending");
  const statReadiness = document.getElementById("stat-readiness");
  const statSources = document.getElementById("stat-sources");
  const statReviews = document.getElementById("stat-reviews");
  const statPrompts = document.getElementById("stat-prompts");
  const statCanaries = document.getElementById("stat-canaries");
  const statTracking = document.getElementById("stat-tracking");

  // Buttons & Forms
  const btnRefresh = document.getElementById("btn-refresh");
  const btnClearLogs = document.getElementById("btn-clear-logs");

  // Review Queue elements
  const btnListReviews = document.getElementById("btn-list-reviews");
  const filterStatus = document.getElementById("review-filter-status");
  const filterType = document.getElementById("review-filter-type");
  const filterLimit = document.getElementById("review-filter-limit");
  const reviewTbody = document.getElementById("review-items-tbody");
  const detailPlaceholder = document.getElementById("review-details-placeholder");
  const detailContainer = document.getElementById("review-details-container");
  const detailItemId = document.getElementById("detail-item-id");
  const detailItemType = document.getElementById("detail-item-type");
  const detailItemPayload = document.getElementById("detail-item-payload");
  const reviewerInput = document.getElementById("review-reviewer-input");
  const notesInput = document.getElementById("review-notes-input");
  const btnApprove = document.getElementById("btn-review-approve");
  const btnReject = document.getElementById("btn-review-reject");
  const btnChanges = document.getElementById("btn-review-changes");

  // Upload/Import forms
  const uploadForm = document.getElementById("upload-form");
  const uploadSourceType = document.getElementById("upload-source-type");
  const uploadFilePicker = document.getElementById("upload-file-picker");
  const youtubeForm = document.getElementById("youtube-import-form");
  const folderIntakeForm = document.getElementById("folder-intake-form");
  const dailyIntakeForm = document.getElementById("daily-intake-form");

  // Scheduler forms
  const schedulerForm = document.getElementById("scheduler-form");

  // Readiness elements
  const btnRunReadiness = document.getElementById("btn-run-readiness");
  const readinessResultCard = document.getElementById("readiness-result-card");
  const readinessBadge = document.getElementById("readiness-badge");
  const readinessId = document.getElementById("readiness-id");
  const readinessTotal = document.getElementById("readiness-checks-total");
  const readinessPassed = document.getElementById("readiness-checks-passed");
  const readinessFailed = document.getElementById("readiness-checks-failed");
  const remediationContainer = document.getElementById("remediation-container");
  const remediationList = document.getElementById("remediation-list");

  // Reports elements
  const btnListReports = document.getElementById("btn-list-reports");
  const reportFilterType = document.getElementById("reports-filter-type");
  const reportsTbody = document.getElementById("reports-tbody");
  const reportPlaceholder = document.getElementById("report-content-placeholder");
  const reportContainer = document.getElementById("report-content-container");
  const reportFilename = document.getElementById("report-filename");
  const reportContentText = document.getElementById("report-content-text");

  // Track currently selected review item
  let currentReviewItem = null;

  // Logging helpers
  function log(message, type = "info") {
    const timestamp = new Date().toLocaleTimeString();
    const entry = document.createElement("div");
    entry.className = `log-entry ${type}`;
    entry.innerText = `[${timestamp}] ${message}`;
    logConsole.appendChild(entry);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  // Clear log console
  btnClearLogs.addEventListener("click", () => {
    logConsole.innerHTML = '<div class="log-entry info">[System] Log cleared. Ready for console operations.</div>';
  });

  // Action status display helper
  function showStatus(title, text, isError = false) {
    statusBox.className = `action-box show ${isError ? 'action-error' : 'action-success'}`;
    statusTitle.innerText = title;
    statusText.innerText = typeof text === "object" ? JSON.stringify(text, null, 2) : text;
  }

  function hideStatus() {
    statusBox.className = "action-box";
  }

  // Handle Button Loading State
  function setButtonState(button, loading, originalText) {
    if (loading) {
      button.disabled = true;
      button.innerText = "⏳ Loading...";
    } else {
      button.disabled = false;
      button.innerText = originalText;
    }
  }

  // Tab Navigation
  tabs.forEach(tab => {
    tab.addEventListener("click", () => {
      // Toggle tabs UI active class
      tabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      // Toggle content active class
      const targetTab = tab.getAttribute("data-tab");
      tabContents.forEach(content => {
        content.classList.remove("active");
        if (content.id === targetTab) {
          content.classList.add("active");
        }
      });
      hideStatus();
      log(`Switched to tab: ${tab.innerText.trim()}`);
    });
  });

  // Fetch Summary Dashboard Stats
  async function refreshSummary() {
    const text = btnRefresh.innerText;
    setButtonState(btnRefresh, true, text);
    log("Requesting system-wide status summary...");

    try {
      const response = await fetch("/api/summary");
      const data = await response.json();
      
      statPending.innerText = data.pending_review_items;
      statSources.innerText = data.recent_training_sources;
      statReviews.innerText = data.recent_human_review_items;
      statPrompts.innerText = data.recent_prompt_versions;
      statCanaries.innerText = data.recent_canaries;
      statTracking.innerText = data.recent_tracking_records;

      // Style readiness badge in metrics
      statReadiness.innerText = data.readiness_status || "UNKNOWN";
      if (data.readiness_status === "PASS") {
        statReadiness.style.color = "var(--success)";
      } else if (data.readiness_status === "FAIL") {
        statReadiness.style.color = "var(--danger)";
      } else {
        statReadiness.style.color = "var(--text-secondary)";
      }

      log("Metrics refreshed successfully.", "success");
    } catch (error) {
      log(`Failed to fetch status metrics: ${error.message}`, "error");
    } finally {
      setButtonState(btnRefresh, false, text);
    }
  }

  btnRefresh.addEventListener("click", refreshSummary);
  // Auto refresh on load
  refreshSummary();

  // A. Review Queue - List items
  async function listReviews() {
    const statusVal = filterStatus.value;
    const typeVal = filterType.value;
    const limitVal = filterLimit.value;
    const btnText = btnListReviews.innerText;

    setButtonState(btnListReviews, true, btnText);
    log(`Listing human reviews (status: ${statusVal}, type: ${typeVal || "all"})...`);

    try {
      const url = `/api/review-items?status=${statusVal}&item_type=${typeVal}&limit=${limitVal}`;
      const response = await fetch(url);
      const data = await response.json();

      if (!data.success) {
        log(`Failed to retrieve review queue: ${data.message}`, "error");
        reviewTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--danger);">Error: ${data.message}</td></tr>`;
        return;
      }

      const items = data.data.items || [];
      if (items.length === 0) {
        reviewTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No matching reviews found.</td></tr>`;
        log("No review queue items found.");
        return;
      }

      reviewTbody.innerHTML = "";
      items.forEach(item => {
        const tr = document.createElement("tr");
        
        // Format preview text
        const payloadStr = JSON.stringify(item.payload || {});
        const preview = payloadStr.length > 50 ? payloadStr.substring(0, 50) + "..." : payloadStr;

        tr.innerHTML = `
          <td style="font-family: monospace; font-size:0.75rem;">${item.id.substring(0, 8)}...</td>
          <td>${item.item_type}</td>
          <td><span class="badge ${item.status === 'pending' ? 'badge-safety' : 'badge-alert'}">${item.status}</span></td>
          <td>${new Date(item.created_at).toLocaleString()}</td>
          <td><button class="btn btn-secondary btn-inspect" data-id="${item.id}" style="padding: 0.15rem 0.4rem; font-size:0.75rem; width:auto;">Inspect</button></td>
        `;
        reviewTbody.appendChild(tr);
      });

      // Bind inspect buttons
      document.querySelectorAll(".btn-inspect").forEach(btn => {
        btn.addEventListener("click", () => {
          const itemId = btn.getAttribute("data-id");
          inspectReviewItem(itemId);
        });
      });

      log(`Found ${items.length} reviews.`, "success");
    } catch (error) {
      log(`Error loading reviews: ${error.message}`, "error");
    } finally {
      setButtonState(btnListReviews, false, btnText);
    }
  }

  btnListReviews.addEventListener("click", listReviews);

  // A. Review Queue - Inspect details
  async function inspectReviewItem(itemId) {
    log(`Inspecting review item ${itemId}...`);
    try {
      const response = await fetch(`/api/review-items/${itemId}`);
      const data = await response.json();

      if (!data.success) {
        log(`Failed to show item: ${data.message}`, "error");
        return;
      }

      currentReviewItem = data.data.item;
      detailItemId.innerText = currentReviewItem.id;
      detailItemType.innerText = currentReviewItem.item_type;
      detailItemPayload.innerText = JSON.stringify(currentReviewItem.payload, null, 2);

      // Populate notes if available
      notesInput.value = currentReviewItem.review_notes || "";

      detailPlaceholder.style.display = "none";
      detailContainer.style.display = "block";
      log(`Review item ${itemId} loaded into detail view.`);
    } catch (error) {
      log(`Error inspecting item: ${error.message}`, "error");
    }
  }

  // A. Review Queue - Approval Actions (Approve, Reject, Needs Changes)
  async function submitReviewAction(action) {
    if (!currentReviewItem) return;

    const reviewer = reviewerInput.value.trim();
    const notes = notesInput.value.trim();

    if (!reviewer) {
      alert("Reviewer identity is strictly required for auditing!");
      reviewerInput.focus();
      return;
    }

    if (action !== "approve" && !notes) {
      alert("Review notes/reasons are strictly required for rejections or change requests!");
      notesInput.focus();
      return;
    }

    const payload = { reviewer, notes };
    log(`Submitting review action "${action}" on item ${currentReviewItem.id}...`);

    let button;
    let originalText;
    if (action === "approve") { button = btnApprove; originalText = "✅ Approve"; }
    else if (action === "reject") { button = btnReject; originalText = "❌ Reject"; }
    else { button = btnChanges; originalText = "⚠️ Needs Changes"; }

    setButtonState(button, true, originalText);

    try {
      const response = await fetch(`/api/review-items/${currentReviewItem.id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const resData = await response.json();

      if (!resData.success) {
        showStatus("Action Failed", resData.error || resData.message, true);
        log(`Action failed: ${resData.error || resData.message}`, "error");
      } else {
        showStatus("Review Action Saved", resData.message, false);
        log(`Review resolved with status: ${resData.data.new_status}`, "success");
        
        // Hide detail view and refresh lists
        detailContainer.style.display = "none";
        detailPlaceholder.style.display = "block";
        currentReviewItem = null;
        
        // Refresh UI
        listReviews();
        refreshSummary();
      }
    } catch (error) {
      showStatus("Server Communication Error", error.message, true);
      log(`Network error submitting action: ${error.message}`, "error");
    } finally {
      setButtonState(button, false, originalText);
    }
  }

  btnApprove.addEventListener("click", () => submitReviewAction("approve"));
  btnReject.addEventListener("click", () => submitReviewAction("reject"));
  btnChanges.addEventListener("click", () => submitReviewAction("needs-changes"));


  // B. Intake Form - File Upload
  uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const file = uploadFilePicker.files[0];
    const sourceType = uploadSourceType.value;
    const btn = document.getElementById("btn-upload-file");
    const text = btn.innerText;

    if (!file) {
      alert("Please select a file to upload.");
      return;
    }

    log(`Uploading file ${file.name} to category ${sourceType}...`);
    setButtonState(btn, true, text);

    const formData = new FormData();
    formData.append("source_type", sourceType);
    formData.append("file", file);

    try {
      const response = await fetch("/api/upload", {
        method: "POST",
        body: formData
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("Upload Failed", data.error || data.message, true);
        log(`Upload failed: ${data.error || data.message}`, "error");
      } else {
        showStatus("Upload Completed", data.message);
        log(`File imported to: ${data.data.path}`, "success");
        uploadForm.reset();
        refreshSummary();
      }
    } catch (error) {
      showStatus("Upload Connection Error", error.message, true);
      log(`Network error during file upload: ${error.message}`, "error");
    } finally {
      setButtonState(btn, false, text);
    }
  });


  // B. Intake Form - YouTube Import
  youtubeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = document.getElementById("youtube-title").value.trim();
    const url = document.getElementById("youtube-url").value.trim();
    const content = document.getElementById("youtube-content").value.trim();
    const runIntake = document.getElementById("youtube-run-intake").checked;
    
    const btn = document.getElementById("btn-youtube-import");
    const text = btn.innerText;

    log(`Importing YouTube transcript content titled "${title}"...`);
    setButtonState(btn, true, text);

    const payload = { title, content, source_url: url, run_intake, dry_run: false };

    try {
      const response = await fetch("/api/youtube/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("YouTube Import Failed", data.error || data.message, true);
        log(`YouTube import failed: ${data.error || data.message}`, "error");
      } else {
        showStatus("YouTube Import Completed", data.message);
        log(`Imported successfully. Created reviews.`, "success");
        youtubeForm.reset();
        refreshSummary();
      }
    } catch (error) {
      showStatus("YouTube Connection Error", error.message, true);
      log(`Network error during YouTube import: ${error.message}`, "error");
    } finally {
      setButtonState(btn, false, text);
    }
  });


  // B. Intake Form - Folder Ingestion
  folderIntakeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = document.getElementById("intake-folder-path").value.trim();
    const sourceType = document.getElementById("intake-source-type").value;
    const limit = document.getElementById("intake-limit").value;
    const dryRun = document.getElementById("intake-dry-run").checked;

    const btn = document.getElementById("btn-run-folder-intake");
    const text = btn.innerText;

    log(`Initiating folder intake on path "${path}"...`);
    setButtonState(btn, true, text);

    const payload = { path, source_type: sourceType || null, limit: limit ? parseInt(limit) : null, dry_run: dryRun };

    try {
      const response = await fetch("/api/intake/folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("Intake Failed", data.error || data.message, true);
        log(`Folder intake failed: ${data.error || data.message}`, "error");
      } else {
        showStatus("Folder Intake Completed", data.message);
        log(`Intake completed: ${JSON.stringify(data.data)}`, "success");
        refreshSummary();
      }
    } catch (error) {
      showStatus("Intake Connection Error", error.message, true);
      log(`Network error running folder intake: ${error.message}`, "error");
    } finally {
      setButtonState(btn, false, text);
    }
  });


  // B. Intake Form - Daily Scan
  dailyIntakeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const limit = document.getElementById("daily-limit").value;
    const dailyQa = document.getElementById("daily-qa-checkbox").checked;
    const dryRun = document.getElementById("daily-dry-run").checked;

    const btn = document.getElementById("btn-run-daily-intake");
    const text = btn.innerText;

    log("Running daily intake workflow scan...");
    setButtonState(btn, true, text);

    const payload = { daily_qa: dailyQa, limit: limit ? parseInt(limit) : null, dry_run: dryRun };

    try {
      const response = await fetch("/api/intake/daily", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("Daily Intake Failed", data.error || data.message, true);
        log(`Daily scan failed: ${data.error || data.message}`, "error");
      } else {
        showStatus("Daily Intake Successful", data.message);
        log(`Daily scan complete: ${JSON.stringify(data.data)}`, "success");
        refreshSummary();
      }
    } catch (error) {
      showStatus("Scan Connection Error", error.message, true);
      log(`Network error running daily scan: ${error.message}`, "error");
    } finally {
      setButtonState(btn, false, text);
    }
  });


  // C. Scheduler once run
  schedulerForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const limit = document.getElementById("sched-limit").value;
    const dailyQa = document.getElementById("sched-daily-qa").checked;
    const dryRun = document.getElementById("sched-dry-run").checked;

    const btn = document.getElementById("btn-run-scheduler");
    const text = btn.innerText;

    log("Triggering intake scheduler engine iteration...");
    setButtonState(btn, true, text);

    const payload = { daily_qa: dailyQa, limit: limit ? parseInt(limit) : null, dry_run: dryRun };

    try {
      const response = await fetch("/api/scheduler/once", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("Scheduler Execution Failed", data.error || data.message, true);
        log(`Scheduler loop failed: ${data.error || data.message}`, "error");
      } else {
        showStatus("Scheduler Iteration Finished", data.message);
        log(`Scheduler completed run ID: ${data.data.scheduler_run_id}`, "success");
        refreshSummary();
      }
    } catch (error) {
      showStatus("Scheduler Connection Error", error.message, true);
      log(`Network error triggering scheduler: ${error.message}`, "error");
    } finally {
      setButtonState(btn, false, text);
    }
  });


  // D. Readiness Auditor - run scan
  btnRunReadiness.addEventListener("click", async () => {
    const strict = document.getElementById("readiness-strict").checked;
    const failOnMedium = document.getElementById("readiness-fail-medium").checked;
    const btnText = btnRunReadiness.innerText;

    log("Starting continuous training readiness audit scan...");
    setButtonState(btnRunReadiness, true, btnText);
    readinessResultCard.style.display = "none";

    const payload = { strict, fail_on_medium: failOnMedium };

    try {
      const response = await fetch("/api/readiness", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const data = await response.json();

      if (!data.success) {
        showStatus("Readiness Audit Crashed", data.error || data.message, true);
        log(`Audit crashed: ${data.error || data.message}`, "error");
        return;
      }

      const res = data.data;
      readinessId.innerText = res.readiness_id;
      readinessTotal.innerText = res.total_checks;
      readinessPassed.innerText = res.checks_passed;
      readinessFailed.innerText = res.checks_failed;

      // Style badge status
      if (res.passed) {
        readinessBadge.className = "badge badge-safety";
        readinessBadge.innerText = "PASS";
        log("Readiness Audit result: PASS", "success");
      } else {
        readinessBadge.className = "badge badge-alert";
        readinessBadge.innerText = "FAIL";
        log("Readiness Audit result: FAIL", "error");
      }

      // Populate remediations
      const items = res.remediation_items || [];
      if (items.length > 0) {
        remediationList.innerHTML = "";
        items.forEach(item => {
          const li = document.createElement("li");
          li.innerText = item;
          remediationList.appendChild(li);
        });
        remediationContainer.style.display = "block";
      } else {
        remediationContainer.style.display = "none";
      }

      readinessResultCard.style.display = "block";
      refreshSummary();
    } catch (error) {
      log(`Network error running readiness checks: ${error.message}`, "error");
    } finally {
      setButtonState(btnRunReadiness, false, btnText);
    }
  });


  // E. Reports - list logs
  async function listReports() {
    const rtype = reportFilterType.value;
    const btnText = btnListReports.innerText;

    setButtonState(btnListReports, true, btnText);
    log(`Querying generated reports (category: ${rtype || "all"})...`);

    try {
      const url = `/api/reports?type=${rtype}&limit=50`;
      const response = await fetch(url);
      const data = await response.json();

      if (!data.success) {
        log(`Failed to list reports: ${data.message}`, "error");
        reportsTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--danger);">Error: ${data.message}</td></tr>`;
        return;
      }

      const files = data.data.reports || [];
      if (files.length === 0) {
        reportsTbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--text-muted);">No reports found in directories.</td></tr>`;
        return;
      }

      reportsTbody.innerHTML = "";
      files.forEach(file => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${file.name}</td>
          <td>${file.type}</td>
          <td>${(file.size_bytes / 1024).toFixed(1)} KB</td>
          <td>${new Date(file.modified_at).toLocaleString()}</td>
          <td><button class="btn btn-secondary btn-read-report" data-path="${file.path}" style="padding: 0.15rem 0.4rem; font-size:0.75rem; width:auto;">View</button></td>
        `;
        reportsTbody.appendChild(tr);
      });

      // Bind report click event
      document.querySelectorAll(".btn-read-report").forEach(btn => {
        btn.addEventListener("click", () => {
          const rpath = btn.getAttribute("data-path");
          viewReportContent(rpath);
        });
      });

      log(`Found ${files.length} reports.`, "success");
    } catch (error) {
      log(`Network error listing reports: ${error.message}`, "error");
    } finally {
      setButtonState(btnListReports, false, btnText);
    }
  }

  btnListReports.addEventListener("click", listReports);

  // E. Reports - View content
  async function viewReportContent(reportPath) {
    log(`Reading report content at path ${reportPath}...`);
    try {
      const response = await fetch(`/api/report?path=${encodeURIComponent(reportPath)}`);
      const data = await response.json();

      if (!data.success) {
        log(`Failed to read report: ${data.message}`, "error");
        return;
      }

      reportFilename.innerText = reportPath;
      reportContentText.innerText = data.data.content;

      reportPlaceholder.style.display = "none";
      reportContainer.style.display = "block";
      log("Report loaded into view panel.");
    } catch (error) {
      log(`Error viewing report: ${error.message}`, "error");
    }
  }

  // =========================================================================
  // Advanced Training Workflow Event Listeners (Prompt 27)
  // =========================================================================

  // 1. QA & Evals Tab
  const qaMinerForm = document.getElementById("qa-miner-form");
  if (qaMinerForm) {
    qaMinerForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const date = document.getElementById("qa-date").value.trim();
      const date_from = document.getElementById("qa-date-from").value.trim();
      const date_to = document.getElementById("qa-date-to").value.trim();
      const limit = document.getElementById("qa-limit").value;
      const dry_run = document.getElementById("qa-dry-run").checked;
      const btn = document.getElementById("btn-run-qa-miner");
      const text = btn.innerText;

      log("Running QA Miner...");
      setButtonState(btn, true, text);

      const payload = {
        date: date || null,
        date_from: date_from || null,
        date_to: date_to || null,
        limit: limit ? parseInt(limit) : null,
        dry_run
      };

      try {
        const response = await fetch("/api/qa/daily", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("QA Miner Finished", data);
          log("QA Miner completed successfully.", "success");
          refreshSummary();
        } else {
          showStatus("QA Miner Failed", data.error || data.message, true);
          log(`QA Miner failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error running QA Miner: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const evalRunnerForm = document.getElementById("eval-runner-form");
  if (evalRunnerForm) {
    evalRunnerForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const case_id = document.getElementById("eval-case-id").value.trim();
      const stage = document.getElementById("eval-stage").value.trim();
      const objection = document.getElementById("eval-objection").value.trim();
      const limit = document.getElementById("eval-limit").value;
      const btn = document.getElementById("btn-run-evals");
      const text = btn.innerText;

      log("Running Eval Cases...");
      setButtonState(btn, true, text);

      const payload = {
        case_id: case_id || null,
        stage: stage || null,
        objection: objection || null,
        limit: limit ? parseInt(limit) : null
      };

      try {
        const response = await fetch("/api/evals/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Eval Run Finished", data);
          log("Eval cases run completed successfully.", "success");
          refreshSummary();
        } else {
          showStatus("Eval Run Failed", data.error || data.message, true);
          log(`Eval cases run failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error running evals: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const replayForm = document.getElementById("replay-form");
  if (replayForm) {
    replayForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const fixture = document.getElementById("replay-fixture").value.trim();
      const fixture_dir = document.getElementById("replay-dir").value.trim();
      const mode = document.getElementById("replay-mode").value;
      const fail_fast = document.getElementById("replay-fail-fast").checked;
      const btn = document.getElementById("btn-run-replay");
      const text = btn.innerText;

      log("Running Transcript Replay...");
      setButtonState(btn, true, text);

      const payload = {
        fixture: fixture || null,
        fixture_dir: fixture_dir || null,
        mode,
        fail_fast
      };

      try {
        const response = await fetch("/api/replay/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Replay Finished", data);
          log("Transcript replay completed successfully.", "success");
          refreshSummary();
        } else {
          showStatus("Replay Failed", data.error || data.message, true);
          log(`Transcript replay failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error running replays: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const simulationForm = document.getElementById("simulation-form");
  if (simulationForm) {
    simulationForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const persona = document.getElementById("sim-persona").value.trim();
      const run_all = document.getElementById("sim-run-all").checked;
      const btn = document.getElementById("btn-run-simulations");
      const text = btn.innerText;

      log("Running Prospect Simulations...");
      setButtonState(btn, true, text);

      const payload = {
        persona: persona || null,
        run_all
      };

      try {
        const response = await fetch("/api/simulations/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Simulations Finished", data);
          log("Simulations completed successfully.", "success");
          refreshSummary();
        } else {
          showStatus("Simulations Failed", data.error || data.message, true);
          log(`Simulations failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error running simulations: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  // 2. Prompt Improvements Tab
  const patchGenerateForm = document.getElementById("patch-generate-form");
  if (patchGenerateForm) {
    patchGenerateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const limit = document.getElementById("patch-limit").value;
      const dry_run = document.getElementById("patch-dry-run").checked;
      const btn = document.getElementById("btn-generate-patches");
      const text = btn.innerText;

      log("Generating prompt patch candidates...");
      setButtonState(btn, true, text);

      const payload = {
        limit: limit ? parseInt(limit) : null,
        dry_run
      };

      try {
        const response = await fetch("/api/prompt/patches/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Patches Generated", data);
          log("Patch candidates generated successfully.", "success");
          refreshSummary();
        } else {
          showStatus("Generation Failed", data.error || data.message, true);
          log(`Patch generation failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error generating patches: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const patchPreviewForm = document.getElementById("patch-preview-form");
  if (patchPreviewForm) {
    patchPreviewForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const patch_id = document.getElementById("preview-patch-id").value.trim();
      const create_candidate_version = document.getElementById("preview-create-version").checked;
      const skip_gates = document.getElementById("preview-skip-gates").checked;
      const btn = document.getElementById("btn-preview-patches");
      const text = btn.innerText;

      log("Running patch preview and verification gates...");
      setButtonState(btn, true, text);

      const payload = {
        patch_id: patch_id || null,
        approved_only: true,
        create_candidate_version,
        skip_gates
      };

      try {
        const response = await fetch("/api/prompt/patches/preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Preview Finished", data);
          log("Preview and gating validation finished.", "success");
          refreshSummary();
        } else {
          showStatus("Preview Failed", data.error || data.message, true);
          log(`Preview failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error running preview: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const btnListPromptVersions = document.getElementById("btn-list-prompt-versions");
  const promptVersionsTbody = document.getElementById("prompt-versions-tbody");
  if (btnListPromptVersions) {
    btnListPromptVersions.addEventListener("click", async () => {
      const text = btnListPromptVersions.innerText;
      log("Listing prompt versions...");
      setButtonState(btnListPromptVersions, true, text);

      try {
        const response = await fetch("/api/prompt/versions?limit=50");
        const data = await response.json();
        if (response.ok && data.success) {
          const versions = data.data.versions || [];
          if (versions.length === 0) {
            promptVersionsTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No prompt versions found.</td></tr>`;
          } else {
            promptVersionsTbody.innerHTML = "";
            versions.forEach(v => {
              const tr = document.createElement("tr");
              tr.innerHTML = `
                <td style="font-family: monospace; font-size:0.75rem;">${v.id}</td>
                <td>${v.file_path}</td>
                <td>${v.created_by}</td>
                <td>${new Date(v.created_at).toLocaleString()}</td>
                <td>${v.change_reason}</td>
                <td><span class="badge badge-safety">${v.canary_status}</span></td>
              `;
              promptVersionsTbody.appendChild(tr);
            });
          }
          log(`Loaded ${versions.length} versions.`, "success");
        } else {
          log(`Failed to list versions: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        log(`Network error listing versions: ${error.message}`, "error");
      } finally {
        setButtonState(btnListPromptVersions, false, text);
      }
    });
  }

  // 3. Canary Tab
  const canaryCreateForm = document.getElementById("canary-create-form");
  if (canaryCreateForm) {
    canaryCreateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const prompt_version_id = document.getElementById("canary-candidate-id").value.trim();
      const traffic_percent = document.getElementById("canary-traffic").value;
      const operator = document.getElementById("canary-operator").value.trim();
      const notes = document.getElementById("canary-notes").value.trim();
      const btn = document.getElementById("btn-create-canary-plan");
      const text = btn.innerText;

      log("Creating canary rollout plan...");
      setButtonState(btn, true, text);

      const payload = {
        prompt_version_id,
        traffic_percent: parseFloat(traffic_percent),
        operator,
        notes: notes || null
      };

      try {
        const response = await fetch("/api/canary/create", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Canary Plan Created", data);
          log("Canary rollout plan created successfully.", "success");
          refreshSummary();
          listCanaries();
        } else {
          showStatus("Canary Plan Failed", data.error || data.message, true);
          log(`Canary plan failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error creating canary: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const btnCheckCanaryCandidate = document.getElementById("btn-check-canary-candidate");
  if (btnCheckCanaryCandidate) {
    btnCheckCanaryCandidate.addEventListener("click", async () => {
      const prompt_version_id = document.getElementById("canary-candidate-id").value.trim();
      if (!prompt_version_id) {
        alert("PromptVersion ID is required.");
        return;
      }
      const text = btnCheckCanaryCandidate.innerText;
      log(`Checking eligibility for version ${prompt_version_id}...`);
      setButtonState(btnCheckCanaryCandidate, true, text);

      try {
        const response = await fetch("/api/canary/check-candidate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ prompt_version_id })
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Eligibility Checked", data);
          log("Eligibility check completed.", "success");
        } else {
          showStatus("Check Failed", data.error || data.message, true);
          log(`Check failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btnCheckCanaryCandidate, false, text);
      }
    });
  }

  window.triggerCanaryAction = async function(action) {
    const experimentId = document.getElementById("canary-experiment-id").value.trim();
    const operator = document.getElementById("canary-action-operator").value.trim();
    const notes = document.getElementById("canary-action-notes").value.trim();

    if (!experimentId) {
      alert("Experiment ID is required.");
      return;
    }
    if (!operator) {
      alert("Operator identity is required.");
      return;
    }
    if ((action === "rollback" || action === "cancel") && !notes) {
      alert("Notes/Reason is strictly required for rollback or cancellation!");
      return;
    }

    log(`Triggering canary action: ${action}...`);
    try {
      const response = await fetch(`/api/canary/${experimentId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ operator, notes, reason: notes })
      });
      const data = await response.json();
      if (response.ok) {
        showStatus(`Canary ${action} Successful`, data);
        log(`Canary action ${action} succeeded.`, "success");
        refreshSummary();
        listCanaries();
      } else {
        showStatus(`Canary ${action} Failed`, data.error || data.message, true);
        log(`Canary action ${action} failed: ${data.error || data.message}`, "error");
      }
    } catch (error) {
      showStatus("Connection Error", error.message, true);
      log(`Network error running canary action: ${error.message}`, "error");
    }
  };

  const btnMonitorCanary = document.getElementById("btn-monitor-canary");
  if (btnMonitorCanary) {
    btnMonitorCanary.addEventListener("click", async () => {
      const experiment_id = document.getElementById("canary-experiment-id").value.trim();
      const text = btnMonitorCanary.innerText;
      log("Running canary monitoring check...");
      setButtonState(btnMonitorCanary, true, text);

      try {
        const response = await fetch("/api/canary/monitor", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ experiment_id: experiment_id || null })
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Monitoring Complete", data);
          log("Canary monitoring report generated.", "success");
        } else {
          showStatus("Monitoring Failed", data.error || data.message, true);
          log(`Monitoring failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btnMonitorCanary, false, text);
      }
    });
  }

  const btnListCanaries = document.getElementById("btn-list-canaries");
  const canaryTbody = document.getElementById("canary-tbody");
  async function listCanaries() {
    const text = btnListCanaries.innerText;
    log("Listing canary experiments...");
    setButtonState(btnListCanaries, true, text);

    try {
      const response = await fetch("/api/canary/list?limit=50");
      const data = await response.json();
      if (response.ok && data.success) {
        const experiments = data.data.canaries || [];
        if (experiments.length === 0) {
          canaryTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No canary experiments found.</td></tr>`;
        } else {
          canaryTbody.innerHTML = "";
          experiments.forEach(e => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td style="font-family: monospace; font-size:0.75rem;">${e.id}</td>
              <td>${e.experiment_name}</td>
              <td style="font-family: monospace; font-size:0.75rem;">${e.prompt_version_id}</td>
              <td>${e.traffic_percent}%</td>
              <td><span class="badge ${e.status === 'running' || e.status === 'active' ? 'badge-safety' : 'badge-alert'}">${e.status}</span></td>
              <td><button class="btn btn-secondary" onclick="document.getElementById('canary-experiment-id').value = '${e.id}'; log('Selected experiment ${e.id} for control.');" style="padding: 0.15rem 0.4rem; font-size:0.75rem; width:auto;">Select</button></td>
            `;
            canaryTbody.appendChild(tr);
          });
        }
        log(`Loaded ${experiments.length} canary experiments.`, "success");
      } else {
        log(`Failed to list canaries: ${data.error || data.message}`, "error");
      }
    } catch (error) {
      log(`Network error listing canaries: ${error.message}`, "error");
    } finally {
      setButtonState(btnListCanaries, false, text);
    }
  }
  if (btnListCanaries) {
    btnListCanaries.addEventListener("click", listCanaries);
  }

  // 4. Fine-Tune Tab
  const ftExportForm = document.getElementById("ft-export-form");
  if (ftExportForm) {
    ftExportForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const limit = document.getElementById("ft-export-limit").value;
      const stage = document.getElementById("ft-export-stage").value.trim();
      const objection = document.getElementById("ft-export-objection").value.trim();
      const dry_run = document.getElementById("ft-export-dry-run").checked;
      const btn = document.getElementById("btn-ft-export");
      const text = btn.innerText;

      log("Exporting fine-tuning dataset...");
      setButtonState(btn, true, text);

      const payload = {
        limit: limit ? parseInt(limit) : null,
        stage: stage || null,
        objection: objection || null,
        dry_run
      };

      try {
        const response = await fetch("/api/fine-tune/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Export Finished", data);
          log("Dataset exported successfully.", "success");
          refreshSummary();
        } else {
          showStatus("Export Failed", data.error || data.message, true);
          log(`Export failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const ftGateForm = document.getElementById("ft-gate-form");
  if (ftGateForm) {
    ftGateForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const dataset_path = document.getElementById("ft-gate-path").value.trim();
      const strict = document.getElementById("ft-gate-strict").checked;
      const btn = document.getElementById("btn-ft-gate");
      const text = btn.innerText;

      log("Executing compliance quality gate scan...");
      setButtonState(btn, true, text);

      try {
        const response = await fetch("/api/fine-tune/gate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dataset_path, strict })
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Gate Check Completed", data);
          log("Gate scan completed successfully.", "success");
        } else {
          showStatus("Gate Check Failed", data.error || data.message, true);
          log(`Gate scan failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const ftRequestForm = document.getElementById("ft-request-form");
  if (ftRequestForm) {
    ftRequestForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const dataset_path = document.getElementById("ft-req-dataset").value.trim();
      const gate_report_path = document.getElementById("ft-req-gate").value.trim();
      const provider = document.getElementById("ft-req-provider").value;
      const dry_run = document.getElementById("ft-req-dry-run").checked;
      const btn = document.getElementById("btn-ft-request");
      const text = btn.innerText;

      log("Preparing job request package...");
      setButtonState(btn, true, text);

      const payload = {
        dataset_path,
        gate_report_path: gate_report_path || null,
        provider,
        dry_run
      };

      try {
        const response = await fetch("/api/fine-tune/job-request", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Job Configuration Prepared", data);
          log("Job request package built successfully.", "success");
        } else {
          showStatus("Preparation Failed", data.error || data.message, true);
          log(`Preparation failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const ftTrackForm = document.getElementById("ft-track-form");
  if (ftTrackForm) {
    ftTrackForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const job_request_id = document.getElementById("ft-track-id").value.trim();
      const status = document.getElementById("ft-track-status").value;
      const operator = document.getElementById("ft-track-operator").value.trim();
      const provider_job_id = document.getElementById("ft-track-provider-job-id").value.trim();
      const notes = document.getElementById("ft-track-notes").value.trim();
      const btn = document.getElementById("btn-ft-track");
      const text = btn.innerText;

      log("Saving job tracking record...");
      setButtonState(btn, true, text);

      const payload = {
        job_request_id,
        status,
        operator,
        provider_job_id: provider_job_id || null,
        notes: notes || null
      };

      try {
        const response = await fetch("/api/fine-tune/track", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Tracking Saved", data);
          log("Tracking record saved successfully.", "success");
          refreshSummary();
          listFTTracking();
        } else {
          showStatus("Saving Failed", data.error || data.message, true);
          log(`Saving failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

  const btnListFTTracking = document.getElementById("btn-list-ft-tracking");
  const ftTrackingTbody = document.getElementById("ft-tracking-tbody");
  async function listFTTracking() {
    const text = btnListFTTracking.innerText;
    log("Listing tracking logs...");
    setButtonState(btnListFTTracking, true, text);

    try {
      const response = await fetch("/api/fine-tune/tracking?limit=50");
      const data = await response.json();
      if (response.ok && data.success) {
        const records = data.data.records || [];
        if (records.length === 0) {
          ftTrackingTbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted);">No tracking logs found.</td></tr>`;
        } else {
          ftTrackingTbody.innerHTML = "";
          records.forEach(r => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
              <td style="font-family: monospace; font-size:0.75rem;">${r.id || r.request_id || '—'}</td>
              <td><span class="badge badge-safety">${r.status}</span></td>
              <td>${r.operator || r.actor || '—'}</td>
              <td>${r.provider_job_id || '—'}</td>
              <td>${r.notes || r.reason || '—'}</td>
              <td>${r.updated_at ? new Date(r.updated_at).toLocaleString() : '—'}</td>
            `;
            ftTrackingTbody.appendChild(tr);
          });
        }
        log(`Loaded ${records.length} tracking logs.`, "success");
      } else {
        log(`Failed to list tracking: ${data.error || data.message}`, "error");
      }
    } catch (error) {
      log(`Network error listing tracking: ${error.message}`, "error");
    } finally {
      setButtonState(btnListFTTracking, false, text);
    }
  }
  if (btnListFTTracking) {
    btnListFTTracking.addEventListener("click", listFTTracking);
  }

  // 5. Post-Call Tab
  const postcallForm = document.getElementById("postcall-form");
  if (postcallForm) {
    postcallForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const payloadStr = document.getElementById("postcall-payload").value.trim();
      const enabled = document.getElementById("postcall-enabled").checked;
      const run_intake = document.getElementById("postcall-intake").checked;
      const dry_run = document.getElementById("postcall-dry-run").checked;
      const btn = document.getElementById("btn-postcall-export");
      const text = btn.innerText;

      let payload;
      try {
        payload = JSON.parse(payloadStr);
      } catch (err) {
        alert("Completed Call Payload must be valid JSON!");
        return;
      }

      log("Running completed call export validation...");
      setButtonState(btn, true, text);

      try {
        const response = await fetch("/api/post-call/export", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ payload, enabled, run_intake, dry_run })
        });
        const data = await response.json();
        if (response.ok) {
          showStatus("Export Validated", data);
          log("Post-call export validation completed.", "success");
          refreshSummary();
        } else {
          showStatus("Validation Failed", data.error || data.message, true);
          log(`Validation failed: ${data.error || data.message}`, "error");
        }
      } catch (error) {
        showStatus("Connection Error", error.message, true);
        log(`Network error validating post-call: ${error.message}`, "error");
      } finally {
        setButtonState(btn, false, text);
      }
    });
  }

});
