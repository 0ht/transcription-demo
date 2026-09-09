let selectedUpload = null;  
let selectedResult = null;  
  
async function fetchJSON(url, options = {}) {  
  const res = await fetch(url, options);  
  if (!res.ok) {  
    const text = await res.text();  
    let message = text || `HTTP ${res.status}`;
    try {
      message = JSON.parse(text).detail || message;
    } catch {
      // The response is not JSON; use the original response text.
    }
    throw new Error(message);
  }  
  return await res.json();  
}  
  
async function loadUploads() {  
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
    div.textContent = item.name;  
    div.onclick = async () => {  
      selectedUpload = item.name;  
      document.querySelectorAll("#uploadList .history-item").forEach(x => x.classList.remove("active"));  
      div.classList.add("active");  
  
      if (confirm(`OCRを実行しますか？\n${item.name}`)) {  
        const originalText = div.textContent;
        div.textContent = `OCR処理中: ${item.name}`;
        try {
          await runOCR(item.name);
        } catch (error) {
          alert(`OCR失敗: ${error.message}`);
        } finally {
          div.textContent = originalText;
        }
      }  
    };  
    box.appendChild(div);  
  });  
}  
  
async function runOCR(blobName) {  
  const data = await fetchJSON("/api/ocr/run", {  
    method: "POST",  
    headers: {"Content-Type": "application/json"},  
    body: JSON.stringify({ blob_name: blobName })  
  });  
  alert(`OCR完了: ${data.result_blob_name}`);  
  await loadResults();  
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
    div.textContent = item.name;  
    div.onclick = async () => {  
      selectedResult = item.name;  
      document.getElementById("ocrSelectedTitle").textContent = item.name;  
      document.querySelectorAll("#resultList .history-item").forEach(x => x.classList.remove("active"));  
      div.classList.add("active");  
  
      const detail = await fetchJSON(`/api/ocr/result/${encodeURIComponent(item.name)}`);  
      document.getElementById("ocrResultPreview").value = detail.text || "";  
  
      document.getElementById("downloadResultBtn").disabled = false;  
      document.getElementById("deleteResultBtn").disabled = false;  
    };  
    box.appendChild(div);  
  });  
}  
  
async function uploadFiles() {  
  const input = document.getElementById("ocrFileInput");  
  if (!input.files || input.files.length === 0) {  
    alert("ファイルを選択してください");  
    return;  
  }  
  
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
  
  alert("アップロード完了");  
  input.value = "";  
  await loadUploads();  
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
  
  await loadResults();  
}  
  
function downloadSelectedResult() {  
  if (!selectedResult) return;  
  window.open(`/api/ocr/result/${encodeURIComponent(selectedResult)}/download`, "_blank");  
}  
  
document.getElementById("uploadOcrBtn").addEventListener("click", uploadFiles);  
document.getElementById("refreshUploadsBtn").addEventListener("click", loadUploads);  
document.getElementById("refreshResultsBtn").addEventListener("click", loadResults);  
document.getElementById("deleteResultBtn").addEventListener("click", deleteSelectedResult);  
document.getElementById("downloadResultBtn").addEventListener("click", downloadSelectedResult);  
  
loadUploads();  
loadResults();  