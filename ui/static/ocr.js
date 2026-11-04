let selectedUpload = null;  
let selectedResult = null;  
let uploadsRefresh = null;
let previousOCRStates = new Map();
let sourceObjectURL = null;
let sourceRequest = null;
let runningOCR = new Set();
  
async function fetchJSON(url, options = {}) {  
  const res = await fetch(url, options);  
  if (!res.ok) {  
    const text = await res.text();  
    throw new Error(text || `HTTP ${res.status}`);  
  }  
  return await res.json();  
}  
  
async function loadUploads() {  
  if (uploadsRefresh) return uploadsRefresh;
  uploadsRefresh = refreshUploads();
  try {
    await uploadsRefresh;
  } finally {
    uploadsRefresh = null;
  }
}

async function refreshUploads() {
  const data = await fetchJSON("/api/ocr/uploads");  
  const box = document.getElementById("uploadList");  
  box.innerHTML = "";  
  
  if (!data.items || data.items.length === 0) {  
    box.innerHTML = `<div class="history-item">アップロード済みファイルはありません</div>`;  
    return;  
  }  
  
  data.items.forEach(item => {  
    const div = document.createElement("div");  
    div.className = "history-item";  
    const name = document.createElement("strong");
    name.textContent = item.name;
    div.appendChild(name);
    const status = runningOCR.has(item.name) ? {...item, status: "processing", error: ""} : item;
    div.appendChild(createProcessingStatus(status));
    if (item.name === selectedUpload) div.classList.add("active");
    div.onclick = async () => {  
      selectedUpload = item.name;  
      selectedResult = null;
      document.getElementById("ocrSelectedTitle").textContent = item.name;
      document.getElementById("ocrResultPreview").value = "";
      document.getElementById("downloadResultBtn").disabled = true;
      document.getElementById("deleteResultBtn").disabled = true;
      document.querySelectorAll("#uploadList .history-item").forEach(x => x.classList.remove("active"));  
      div.classList.add("active");  
      await showOCRSource(item.name);
      if (status.status === "processing") {
        setOCRStatus("このファイルは処理中です。完了までお待ちください。");
        return;
      }
      if (confirm(`OCRを実行しますか？\n${item.name}`)) {  
        await runOCR(item.name);  
      }  
    };  
    box.appendChild(div);  
  });  
  const changed = data.items.some(item =>
    item.status === "completed" && previousOCRStates.get(item.name) !== "completed");
  previousOCRStates = new Map(data.items.map(item => [item.name, item.status]));
  if (changed) await loadResults();
}  

function setOCRStatus(message) {
  document.getElementById("ocrStatus").textContent = message;
}

function showOCRError(error) {
  setOCRStatus(`操作失敗: ${error.message}`);
}

async function showOCRSource(blobName) {
  if (sourceRequest) sourceRequest.abort();
  sourceRequest = new AbortController();
  const request = sourceRequest;
  const box = document.getElementById("ocrSourcePreview");
  box.replaceChildren();
  document.getElementById("ocrSourceName").textContent = blobName || "元文書の対応情報がありません";
  const download = document.getElementById("ocrSourceDownload");
  download.hidden = !blobName;
  if (sourceObjectURL) {
    URL.revokeObjectURL(sourceObjectURL);
    sourceObjectURL = null;
  }
  if (!blobName) {
    box.textContent = "元文書をアップロード一覧から選択してください。";
    return;
  }
  const url = `/api/ocr/source/${encodeURIComponent(blobName)}`;
  download.href = url;
  box.textContent = "元文書を読み込み中...";
  try {
    const response = await fetch(url, {signal: request.signal});
    if (!response.ok) throw new Error(`元文書取得失敗: HTTP ${response.status}`);
    const blob = await response.blob();
    if (request.signal.aborted) return;
    sourceObjectURL = URL.createObjectURL(blob);
    box.replaceChildren();
    const extension = blobName.split(".").pop().toLowerCase();
    if (extension === "pdf") {
      const frame = document.createElement("iframe");
      frame.title = `元文書: ${blobName}`;
      frame.src = sourceObjectURL;
      box.appendChild(frame);
    } else if (["png", "jpg", "jpeg", "bmp"].includes(extension)) {
      const image = document.createElement("img");
      image.alt = `元文書: ${blobName}`;
      image.src = sourceObjectURL;
      image.onerror = () => { box.textContent = "画像を表示できません。元文書を開く / 保存から確認してください。"; };
      box.appendChild(image);
    } else {
      box.textContent = "ブラウザーで表示できない形式です。元文書を開く / 保存から確認してください。";
    }
  } catch (error) {
    if (error.name !== "AbortError") {
      box.textContent = error.message;
      showOCRError(error);
    }
  }
}
  
async function runOCR(blobName) {  
  if (runningOCR.has(blobName)) return;
  runningOCR.add(blobName);
  setOCRStatus(`OCR処理中: ${blobName}`);
  await showOCRSource(blobName);
  document.getElementById("ocrResultPreview").value = "OCR処理中...";
  let processed = false;
  try {
    await loadUploads().catch(showOCRError);
    const data = await fetchJSON("/api/ocr/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ blob_name: blobName })
    });
    processed = true;
    if (selectedUpload === blobName && selectedResult === null) {
      await selectOCRResult(data.result_blob_name);
    }
    setOCRStatus(`OCR完了: ${blobName}`);
    await loadResults();
  } catch (error) {
    const message = `${processed ? "OCR結果の表示失敗" : "OCR失敗"}: ${error.message}`;
    if (selectedUpload === blobName && selectedResult === null) {
      document.getElementById("ocrResultPreview").value = message;
    }
    setOCRStatus(message);
  } finally {
    runningOCR.delete(blobName);
    await loadUploads().catch(showOCRError);
  }
}  
  
async function loadResults() {  
  const data = await fetchJSON("/api/ocr/results");  
  const box = document.getElementById("resultList");  
  box.innerHTML = "";  
  
  if (!data.items || data.items.length === 0) {  
    box.innerHTML = `<div class="history-item">OCR結果はありません</div>`;  
    return;  
  }  
  
  data.items.forEach(item => {  
    const div = document.createElement("div");  
    div.className = "history-item";  
    if (item.name === selectedResult) div.classList.add("active");
    div.textContent = item.name;  
    div.onclick = async () => {  
      document.querySelectorAll("#resultList .history-item").forEach(x => x.classList.remove("active"));  
      div.classList.add("active");  
      try {
        await selectOCRResult(item.name, item.source_blob_name);
      } catch (error) {
        showOCRError(error);
      }

    };  
    box.appendChild(div);  
  });  
}  

async function selectOCRResult(blobName, sourceName = "") {
  selectedResult = blobName;
  document.getElementById("ocrSelectedTitle").textContent = blobName;
  document.getElementById("ocrResultPreview").value = "OCR結果を読み込み中...";
  document.getElementById("downloadResultBtn").disabled = true;
  document.getElementById("deleteResultBtn").disabled = true;
  try {
    const detail = await fetchJSON(`/api/ocr/result/${encodeURIComponent(blobName)}`);
    if (selectedResult !== blobName) return;
    await showOCRSource(detail.source_blob_name || sourceName || "");
    if (selectedResult !== blobName) return;
    document.getElementById("ocrResultPreview").value = detail.text || "";
    document.getElementById("downloadResultBtn").disabled = false;
    document.getElementById("deleteResultBtn").disabled = false;
  } catch (error) {
    if (selectedResult === blobName) {
      document.getElementById("ocrResultPreview").value = `OCR結果取得失敗: ${error.message}`;
    }
    throw error;
  }
}
  
async function uploadFiles() {  
  const input = document.getElementById("ocrFileInput");  
  if (!input.files || input.files.length === 0) {  
    alert("ファイルを選択してください");  
    return;  
  }  
  
  const button = document.getElementById("uploadOcrBtn");
  button.disabled = true;
  try {
    for (const file of input.files) {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/ocr/upload", {
        method: "POST",
        body: form
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "upload failed");
      }
    }
    setOCRStatus("アップロード完了。待機中のファイルをクリックしてOCRを実行してください。");
    input.value = "";
  } catch (error) {
    showOCRError(error);
  } finally {
    button.disabled = false;
    await loadUploads().catch(showOCRError);
  }
}  
  
async function deleteSelectedResult() {  
  if (!selectedResult) return;  
  if (!confirm(`削除しますか？\n${selectedResult}`)) return;  
  
  await fetchJSON(`/api/ocr/result/${encodeURIComponent(selectedResult)}`, {  
    method: "DELETE"  
  });  
  
  selectedResult = null;  
  document.getElementById("ocrSelectedTitle").textContent = "未選択";  
  document.getElementById("ocrResultPreview").value = "";  
  document.getElementById("downloadResultBtn").disabled = true;  
  document.getElementById("deleteResultBtn").disabled = true;  
  await showOCRSource("");
  
  await loadResults();  
}  
  
function downloadSelectedResult() {  
  if (!selectedResult) return;  
  window.open(`/api/ocr/result/${encodeURIComponent(selectedResult)}/download`, "_blank");  
}  
  
document.getElementById("uploadOcrBtn").addEventListener("click", uploadFiles);  
document.getElementById("refreshUploadsBtn").addEventListener("click", () => loadUploads().catch(showOCRError));
document.getElementById("refreshResultsBtn").addEventListener("click", () => loadResults().catch(showOCRError));
document.getElementById("deleteResultBtn").addEventListener("click", () => deleteSelectedResult().catch(showOCRError));
document.getElementById("downloadResultBtn").addEventListener("click", downloadSelectedResult);  
  
loadUploads().catch(showOCRError);
loadResults().catch(showOCRError);
pollProcessingStatus(loadUploads, showOCRError);
window.addEventListener("pagehide", () => {
  if (sourceRequest) sourceRequest.abort();
  if (sourceObjectURL) URL.revokeObjectURL(sourceObjectURL);
});