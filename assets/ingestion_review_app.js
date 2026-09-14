const result = document.getElementById("result");
const pageImage = document.getElementById("pageImage");
const clientLogo = document.getElementById("clientLogo");

function tokenHeaders() {
  const token = document.getElementById("token").value.trim();
  if (!token) {
    throw new Error("Paste a bearer token first.");
  }
  return {
    "Content-Type": "application/json",
    "Authorization": `Bearer ${token}`,
  };
}

async function callApi(operation, payload) {
  const response = await fetch(`/api/ingestion/${operation}`, {
    method: "POST",
    headers: tokenHeaders(),
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || `Request failed with ${response.status}`);
  }
  result.textContent = JSON.stringify(data, null, 2);
  return data;
}

function setSessionId(sessionId) {
  if (sessionId) {
    document.getElementById("sessionId").value = sessionId;
  }
}

async function fileToBase64(file) {
  const buffer = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

document.getElementById("logoFile").addEventListener("change", async () => {
  const file = document.getElementById("logoFile").files[0];
  if (!file) {
    clientLogo.removeAttribute("src");
    return;
  }
  clientLogo.src = URL.createObjectURL(file);
});

document.getElementById("createSession").addEventListener("click", async () => {
  try {
    const data = await callApi("create_ingestion_session", {
      idempotency_key: document.getElementById("createKey").value.trim(),
      expected_pages: Number(document.getElementById("expectedPages").value),
    });
    setSessionId(data.session_id);
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("listSessions").addEventListener("click", async () => {
  try {
    const data = await callApi("list_ingestion_sessions", {});
    setSessionId(data.sessions[0]?.session_id || "");
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("uploadPage").addEventListener("click", async () => {
  try {
    const file = document.getElementById("fileInput").files[0];
    if (!file) {
      throw new Error("Choose a PNG or JPEG file first.");
    }
    const data_base64 = await fileToBase64(file);
    await callApi("upload_ingestion_page", {
      session_id: document.getElementById("sessionId").value.trim(),
      idempotency_key: document.getElementById("uploadKey").value.trim(),
      page_number: Number(document.getElementById("pageNumber").value),
      mime_type: file.type,
      data_base64,
    });
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("grantReviewer").addEventListener("click", async () => {
  try {
    await callApi("grant_ingestion_session_reviewer", {
      session_id: document.getElementById("sessionId").value.trim(),
      idempotency_key: document.getElementById("grantKey").value.trim(),
      reviewer_subject: document.getElementById("reviewerSubject").value.trim(),
    });
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("submitProposal").addEventListener("click", async () => {
  try {
    await callApi("submit_record_proposal", {
      session_id: document.getElementById("sessionId").value.trim(),
      idempotency_key: document.getElementById("proposalKey").value.trim(),
      proposal: JSON.parse(document.getElementById("proposalJson").value),
    });
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("loadSummary").addEventListener("click", async () => {
  try {
    await callApi("get_ingestion_review_summary", {
      session_id: document.getElementById("sessionId").value.trim(),
    });
  } catch (error) {
    result.textContent = error.message;
  }
});

document.getElementById("loadPage").addEventListener("click", async () => {
  try {
    const data = await callApi("get_ingestion_page", {
      session_id: document.getElementById("sessionId").value.trim(),
      page_number: Number(document.getElementById("pageNumber").value),
    });
    pageImage.src = `data:${data.mime_type};base64,${data.data}`;
  } catch (error) {
    result.textContent = error.message;
  }
});
