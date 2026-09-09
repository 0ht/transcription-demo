let selectedTranscriptPath = null;  
let selectedDetail = null;  
  
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
  
  if (detail.media_path) {  
    const qs = new URLSearchParams({ media_path: detail.media_path });  
    const url = `/api/documents/media?${qs.toString()}`;  
  
    if (detail.is_audio) {  
      mediaWrap.innerHTML = `<audio controls style="width:100%;" src="${url}"></audio>`;  
    } else if (detail.is_video) {  
      mediaWrap.innerHTML = `<video controls style="width:100%; max-height:320px;" src="${url}"></video>`;  
    }  
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
  
document.getElementById("searchDocumentsBtn").addEventListener("click", loadDocuments);  
document.getElementById("refreshDocumentsBtn").addEventListener("click", loadDocuments);  
document.getElementById("runRagBtn").addEventListener("click", runDocumentRag);  
  
document.getElementById("dateFrom").value = daysAgoStr(30);  
document.getElementById("dateTo").value = todayStr();  
  
loadDocuments();  