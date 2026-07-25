const gateForm = document.getElementById("gate-form");
const passwordInput = document.getElementById("password-input");
const gateStatus = document.getElementById("gate-status");
const gate = document.getElementById("gate");
const uploader = document.getElementById("uploader");
const fileInput = document.getElementById("file-input");
const uploadButton = document.getElementById("upload-button");
const uploadStatus = document.getElementById("upload-status");

let adminPassword = null;

gateForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const password = passwordInput.value;
  if (!password) return;
  gateStatus.textContent = "Checking…";
  gateStatus.removeAttribute("data-state");
  try {
    const response = await fetch("/admin/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.ok) {
      adminPassword = password;
      gate.hidden = true;
      uploader.hidden = false;
    } else if (response.status === 401) {
      setStatus(gateStatus, "Incorrect password.", "error");
    } else if (response.status === 403) {
      setStatus(gateStatus, "Admin is not configured (set ADMIN_PASSWORD).", "error");
    } else {
      const body = await response.json().catch(() => ({}));
      setStatus(gateStatus, body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(gateStatus, err.message, "error");
  }
});

uploadButton.addEventListener("click", async () => {
  const file = fileInput.files[0];
  if (!file) {
    setStatus(uploadStatus, "Choose a PDF first.", "error");
    return;
  }
  if (!adminPassword) return;
  setStatus(uploadStatus, `Uploading and indexing ${file.name}…`, null);
  const formData = new FormData();
  formData.append("password", adminPassword);
  formData.append("file", file);
  try {
    const response = await fetch("/admin/train", { method: "POST", body: formData });
    let body;
    try {
      body = await response.json();
    } catch (parseErr) {
      body = { detail: `${response.status} ${response.statusText}` };
    }
    if (response.ok || response.status === 202) {
      setStatus(uploadStatus, `${file.name}: ${body.status}`, body.status === "indexed" ? "ok" : null);
      fileInput.value = "";
    } else {
      setStatus(uploadStatus, body.detail || response.statusText, "error");
    }
  } catch (err) {
    setStatus(uploadStatus, err.message, "error");
  }
});

function setStatus(el, text, state) {
  el.textContent = text;
  if (state) el.dataset.state = state;
  else delete el.dataset.state;
}
