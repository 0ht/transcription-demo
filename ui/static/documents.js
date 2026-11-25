let selectedTranscriptPath = null;  
let selectedDetail = null;  
let previousUploadStates = new Map();
let uploadRefresh = null;

async function loadSourceUploads() {
  if (uploadRefresh) return uploadRefresh;
  uploadRefresh = (async () => {
    const data = await fetchJSON("/api/documents/uploads");
    const items = data.items || [];
    const box = document.getElementById("sourceUploadList");
    box.replaceChildren();
    let completed = false;
    for (const item of items) {
      const row = document.createElement("div");
      row.className = "history-item";
      const name = document.createElement("strong");
      name.textContent = item.name;
      row.appendChild(name);
      row.appendChild(createProcessingStatus(item));
      if (item.status === "completed" && item.result_blob_name) {
        row.onclick = () => {
          selectedTranscriptPath = item.result_blob_name;
          loadDocumentDetail(item.result_blob_name);
        };
      }
      box.appendChild(row);
      if (item.status === "completed" && previousUploadStates.get(item.path) !== "completed") {
        completed = true;
      }
    }
    if (!items.length) box.textContent = "アップロード済みファイルはありません";
    previousUploadStates = new Map(items.map(item => [item.path, item.status]));
    document.getElementById("sourceUploadMessage").textContent = "5秒ごとに自動更新";
    if (completed) await loadDocuments();
  })();
  try {
    await uploadRefresh;
  } finally {
    uploadRefresh = null;
  }
}

function showUploadStatusError(error) {
  document.getElementById("sourceUploadMessage").textContent = `ステータス取得失敗: ${error.message}`;
}
  
function todayStr() {  
  const d = new Date();  
  return d.toISOString().slice(0, 10);  
}  
  
function daysAgoStr(days) {  
  const d = new Date();  
  d.setDate(d.getDate() - days);  
  return d.toISOString().slice(0, 10);  
}  
  
async function fetchJSON(url, options = {}) {  
  const res = await fetch(url, options);  
  if (!res.ok) {  
    let msg = `HTTP ${res.status}`;  
    try {  
      const data = await res.json();  
      msg = data.detail || msg;  
    } catch {  
      msg = await res.text() || msg;  
    }  
    throw new Error(msg);  
  }  
  return await res.json();  
}  
  
function escapeHtml(text) {  
  return (text || "")  
    .replaceAll("&", "&amp;")  
    .replaceAll("<", "&lt;")  
    .replaceAll(">", "&gt;");  
}  
  
function setLoadingMessage(msg) {  
  document.getElementById("documentsStatus").textContent = msg;  
}  
  
async function loadDocuments() {  
  const dateFrom = document.getElementById("dateFrom").value;  
  const dateTo = document.getElementById("dateTo").value;  
  const keyword = document.getElementById("keywordInput").value || "";  
  
  setLoadingMessage("文書一覧を読み込み中...");  
  const qs = new URLSearchParams({  
    date_from: dateFrom,  
    date_to: dateTo,  
    keyword: keyword,  
  });  
  
  try {  
    const data = await fetchJSON(`/api/documents?${qs.toString()}`);  
    renderDocumentList(data.items || []);  
    setLoadingMessage(`一覧読み込み完了: ${data.items?.length || 0} 件`);  
  } catch (e) {  
    setLoadingMessage(`一覧取得失敗: ${e.message}`);  
    alert(e.message);  
  }  
}  
  
function renderDocumentList(items) {  
  const box = document.getElementById("documentList");  
  box.innerHTML = "";  
  
  if (!items.length) {  
    box.innerHTML = `<div class="history-item">該当文書なし</div>`;  
    return;  
  }  
  
  for (const item of items) {  
    const div = document.createElement("div");  
    div.className = "history-item";  
    if (item.path === selectedTranscriptPath) {  
      div.classList.add("active");  
    }  
  
    div.innerHTML = `  
      <div><strong>${escapeHtml(item.name)}</strong></div>  
      <div class="small">${escapeHtml(item.date || "")} / ✅ 処理済み</div>  
    `;  
  
    div.onclick = async () => {  
      selectedTranscriptPath = item.path;  
      document.querySelectorAll("#documentList .history-item").forEach(x => x.classList.remove("active"));  
      div.classList.add("active");  
      await loadDocumentDetail(item.path);  
    };  
  
    box.appendChild(div);  
  }  
}  
  
async function loadDocumentDetail(transcriptPath) {  
  setLoadingMessage("詳細を読み込み中...");  
  try {  
    const qs = new URLSearchParams({ transcript_path: transcriptPath });  
    const detail = await fetchJSON(`/api/documents/detail?${qs.toString()}`);  
    selectedDetail = detail;  
    renderDetail(detail);  
    setLoadingMessage("詳細読み込み完了");  
  } catch (e) {  
    setLoadingMessage(`詳細取得失敗: ${e.message}`);  
    alert(e.message);  
  }  
}  
  
function renderDetail(detail) {  
  document.getElementById("detailEmpty").style.display = "none";  
  document.getElementById("detailPanel").style.display = "block";  
  
  document.getElementById("documentTitle").textContent = detail.source_file || detail.transcript_path;  
  document.getElementById("documentMeta").textContent =  
    `処理日時: ${detail.processed_at || "-"} / 長さ: ${detail.duration || "-"} / 言語: ${detail.language || "-"}`;  
  
  document.getElementById("transcriptPreview").value = detail.txt_data || detail.transcript_text || "";  
  
  document.getElementById("downloadJsonBtn").disabled = false;  
  document.getElementById("downloadTextBtn").disabled = false;  
  
  const mediaWrap = document.getElementById("mediaWrap");  
  mediaWrap.innerHTML = "";  
  
  if (detail.media_path && (detail.is_audio || detail.is_video)) {
    const qs = new URLSearchParams({ media_path: detail.media_path });  
    const url = `/api/documents/media?${qs.toString()}`;  
    const player = document.createElement(detail.is_audio ? "audio" : "video");
    player.controls = true;
    player.preload = "metadata";
    player.style.width = "100%";
    if (detail.is_video) player.style.maxHeight = "320px";
    const errorMessage = document.createElement("div");
    errorMessage.className = "processing-error";
    errorMessage.setAttribute("role", "alert");
    errorMessage.hidden = true;
    player.addEventListener("error", () => {
      const messages = {
        1: "メディアの読み込みが中断されました。",
        2: "元ファイルの取得中に通信エラーが発生しました。",
        3: "メディアをデコードできません。このブラウザーがコーデックに対応していない可能性があります。",
        4: "このブラウザーが動画・音声形式に対応していないか、元ファイルを読み込めません。",
      };
      errorMessage.textContent = (messages[player.error?.code] || "メディアを再生できません。") +
        " 元ファイルをダウンロードし、対応するブラウザーやプレーヤーで再生してください。";
      errorMessage.hidden = false;
    });
    player.addEventListener("loadedmetadata", () => {
      errorMessage.textContent = "";
      errorMessage.hidden = true;
    });
    player.src = url;
    const downloadLink = document.createElement("a");
    downloadLink.href = `${url}&download=true`;
    downloadLink.textContent = "元ファイルをダウンロード";
    downloadLink.download = (detail.source_file || detail.media_path).split("/").pop();
    const downloadRow = document.createElement("div");
    downloadRow.className = "button-row";
    downloadRow.appendChild(downloadLink);
    mediaWrap.append(player, errorMessage, downloadRow);
  }  
  
  document.getElementById("downloadJsonBtn").onclick = () => {  
    const qs = new URLSearchParams({ transcript_path: detail.transcript_path });  
    window.open(`/api/documents/download/json?${qs.toString()}`, "_blank");  
  };  
  
  document.getElementById("downloadTextBtn").onclick = () => {  
    const qs = new URLSearchParams({ transcript_path: detail.transcript_path });  
    window.open(`/api/documents/download/text?${qs.toString()}`, "_blank");  
  };  
}  
  
async function runDocumentRag() {  
  if (!selectedDetail || !selectedDetail.transcript_path) {  
    alert("文書を選択してください");  
    return;  
  }  
  
  const message = document.getElementById("ragQuestion").value.trim();  
  if (!message) {  
    alert("質問を入力してください");  
    return;  
  }  
  
  const searchMode = document.getElementById("searchModeSelect").value;  
  const topK = parseInt(document.getElementById("topKSelect").value, 10);  
  const useRewrite = document.getElementById("useRewriteCheckbox").checked;  
  
  setLoadingMessage("RAG回答を生成中...");  
  
  try {  
    const data = await fetchJSON("/api/documents/chat", {  
      method: "POST",  
      headers: {  
        "Content-Type": "application/json",  
      },  
      body: JSON.stringify({  
        transcript_path: selectedDetail.transcript_path,  
        message: message,  
        search_mode: searchMode,  
        top_k: topK,  
        use_query_rewrite: useRewrite,  
      }),  
    });  
  
    renderRagResult(data);  
    setLoadingMessage("RAG回答生成完了");  
  } catch (e) {  
    setLoadingMessage(`RAG失敗: ${e.message}`);  
    alert(e.message);  
  }  
}  
  
function renderRagResult(data) {  
  document.getElementById("ragResultWrap").style.display = "block";  
  document.getElementById("ragSummary").textContent = data.summary || "";  
  document.getElementById("ragQuery").textContent = data.final_query || "";  
  document.getElementById("ragAnswer").textContent = data.answer || "関連する検索結果が見つかりませんでした。";  
  
  const intentBox = document.getElementById("intentBox");  
  if (data.intent_summary) {  
    intentBox.style.display = "block";  
    document.getElementById("ragIntent").textContent = data.intent_summary;  
  } else {  
    intentBox.style.display = "none";  
    document.getElementById("ragIntent").textContent = "";  
  }  
  
  const contextsBox = document.getElementById("ragContexts");  
  contextsBox.innerHTML = "";  
  
  for (let i = 0; i < (data.contexts || []).length; i++) {  
    const c = data.contexts[i];  
    const title = c.source_file || c.title || `doc-${i + 1}`;  
  
    const block = document.createElement("div");  
    block.className = "doc-item";  
    block.style.marginBottom = "10px";  
    block.innerHTML = `  
      <div><strong>[${i + 1}] ${escapeHtml(title)}</strong></div>  
      <pre style="white-space:pre-wrap; margin:8px 0 0;">${escapeHtml(JSON.stringify(c, null, 2))}</pre>  
    `;  
    contextsBox.appendChild(block);  
  }  
}  
  
function showUploadProgress(state, message, value) {
  const box = document.getElementById("uploadProgress");
  const bar = document.getElementById("uploadProgressBar");
  box.hidden = false;
  box.dataset.state = state;
  if (value === null) {
    bar.removeAttribute("value");
  } else {
    bar.value = value;
  }
  document.getElementById("uploadProgressText").textContent = message;
}

function uploadDocuments(formData) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/documents/upload");
    request.upload.addEventListener("progress", event => {
      const sent = (event.loaded / 1_000_000).toFixed(1);
      if (event.lengthComputable && event.total > 0) {
        const percent = Math.min(100, Math.floor(event.loaded / event.total * 100));
        const total = (event.total / 1_000_000).toFixed(1);
        showUploadProgress("uploading", `送信中 ${percent}%（${sent} / ${total} MB）`, percent);
      } else {
        showUploadProgress("uploading", `送信中（${sent} MB送信済み）`, null);
      }
    });
    request.upload.addEventListener("load", () => {
      showUploadProgress("saving", "送信完了（100%）・サーバーに保存中...", 100);
      setLoadingMessage("サーバーにファイルを保存中...");
    });
    request.addEventListener("load", () => {
      let data;
      try {
        data = JSON.parse(request.responseText);
      } catch {
        reject(new Error(request.status >= 200 && request.status < 300
          ? "サーバーの応答を読み取れません。ログイン状態を確認してください。"
          : `HTTP ${request.status}: サーバーがアップロードを受け付けませんでした。`));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        reject(new Error(typeof data?.detail === "string" ? data.detail : `HTTP ${request.status}`));
        return;
      }
      if (data?.status !== "ok" || !Number.isInteger(data.count) || data.count < 1 ||
          !Array.isArray(data.items) || data.items.length !== data.count) {
        reject(new Error("アップロード結果の形式が不正です。"));
        return;
      }
      resolve(data);
    });
    request.addEventListener("error", () => reject(new Error("ネットワークエラーでアップロードできませんでした。")));
    request.addEventListener("abort", () => reject(new Error("アップロードが中断されました。")));
    request.addEventListener("timeout", () => reject(new Error("アップロードがタイムアウトしました。")));
    request.send(formData);
  });
}

async function uploadDocumentFile() {
  const fileInput = document.getElementById("uploadFileInput");  
  const uploadBtn = document.getElementById("uploadDocumentBtn");  
  const uploadStatus = document.getElementById("uploadStatus");  
  if (uploadBtn.disabled) return;
  
  const files = Array.from(fileInput?.files || []);  
  if (!files.length) {  
    alert("アップロードするファイルを選択してください");  
    return;  
  }  

  const maxBytes = Number(fileInput.dataset.maxFileBytes);
  const oversized = files.find(file => file.size > maxBytes);
  if (!Number.isSafeInteger(maxBytes) || maxBytes <= 0 || oversized) {
    const message = oversized
      ? `${oversized.name}: 1ファイルの上限は500 MB（500,000,000 bytes）です。`
      : "アップロード上限を取得できません。画面を再読み込みしてください。";
    showUploadProgress("failed", message, 0);
    uploadStatus.textContent = message;
    setLoadingMessage(message);
    alert(message);
    return;
  }

  uploadBtn.disabled = true;  
  fileInput.disabled = true;
  showUploadProgress("uploading", "送信準備中（0%）", 0);
  uploadStatus.textContent = `アップロード中: ${files.length} ファイル`;  
  setLoadingMessage(`ファイルをアップロード中... (${files.length}件)`);  
  
  try {  
    const formData = new FormData();  
    for (const file of files) {  
      formData.append("files", file);  
    }  
  
    const data = await uploadDocuments(formData);
  
    const lines = [  
      `アップロード完了: ${data.count || 0} 件`,  
      "",  
      ...(data.items || []).map(x => `- ${x.name} → ${x.uploaded}`),  
      "",  
      "アップロード後のステータスは下の一覧で自動更新されます。"
    ];  
  
    uploadStatus.textContent = lines.join("\n");  
    showUploadProgress("completed", "アップロード完了（100%）。文字起こしの状況は下の一覧で確認できます。", 100);
    setLoadingMessage(`アップロード完了: ${data.count || 0} 件`);  
  
    fileInput.value = "";  
  
    await loadSourceUploads().catch(showUploadStatusError);
  } catch (e) {  
    const bar = document.getElementById("uploadProgressBar");
    showUploadProgress("failed", `アップロード失敗: ${e.message}`, bar.hasAttribute("value") ? bar.value : 0);
    uploadStatus.textContent = `アップロード失敗: ${e.message}`;  
    setLoadingMessage(`アップロード失敗: ${e.message}`);  
    alert(e.message);  
  } finally {  
    uploadBtn.disabled = false;  
    fileInput.disabled = false;
  }  
}  
  
document.getElementById("searchDocumentsBtn").addEventListener("click", loadDocuments);  
document.getElementById("refreshDocumentsBtn").addEventListener("click", () => {
  loadDocuments();
  loadSourceUploads().catch(showUploadStatusError);
});
document.getElementById("runRagBtn").addEventListener("click", runDocumentRag);  
  
const uploadBtn = document.getElementById("uploadDocumentBtn");  
if (uploadBtn) {  
  uploadBtn.addEventListener("click", uploadDocumentFile);  
}  
  
document.getElementById("dateFrom").value = daysAgoStr(30);  
document.getElementById("dateTo").value = todayStr();  
  
loadDocuments();  
loadSourceUploads().catch(showUploadStatusError);
pollProcessingStatus(loadSourceUploads, showUploadStatusError);