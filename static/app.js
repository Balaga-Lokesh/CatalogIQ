// CatalogIQ frontend - plain JavaScript, no frameworks. Talks to the API with fetch.
//
// Data from the server is always inserted with textContent (never innerHTML),
// so a product title like "<script>" is shown as text, not run.

"use strict";

// Same list as app/schemas.py CATEGORIES.
const CATEGORIES = [
  "Groceries", "Beverages", "Personal Care", "Household",
  "Electronics", "Fashion", "Home & Kitchen", "Other",
];
const PAGE_SIZE = 20;
const POLL_MS = 1000;        // the brief: don't poll more than once a second
const SEARCH_DELAY_MS = 300; // wait for a pause in typing before searching

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// API helper: returns parsed JSON, or throws an Error with the server's message
// ---------------------------------------------------------------------------

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

// ---------------------------------------------------------------------------
// 1. CSV upload
// ---------------------------------------------------------------------------

// RFC 4180 CSV: fields may be "quoted", quoted fields may contain commas,
// newlines and doubled quotes (""). Returns an array of rows (arrays of strings).
function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;
  text = text.replace(/^﻿/, ""); // strip a byte-order mark (Excel adds one)

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"' && text[i + 1] === '"') { field += '"'; i++; }   // escaped quote
      else if (ch === '"') { inQuotes = false; }
      else { field += ch; }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(field); field = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;                   // CRLF
      row.push(field); rows.push(row); row = []; field = "";
    } else {
      field += ch;
    }
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ""));   // drop blank lines
}

// CSV rows -> [{sku, raw_title, raw_description}], or throws with a readable message.
function rowsToProducts(rows) {
  if (rows.length < 2) throw new Error("The CSV needs a header row and at least one listing.");
  const header = rows[0].map((h) => h.trim().toLowerCase());
  const col = (...names) => header.findIndex((h) => names.includes(h));
  const skuCol = col("sku");
  const titleCol = col("raw_title", "title");
  const descCol = col("raw_description", "description");
  if (skuCol < 0 || titleCol < 0) {
    throw new Error('The header must include "sku" and "raw_title" columns.');
  }

  return rows.slice(1).map((r, i) => {
    const line = i + 2; // +1 for the header, +1 because people count from 1
    const sku = (r[skuCol] || "").trim();
    const title = r[titleCol] || "";
    if (!sku) throw new Error(`Line ${line}: missing sku.`);
    if (!title.trim()) throw new Error(`Line ${line} (${sku}): missing raw_title.`);
    const description = descCol >= 0 ? r[descCol] || "" : "";
    return { sku, raw_title: title, raw_description: description.trim() ? description : null };
  });
}

function setMessage(el, text, isError = false) {
  el.textContent = text;
  el.classList.toggle("error", isError);
}

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("upload-message");
  const button = event.submitter;
  const file = $("csv-file").files[0];
  if (!file) return setMessage(message, "Choose a CSV file first.", true);

  button.disabled = true; // prevent a double submit
  try {
    const products = rowsToProducts(parseCsv(await file.text()));
    setMessage(message, `Sending ${products.length} listings...`);
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ products }) });
    setMessage(message, `Job ${job.id} started with ${job.total} listings.`);
    startTracking(job);
  } catch (err) {
    setMessage(message, err.message, true);
  } finally {
    button.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// 2. Live job progress
// ---------------------------------------------------------------------------

let pollTimer = null;
let pollCount = 0;

function startTracking(job) {
  clearTimeout(pollTimer);
  pollCount = 0;
  try { localStorage.setItem("catalogiq.lastJob", job.id); } catch { /* storage may be blocked */ }
  $("progress-section").hidden = false;
  $("job-announce").textContent = "";
  renderJob(job);
  pollTimer = setTimeout(() => poll(job.id), POLL_MS);
}

// setTimeout AFTER each response (not setInterval): never more than one
// request in flight, and at most one per second even if the server is slow.
async function poll(jobId) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    renderJob(job);
    pollCount++;
    if (job.status === "completed") {
      $("job-announce").textContent =
        `Job finished: ${job.done} of ${job.total} done, ${job.failed} failed, ${job.cache_hits} cache hits.`;
      loadProducts();
      return;
    }
    if (pollCount % 5 === 0) loadProducts(); // show new products appearing, every ~5 s
  } catch (err) {
    $("job-status").textContent = `could not refresh (${err.message}), retrying`;
  }
  pollTimer = setTimeout(() => poll(jobId), POLL_MS);
}

function renderJob(job) {
  $("job-id").textContent = job.id;
  $("job-status").textContent = job.status;
  $("job-progress").max = Math.max(job.total, 1);
  $("job-progress").value = job.done;
  $("count-done").textContent = job.done;
  $("count-total").textContent = job.total;
  $("count-failed").textContent = job.failed;
  $("count-cache").textContent = job.cache_hits;
}

// ---------------------------------------------------------------------------
// 3. Catalogue: pagination, category filter, debounced search
// ---------------------------------------------------------------------------

const state = { page: 1, category: "", q: "" };
let listController = null;

async function loadProducts() {
  // Abort the previous request: if an old, slow response arrived after a
  // newer one, it would overwrite the list with stale results.
  listController?.abort();
  listController = new AbortController();

  const params = new URLSearchParams({ page: state.page, page_size: PAGE_SIZE });
  if (state.category) params.set("category", state.category);
  if (state.q) params.set("q", state.q);

  try {
    const response = await fetch(`/api/products?${params}`, { signal: listController.signal });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Could not load products");
    renderProducts(data);
  } catch (err) {
    if (err.name === "AbortError") return; // replaced by a newer request
    $("results-summary").textContent = err.message;
  }
}

function renderProducts({ items, page, page_size, total }) {
  const pages = Math.max(1, Math.ceil(total / page_size));
  $("results-summary").textContent =
    total === 0 ? "No products match." : `${total} product${total === 1 ? "" : "s"}`;
  $("page-info").textContent = `Page ${page} of ${pages}`;
  $("prev-page").disabled = page <= 1;
  $("next-page").disabled = page >= pages;

  const list = $("product-list");
  list.replaceChildren(...items.map(productItem));
}

function productItem(product) {
  const li = document.createElement("li");
  const button = document.createElement("button");
  button.type = "button";
  button.className = "product-button";
  button.dataset.sku = product.sku;

  const title = document.createElement("span");
  title.className = "product-title";
  title.textContent = product.clean_title || product.raw_title;

  const meta = document.createElement("span");
  meta.className = "product-meta";
  meta.textContent = [product.sku, product.category, product.brand].filter(Boolean).join(" · ");

  const badge = document.createElement("span");
  badge.className = `badge ${product.status}`;
  badge.textContent = product.status;

  button.append(title, meta, badge);
  button.addEventListener("click", () => openReview(product.sku, button));
  li.append(button);
  return li;
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

$("search").addEventListener("input", debounce((event) => {
  state.q = event.target.value.trim();
  state.page = 1;
  loadProducts();
}, SEARCH_DELAY_MS));

$("category-filter").addEventListener("change", (event) => {
  state.category = event.target.value;
  state.page = 1;
  loadProducts();
});

$("filters").addEventListener("submit", (event) => event.preventDefault()); // Enter in search box

$("prev-page").addEventListener("click", () => { state.page--; loadProducts(); });
$("next-page").addEventListener("click", () => { state.page++; loadProducts(); });

// ---------------------------------------------------------------------------
// 4. Review a product (native <dialog>: focus is kept inside, Esc closes it)
// ---------------------------------------------------------------------------

let reviewing = null;   // SKU currently open
let returnFocusTo = null;

async function openReview(sku, opener) {
  returnFocusTo = opener;
  let product;
  try {
    product = await api(`/api/products/${encodeURIComponent(sku)}`);
  } catch (err) {
    $("results-summary").textContent = err.message;
    return;
  }
  reviewing = sku;
  $("review-sku").textContent = product.sku;
  $("review-status").textContent = product.status;
  $("review-status").className = `badge ${product.status}`;
  $("raw-title").textContent = product.raw_title;
  $("raw-description").textContent = product.raw_description || "(none)";
  $("review-brand").textContent = product.brand || "unknown";

  const error = $("review-error");
  error.hidden = !product.error;
  error.textContent = product.error ? `Enrichment failed: ${product.error}` : "";

  $("edit-title").value = product.clean_title || "";
  $("edit-category").value = product.category || "";
  $("edit-tags").value = (product.tags || []).join(", ");
  setMessage($("review-message"), "");
  $("review-dialog").showModal();
}

$("review-form").addEventListener("submit", async (event) => {
  if (event.submitter?.value !== "approve") return; // "Close" closes the dialog natively
  event.preventDefault();

  const tags = $("edit-tags").value.split(",").map((t) => t.trim()).filter(Boolean);
  const body = {
    clean_title: $("edit-title").value,
    category: $("edit-category").value,
    tags,
  };
  const button = $("approve-button");
  button.disabled = true;
  try {
    await api(`/api/products/${encodeURIComponent(reviewing)}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
    $("review-dialog").close();
    await loadProducts();
    // The list was re-rendered, so focus the NEW button for the same product.
    document.querySelector(`.product-button[data-sku="${CSS.escape(reviewing)}"]`)?.focus();
  } catch (err) {
    setMessage($("review-message"), err.message, true);
  } finally {
    button.disabled = false;
  }
});

$("review-dialog").addEventListener("close", () => {
  // Back to the product the user opened (if the list wasn't re-rendered meanwhile).
  if (returnFocusTo?.isConnected) returnFocusTo.focus();
});

// ---------------------------------------------------------------------------
// Start-up
// ---------------------------------------------------------------------------

function fillCategorySelects() {
  const filter = $("category-filter");
  const edit = $("edit-category");
  const placeholder = new Option("Choose a category", "");
  placeholder.disabled = true;
  edit.append(placeholder);
  for (const category of CATEGORIES) {
    filter.append(new Option(category, category));
    edit.append(new Option(category, category));
  }
}

async function init() {
  fillCategorySelects();
  loadProducts();
  try {
    const health = await api("/api/health");
    $("provider-info").textContent =
      `LLM provider: ${health.llm_provider} · up to ${health.llm_concurrency} calls at once`;
  } catch { /* the header line is optional */ }

  // Reloading the page keeps showing the last job's progress.
  let lastJob = null;
  try { lastJob = localStorage.getItem("catalogiq.lastJob"); } catch { /* ignore */ }
  if (lastJob) {
    try { startTracking(await api(`/api/jobs/${encodeURIComponent(lastJob)}`)); }
    catch { /* job no longer exists (e.g. new database) */ }
  }
}

init();
