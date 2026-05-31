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

});
