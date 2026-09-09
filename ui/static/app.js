console.log("APP_JS_CHAT_LAYOUT_V8 LOADED");  
  
let currentSessionId = null;  
let ws = null;  
let viewingHistoryItemId = null;  
  
let mediaStream = null;  
let audioContext = null;  
let sourceNode = null;  
let processorNode = null;  
  
let transcriptPollingTimer = null;  
let transcriptPollingGeneration = 0;  
  
let isSessionStarting = false;  
let isSessionStopping = false;  
let isMicStarting = false;  
let isMicRecording = false;  
let isAnalyzing = false;  
let isConfirming = false;  
let latestAnalysisData = null;  
  
const chatMessagesState = [];  
  
/* DOM */  
const appShell = document.querySelector(".app-shell");  
  
const apiStatus = document.getElementById("apiStatus");  
const sessionStatus = document.getElementById("sessionStatus");  
const micStatus = document.getElementById("micStatus");  
const backToCurrentBtn = document.getElementById("backToCurrentBtn");  
  
const historySidebar = document.getElementById("historySidebar");  
const toggleHistoryBtn = document.getElementById("toggleHistoryBtn");  
  
const transcriptSidebar = document.getElementById("transcriptSidebar");  
const toggleTranscriptBtn = document.getElementById("toggleTranscriptBtn");  
const closeTranscriptBtn = document.getElementById("closeTranscriptBtn");  
  
const transcriptBox = document.getElementById("transcriptBox");  
const debugTextBox = document.getElementById("debugTextBox");  
  
const historyBox = document.getElementById("historyBox");  
const promptSetSelect = document.getElementById("promptSetSelect");  
const chatMessages = document.getElementById("chatMessages");  
const chatInput = document.getElementById("chatInput");  
const chatContextTitle = document.getElementById("chatContextTitle");  
  
const startConversationBtn = document.getElementById("startConversationBtn");  
const pauseConversationBtn = document.getElementById("pauseConversationBtn");  
const stopConversationBtn = document.getElementById("stopConversationBtn");  
  
const commitBtn = document.getElementById("commitBtn");  
const refreshTranscriptBtn = document.getElementById("refreshTranscriptBtn");  
const addDebugBtn = document.getElementById("addDebugBtn");  
const refreshHistoryBtn = document.getElementById("refreshHistoryBtn");  
const clearDebugInputBtn = document.getElementById("clearDebugInputBtn");  
  
const analysisCardContainer = document.getElementById("analysisCardContainer");  
  
/* API */  
async function apiGet(url) {  
  const res = await fetch(url);  
  if (!res.ok) {  
    const text = await res.text();  
    throw new Error(text);  
  }  
  return res.json();  
}  
  
async function apiPost(url, body = null) {  
  const options = {  
    method: "POST",  
    headers: { "Content-Type": "application/json" }  
  };  
  
  if (body !== null) {  
    options.body = JSON.stringify(body);  
  }  
  
  const res = await fetch(url, options);  
  if (!res.ok) {  
    const text = await res.text();  
    throw new Error(text);  
  }  
  return res.json();  
}  
  
/* UI helpers */  
function setApiStatus(text) {  
  apiStatus.textContent = text;  
}  
  
function setSessionStatus(text) {  
  sessionStatus.textContent = text;  
}  
  
function setMicStatus(text) {  
  micStatus.textContent = `Mic: ${text}`;  
}  
  
function openTranscriptSidebar() {  
  if (!transcriptSidebar) return;  
  transcriptSidebar.classList.remove("collapsed");  
  appShell.classList.add("sidebar-open");  
}  
  
function closeTranscriptSidebar() {  
  if (!transcriptSidebar) return;  
  transcriptSidebar.classList.add("collapsed");  
  appShell.classList.remove("sidebar-open");  
}  
  
function escapeHtml(str) {  
  return String(str || "")  
    .replaceAll("&", "&amp;")  
    .replaceAll("<", "&lt;")  
    .replaceAll(">", "&gt;")  
    .replaceAll('"', "&quot;")  
    .replaceAll("'", "&#039;");  
}  
  
function renderTranscript(fullText = "", partialText = "") {  
  const finalText = (fullText || "").trim();  
  const partial = (partialText || "").trim();  
  
  if (finalText && partial) {  
    transcriptBox.value = `${finalText}\n\n[認識中] ${partial}`;  
  } else if (finalText) {  
    transcriptBox.value = finalText;  
  } else if (partial) {  
    transcriptBox.value = `[認識中] ${partial}`;  
  } else {  
    transcriptBox.value = "";  
  }  
}  
  
function clearChatMessages() {  
  chatMessagesState.length = 0;  
  chatMessages.innerHTML = "";  
}  
  
function pushChatMessage(role, html) {  
  chatMessagesState.push({ role, html });  
  renderChatMessages();  
}  
  
function renderChatMessages() {  
  chatMessages.innerHTML = "";  
  
  if (!chatMessagesState.length) {  
    chatMessages.innerHTML = `  
      <div class="chat-message assistant">  
        <div class="chat-bubble">  
          ユーザーとの会話内容について質問したり、「要約して」「回答候補をレコメンドして」などと入力してください。  
        </div>  
      </div>  
    `;  
    return;  
  }  
  
  chatMessagesState.forEach((msg) => {  
    const wrapper = document.createElement("div");  
    wrapper.className = `chat-message ${msg.role}`;  
  
    const bubble = document.createElement("div");  
    bubble.className = "chat-bubble";  
    bubble.innerHTML = msg.html;  
  
    wrapper.appendChild(bubble);  
    chatMessages.appendChild(wrapper);  
  });  
  
  chatMessages.scrollTop = chatMessages.scrollHeight;  
}  
  
function hideAnalysisCard() {  
  latestAnalysisData = null;  
  analysisCardContainer.classList.add("hidden");  
  analysisCardContainer.innerHTML = "";  
}  
  
function showAnalysisCard(data, expanded = false) {  
  latestAnalysisData = data || null;  
  
  if (!data) {  
    hideAnalysisCard();  
    return;  
  }  
  
  const topicTitle = data.current_topic_title || data.topic_title || "検索結果 / 分析結果";  
  const summary = data.current_summary || data.summary || data.current_analysis || data.analysis || "";  
  const shortSummary = truncateText(summary, 120);  
  
  analysisCardContainer.classList.remove("hidden");  
  analysisCardContainer.innerHTML = `  
    <div class="analysis-card ${expanded ? "expanded" : "compact"}">  
      <div class="analysis-card-header">  
        <h3>${escapeHtml(topicTitle)}</h3>  
        <div class="analysis-card-actions">  
          <button id="toggleAnalysisCardBtn" class="secondary">  
            ${expanded ? "折りたたむ" : "展開"}  
          </button>  
          <button id="clearAnalysisCardBtn" class="secondary">閉じる</button>  
        </div>  
      </div>  
  
      <div class="analysis-card-summary">  
        ${escapeHtml(shortSummary).replaceAll("\n", "<br>")}  
      </div>  
  
      <div class="analysis-card-body">  
        ${buildAssistantAnalysisHtml(data)}  
      </div>  
    </div>  
  `;  
  
  const clearBtn = document.getElementById("clearAnalysisCardBtn");  
  if (clearBtn) {  
    clearBtn.addEventListener("click", hideAnalysisCard);  
  }  
  
  const toggleBtn = document.getElementById("toggleAnalysisCardBtn");  
  if (toggleBtn) {  
    toggleBtn.addEventListener("click", () => {  
      showAnalysisCard(data, !expanded);  
    });  
  }  
}  
  
function buildAssistantAnalysisHtml(data) {  
  const summary = data.current_summary || data.summary || "";  
  const searchQuery = data.current_search_query || data.search_query || "";  
  const analysis = data.current_analysis || data.analysis || "";  
  const nextActions = data.current_next_actions || data.next_actions || [];  
  const docs = data.current_docs || data.docs || [];  
  
  return `  
    ${summary ? `  
      <div class="assistant-card-section" style="margin-top:0;padding-top:0;border-top:none;">  
        <h4>要約</h4>  
        <div>${escapeHtml(summary).replaceAll("\n", "<br>")}</div>  
      </div>  
    ` : ""}  
  
    ${analysis ? `  
      <div class="assistant-card-section">  
        <h4>回答</h4>  
        <div>${escapeHtml(analysis).replaceAll("\n", "<br>")}</div>  
      </div>  
    ` : ""}  
  
    ${nextActions.length ? `  
      <div class="assistant-card-section">  
        <h4>次アクション</h4>  
        <ul>  
          ${nextActions.map(x => `<li>${escapeHtml(x)}</li>`).join("")}  
        </ul>  
      </div>  
    ` : ""}  
  
    ${buildDocsDetailsHtml(docs)}  
  
    ${searchQuery ? `  
      <details class="assistant-details">  
        <summary>検索クエリ</summary>  
        <div style="margin-top: 8px;">${escapeHtml(searchQuery)}</div>  
      </details>  
    ` : ""}  
  
    ${buildFollowupChipsHtml([  
      "もっと短く要約して",  
      "懸念点だけ教えて",  
      "次アクションだけ箇条書きで",  
      "上司向けに簡潔に言い換えて",  
      "メール文案にして"  
    ])}  
  `;  
}  
  
function updateButtonStates() {  
  const hasSession = !!currentSessionId;  
  const wsOpen = !!(ws && ws.readyState === WebSocket.OPEN);  
  
  if (startConversationBtn) {  
    startConversationBtn.disabled =  
      isSessionStarting ||  
      isSessionStopping ||  
      isMicStarting ||  
      (hasSession && isMicRecording);  
  }  
  
  if (pauseConversationBtn) {  
    pauseConversationBtn.disabled =  
      !hasSession ||  
      isSessionStarting ||  
      isSessionStopping ||  
      isMicStarting ||  
      !isMicRecording;  
  }  
  
  if (stopConversationBtn) {  
    stopConversationBtn.disabled =  
      !hasSession ||  
      isSessionStarting ||  
      isSessionStopping;  
  }  
  
  if (commitBtn) {  
    commitBtn.disabled =  
      !hasSession ||  
      !wsOpen ||  
      !isMicRecording ||  
      isSessionStarting ||  
      isSessionStopping;  
  }  
  
  if (refreshTranscriptBtn) {  
    refreshTranscriptBtn.disabled =  
      !hasSession || isSessionStarting || isSessionStopping;  
  }  
  
  if (addDebugBtn) {  
    addDebugBtn.disabled =  
      !hasSession ||  
      isAnalyzing ||  
      isConfirming ||  
      isSessionStarting ||  
      isSessionStopping;  
  }  
  
  if (refreshHistoryBtn) {  
    refreshHistoryBtn.disabled = isConfirming;  
  }  
  
  if (backToCurrentBtn) {  
    backToCurrentBtn.disabled =  
      !viewingHistoryItemId ||  
      isSessionStarting ||  
      isSessionStopping;  
  }  
}  
  
function updateHistoryView(history) {  
  historyBox.innerHTML = "";  
  
  if (!history || !history.length) {  
    historyBox.innerHTML = "<div class='empty-box'>履歴はまだありません。</div>";  
    return;  
  }  
  
  history.forEach((item) => {  
    const div = document.createElement("div");  
    div.className = "history-item";  
  
    if (viewingHistoryItemId && String(viewingHistoryItemId) === String(item.topic_id)) {  
      div.classList.add("active");  
    }  
  
    div.innerHTML = `  
      <strong>${escapeHtml(item.title || "無題")}</strong>  
      <div class="small" style="margin-top: 6px;">${escapeHtml(item.created_at || "")}</div>  
      <div style="margin-top: 6px;">${escapeHtml(item.summary || "")}</div>  
    `;  
  
    div.addEventListener("click", async () => {  
      await loadHistoryItem(item.topic_id);  
    });  
  
    historyBox.appendChild(div);  
  });  
}  
  
function clearMainView() {  
  renderTranscript("", "");  
  clearChatMessages();  
  hideAnalysisCard();  
  setMicStatus("idle");  
  chatContextTitle.textContent = "現在の会話";  
}  
  
function toggleHistorySidebar() {  
  if (!historySidebar || !toggleHistoryBtn) return;  
  
  historySidebar.classList.toggle("collapsed");  
  
  if (historySidebar.classList.contains("collapsed")) {  
    toggleHistoryBtn.textContent = "広げる";  
  } else {  
    toggleHistoryBtn.textContent = "折りたたむ";  
  }  
}  
  
async function loadHistoryItem(historyId) {  
  try {  
    const data = await apiGet(`/api/history/${historyId}`);  
  
    viewingHistoryItemId = historyId;  
    chatContextTitle.textContent = data.topic_title || data.title || `履歴 ${historyId}`;  
  
    renderTranscript(data.full_text || "", data.partial_text || "");  
    restoreChatMessagesFromHistory(data.chat_messages || []);  
  
    showAnalysisCard({  
      topic_title: data.topic_title || data.title || "",  
      summary: data.summary || "",  
      search_query: data.search_query || "",  
      analysis: data.analysis || "",  
      docs: data.docs || []  
    });  
  
    if (currentSessionId) {  
      try {  
        await apiPost(`/api/session/${currentSessionId}/chat/load_history/${historyId}`);  
      } catch (_) {  
        // 補助APIなので失敗しても継続  
      }  
    }  
  
    updateHistoryView((await apiGet("/api/history")).items || []);  
    setMicStatus(`history preview: ${historyId}`);  
    updateButtonStates();  
    openTranscriptSidebar();  
  } catch (e) {  
    alert("履歴の復元に失敗しました\n" + e.message);  
  }  
}  
  
async function backToCurrentSessionView() {  
  try {  
    viewingHistoryItemId = null;  
    chatContextTitle.textContent = "現在の会話";  
    hideAnalysisCard();  
  
    if (!currentSessionId) {  
      clearMainView();  
      await refreshHistory();  
      updateButtonStates();  
      return;  
    }  
  
    clearChatMessages();  
    await refreshTranscript(false);  
    await refreshHistory();  
    updateButtonStates();  
  } catch (e) {  
    alert("現在の会話への復帰に失敗しました\n" + e.message);  
  }  
}  
  
async function startSession() {  
  if (isSessionStarting) return;  
  
  try {  
    isSessionStarting = true;  
    updateButtonStates();  
  
    const data = await apiPost("/api/session/start");  
    currentSessionId = data.session_id;  
    viewingHistoryItemId = null;  
  
    setSessionStatus(`Session: ${currentSessionId}`);  
    setMicStatus("session ready");  
    renderTranscript("", "");  
    clearChatMessages();  
    hideAnalysisCard();  
    chatContextTitle.textContent = "現在の会話";  
    startTranscriptPolling();  
  } catch (e) {  
    alert("会話開始に失敗しました\n" + e.message);  
    throw e;  
  } finally {  
    isSessionStarting = false;  
    updateButtonStates();  
  }  
}  
  
async function stopSession() {  
  if (isSessionStopping) return;  
  
  try {  
    if (!currentSessionId) return;  
  
    isSessionStopping = true;  
    updateButtonStates();  
  
    stopTranscriptPolling();  
  
    const sessionIdToStop = currentSessionId;  
  
    // 先に録音・通信停止  
    await stopMic();  
    closeWebSocket();  
  
    // 保存するか確認  
    const shouldSave = confirm("会話終了前に履歴を保存しますか？");  
  
    if (shouldSave) {  
      try {  
        const prompt_set_name = promptSetSelect.value;  
        const confirmData = await apiPost(`/api/session/${sessionIdToStop}/confirm`, {  
          prompt_set_name  
        });  
  
        await refreshHistory();  
  
        pushChatMessage(  
          "assistant",  
          `保存しました: ${escapeHtml(confirmData.item?.title || "")}`  
        );  
      } catch (e) {  
        const continueWithoutSave = confirm(  
          "履歴保存に失敗しました。\n保存せずに終了しますか？\n\n" + e.message  
        );  
  
        if (!continueWithoutSave) {  
          // 終了中止  
          isSessionStopping = false;  
          updateButtonStates();  
          return;  
        }  
      }  
    }  
  
    await apiPost(`/api/session/${sessionIdToStop}/stop`);  
  
    currentSessionId = null;  
    viewingHistoryItemId = null;  
  
    clearMainView();  
    setSessionStatus("Session: -");  
    setMicStatus("stopped");  
  
    await refreshHistory();  
  } catch (e) {  
    alert("会話終了に失敗しました\n" + e.message);  
  } finally {  
    isSessionStopping = false;  
    updateButtonStates();  
  }  
}  
  
async function startOrResumeConversation() {  
  try {  
    if (!currentSessionId) {  
      await startSession();  
    }  
    if (!isMicRecording) {  
      await startMic();  
    }  
  } catch (e) {  
    alert("会話開始に失敗しました\n" + e.message);  
  } finally {  
    updateButtonStates();  
  }  
}  
  
async function pauseConversation() {  
  try {  
    if (!currentSessionId || !isMicRecording) return;  
    await stopMic();  
    closeWebSocket();  
    setMicStatus("paused");  
  } catch (e) {  
    alert("一時停止に失敗しました\n" + e.message);  
  } finally {  
    updateButtonStates();  
  }  
}  
  
async function refreshTranscript(silent = false) {  
  try {  
    if (!currentSessionId) return;  
    if (viewingHistoryItemId) return;  
  
    const sessionId = currentSessionId;  
    const data = await apiGet(`/api/session/${sessionId}/transcript`);  
  
    if (!currentSessionId || currentSessionId !== sessionId) {  
      return;  
    }  
  
    renderTranscript(data.full_text || "", data.partial_text || "");  
  
    if (isMicRecording) {  
      setMicStatus(`running=true, chunks=${data.audio_chunk_count}`);  
    } else if (currentSessionId) {  
      setMicStatus(`paused, chunks=${data.audio_chunk_count}`);  
    } else {  
      setMicStatus("idle");  
    }  
  } catch (e) {  
    if (!silent) {  
      alert("文字おこし取得に失敗しました\n" + e.message);  
    }  
  }  
}  
  
function startTranscriptPolling() {  
  stopTranscriptPolling();  
  
  const myGeneration = ++transcriptPollingGeneration;  
  
  transcriptPollingTimer = setInterval(() => {  
    if (myGeneration !== transcriptPollingGeneration) return;  
    refreshTranscript(true);  
  }, 5000);  
}  
  
function stopTranscriptPolling() {  
  transcriptPollingGeneration++;  
  
  if (transcriptPollingTimer) {  
    clearInterval(transcriptPollingTimer);  
    transcriptPollingTimer = null;  
  }  
}  
  
async function addDebugText() {  
  try {  
    if (!currentSessionId) {  
      alert("先に会話開始してください");  
      return;  
    }  
  
    const text = debugTextBox.value.trim();  
    if (!text) {  
      alert("デバッグ入力が空です");  
      return;  
    }  
  
    await apiPost(`/api/session/${currentSessionId}/debug_text`, { text });  
    debugTextBox.value = "";  
    await refreshTranscript();  
    openTranscriptSidebar();  
  } catch (e) {  
    alert("デバッグ文字追加に失敗しました\n" + e.message);  
  }  
}  
  
async function analyzeWithOptionalUserPrompt(userPrompt = "") {  
  if (isAnalyzing) return;  
  
  try {  
    if (!currentSessionId) {  
      alert("先に会話開始してください");  
      return;  
    }  
  
    if (viewingHistoryItemId) {  
      alert("履歴表示中ではなく、現在の会話に戻ってから実行してください");  
      return;  
    }  
  
    isAnalyzing = true;  
    updateButtonStates();  
  
    if (userPrompt && userPrompt.trim()) {  
      pushChatMessage("user", escapeHtml(userPrompt.trim()).replaceAll("\n", "<br>"));  
    }  
  
    const prompt_set_name = promptSetSelect.value;  
    const data = await apiPost(`/api/session/${currentSessionId}/analyze`, {  
      prompt_set_name,  
      user_instruction: userPrompt || ""  
    });  
  
    showAnalysisCard(data);  
  
    if (userPrompt && userPrompt.trim()) {  
      pushChatMessage(  
        "assistant",  
        buildAssistantChatHtml(  
          "分析結果を上に表示しました。この内容について追加で質問できます。",  
          data.docs || [],  
          data.search_query || ""  
        )  
      );  
    }  
  } catch (e) {  
    alert("分析に失敗しました\n" + e.message);  
  } finally {  
    isAnalyzing = false;  
    updateButtonStates();  
  }  
}  
  
async function sendChat() {  
  const text = chatInput.value.trim();  
  if (!text) {  
    return;  
  }  
  
  console.log("[sendChat] start", {  
    text,  
    currentSessionId,  
    viewingHistoryItemId,  
    latestAnalysisData,  
    hasLatestAnalysisData: !!latestAnalysisData  
  });  
  
  try {  
    isAnalyzing = true;  
    updateButtonStates();  
  
    pushChatMessage("user", escapeHtml(text).replaceAll("\n", "<br>"));  
  
    if (viewingHistoryItemId) {  
      const data = await apiPost(`/api/history/${viewingHistoryItemId}/chat`, {  
        message: text,  
        prompt_set_name: promptSetSelect.value,  
        use_rag: true,  
        context_mode: "history",  
        topic_id: viewingHistoryItemId  
      });  
  
      if (data.context_title) {  
        chatContextTitle.textContent = data.context_title;  
      }  
  
      pushChatMessage(  
        "assistant",  
        buildAssistantChatHtml(data.reply || "", data.docs || [], data.search_query || "")  
      );  
  
      chatInput.value = "";  
      return;  
    }  
  
    if (!currentSessionId) {  
      alert("現在の会話に対して質問するには、先に会話開始してください");  
      return;  
    }  
  
    if (!latestAnalysisData) {  
      const analysisData = await apiPost(`/api/session/${currentSessionId}/analyze`, {  
        prompt_set_name: promptSetSelect.value,  
        user_instruction: text  
      });  
  
      showAnalysisCard(analysisData);  
      pushChatMessage(  
        "assistant",  
        buildAssistantChatHtml(  
          analysisData.analysis || "分析結果を表示しました。",  
          analysisData.docs || [],  
          analysisData.search_query || ""  
        )  
      );  
  
      chatInput.value = "";  
      return;  
    }  
  
    const payload = {  
      message: text,  
      prompt_set_name: promptSetSelect.value,  
      use_rag: true,  
      context_mode: "current_transcript",  
      topic_id: null  
    };  
  
    const data = await apiPost(`/api/session/${currentSessionId}/chat`, payload);  
  
    if (data.context_title) {  
      chatContextTitle.textContent = data.context_title;  
    }  
  
    pushChatMessage(  
      "assistant",  
      buildAssistantChatHtml(data.reply || "", data.docs || [], data.search_query || "")  
    );  
  
    chatInput.value = "";  
  } catch (e) {  
    console.error("[sendChat] error", e);  
    alert("チャット送信に失敗しました\n" + e.message);  
  } finally {  
    isAnalyzing = false;  
    updateButtonStates();  
  }  
}  
  
async function confirmCurrent() {  
  if (isConfirming) return;  
  
  try {  
    if (!currentSessionId) {  
      alert("先に会話開始してください");  
      return;  
    }  
  
    isConfirming = true;  
    updateButtonStates();  
  
    const prompt_set_name = promptSetSelect.value;  
    const data = await apiPost(`/api/session/${currentSessionId}/confirm`, {  
      prompt_set_name  
    });  
  
    await refreshHistory();  
    await refreshTranscript();  
  
    pushChatMessage(  
      "assistant",  
      `保存しました: ${escapeHtml(data.item?.title || "")}`  
    );  
  } catch (e) {  
    alert("確定に失敗しました\n" + e.message);  
  } finally {  
    isConfirming = false;  
    updateButtonStates();  
  }  
}  
  
async function refreshHistory() {  
  try {  
    const data = await apiGet("/api/history");  
    updateHistoryView(data.items || []);  
  } catch (e) {  
    alert("履歴取得に失敗しました\n" + e.message);  
  }  
}  
  
function buildWsUrl(sessionId) {  
  const scheme = location.protocol === "https:" ? "wss" : "ws";  
  return `${scheme}://${location.host}/ws/audio/${sessionId}`;  
}  
  
async function connectWebSocket() {  
  if (!currentSessionId) {  
    alert("先に会話開始してください");  
    return;  
  }  
  
  if (ws && ws.readyState === WebSocket.OPEN) {  
    console.log("ws already open");  
    updateButtonStates();  
    return;  
  }  
  
  const wsUrl = buildWsUrl(currentSessionId);  
  console.log("connecting ws:", wsUrl);  
  
  ws = new WebSocket(wsUrl);  
  ws.binaryType = "arraybuffer";  
  
  await new Promise((resolve, reject) => {  
    ws.onopen = () => {  
      console.log("ws open");  
      setMicStatus("websocket connected");  
      updateButtonStates();  
      resolve();  
    };  
  
    ws.onmessage = (event) => {  
      console.log("ws message", event.data);  
    };  
  
    ws.onerror = (err) => {  
      console.error("ws error", err);  
      setMicStatus("websocket error");  
      updateButtonStates();  
      reject(new Error("WebSocket error"));  
    };  
  
    ws.onclose = (event) => {  
      console.log("ws close", event.code, event.reason);  
      setMicStatus("websocket closed");  
      updateButtonStates();  
    };  
  });  
}  
  
function closeWebSocket() {  
  if (ws) {  
    try {  
      ws.close();  
    } catch (_) {}  
    ws = null;  
  }  
  updateButtonStates();  
}  
  
function downsampleBuffer(buffer, inputSampleRate, outputSampleRate) {  
  if (outputSampleRate === inputSampleRate) {  
    return buffer;  
  }  
  
  const ratio = inputSampleRate / outputSampleRate;  
  const newLength = Math.round(buffer.length / ratio);  
  const result = new Float32Array(newLength);  
  
  let offsetResult = 0;  
  let offsetBuffer = 0;  
  
  while (offsetResult < result.length) {  
    const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);  
    let accum = 0;  
    let count = 0;  
  
    for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {  
      accum += buffer[i];  
      count++;  
    }  
  
    result[offsetResult] = count > 0 ? (accum / count) : 0;  
    offsetResult++;  
    offsetBuffer = nextOffsetBuffer;  
  }  
  
  return result;  
}  
  
function convertFloat32ToInt16PCM(float32Array) {  
  const int16 = new Int16Array(float32Array.length);  
  
  for (let i = 0; i < float32Array.length; i++) {  
    let s = Math.max(-1, Math.min(1, float32Array[i]));  
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;  
  }  
  
  return int16;  
}  
  
async function startMic() {  
  if (isMicStarting || isMicRecording) return;  
  
  try {  
    if (!currentSessionId) {  
      alert("先に会話開始してください");  
      return;  
    }  
  
    isMicStarting = true;  
    updateButtonStates();  
  
    await connectWebSocket();  
  
    mediaStream = await navigator.mediaDevices.getUserMedia({  
      audio: {  
        channelCount: 1,  
        echoCancellation: true,  
        noiseSuppression: true,  
        autoGainControl: true  
      }  
    });  
  
    audioContext = new (window.AudioContext || window.webkitAudioContext)();  
    await audioContext.resume();  
  
    console.log("audioContext.sampleRate =", audioContext.sampleRate);  
  
    sourceNode = audioContext.createMediaStreamSource(mediaStream);  
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);  
  
    const targetSampleRate = 24000;  
    let debugCount = 0;  
  
    processorNode.onaudioprocess = (event) => {  
      if (!ws || ws.readyState !== WebSocket.OPEN) {  
        return;  
      }  
  
      const inputData = event.inputBuffer.getChannelData(0);  
      const downsampled = downsampleBuffer(  
        inputData,  
        audioContext.sampleRate,  
        targetSampleRate  
      );  
      const pcm16 = convertFloat32ToInt16PCM(downsampled);  
  
      if (debugCount < 10) {  
        console.log("input length =", inputData.length);  
        console.log("downsampled length =", downsampled.length);  
        console.log("pcm16 byteLength =", pcm16.byteLength);  
        console.log("pcm16 sample[0..7] =", Array.from(pcm16.slice(0, 8)));  
        debugCount++;  
      }  
  
      ws.send(pcm16);  
    };  
  
    sourceNode.connect(processorNode);  
    processorNode.connect(audioContext.destination);  
  
    isMicRecording = true;  
    setMicStatus(`recording pcm ${audioContext.sampleRate}Hz -> 24000Hz`);  
    openTranscriptSidebar();  
  } catch (e) {  
    alert("録音開始に失敗しました\n" + e.message);  
    throw e;  
  } finally {  
    isMicStarting = false;  
    updateButtonStates();  
  }  
}  
  
async function stopMic() {  
  try {  
    if (processorNode) {  
      try { processorNode.disconnect(); } catch (_) {}  
      processorNode.onaudioprocess = null;  
      processorNode = null;  
    }  
  
    if (sourceNode) {  
      try { sourceNode.disconnect(); } catch (_) {}  
      sourceNode = null;  
    }  
  
    if (audioContext) {  
      try { await audioContext.close(); } catch (_) {}  
      audioContext = null;  
    }  
  
    if (mediaStream) {  
      mediaStream.getTracks().forEach(track => track.stop());  
      mediaStream = null;  
    }  
  
    isMicRecording = false;  
    setMicStatus("mic stopped");  
  } catch (e) {  
    alert("録音停止に失敗しました\n" + e.message);  
  } finally {  
    updateButtonStates();  
  }  
}  
  
async function commitAudio() {  
  try {  
    if (!ws || ws.readyState !== WebSocket.OPEN) {  
      alert("WebSocketが接続されていません");  
      return;  
    }  
  
    ws.send("__commit__");  
    setMicStatus("commit sent");  
  } catch (e) {  
    alert("commitに失敗しました\n" + e.message);  
  }  
}  
  
function truncateText(text, maxLength = 280) {  
  const s = String(text || "").trim();  
  if (s.length <= maxLength) return s;  
  return s.slice(0, maxLength) + "…";  
}  
  
function buildDocsDetailsHtml(docs = []) {  
  if (!docs.length) {  
    return "<div class='small'>取得文書はありません。</div>";  
  }  
  
  return `  
    <details class="assistant-details">  
      <summary>取得文書を表示 (${docs.length}件)</summary>  
      <div class="docs-list" style="margin-top: 10px;">  
        ${docs.map((doc, idx) => `  
          <div class="doc-item">  
            <strong>${idx + 1}. ${escapeHtml(doc.title || doc.file_name || "No Title")}</strong>  
            <div class="small" style="margin-top: 6px;">  
              ${escapeHtml(truncateText(doc.content || doc.chunk || doc.text || "", 220)).replaceAll("\n", "<br>")}  
            </div>  
          </div>  
        `).join("")}  
      </div>  
    </details>  
  `;  
}  
  
function buildFollowupChipsHtml(items = []) {  
  if (!items.length) return "";  
  
  return `  
    <div class="followup-chips">  
      ${items.map(text => `  
        <button class="followup-chip" data-followup="${escapeHtml(text)}">${escapeHtml(text)}</button>  
      `).join("")}  
    </div>  
  `;  
}  
  
function buildAssistantChatHtml(reply, docs = [], searchQuery = "") {  
  const shortReply = truncateText(reply, 500);  
  
  return `  
    <div>${escapeHtml(shortReply).replaceAll("\n", "<br>")}</div>  
  
    ${buildFollowupChipsHtml([  
      "もっと短く",  
      "具体例を追加して",  
      "懸念点も教えて",  
      "箇条書きで整理して"  
    ])}  
  
    ${searchQuery ? `  
      <details class="assistant-details">  
        <summary>検索クエリを表示</summary>  
        <div style="margin-top: 8px;">${escapeHtml(searchQuery)}</div>  
      </details>  
    ` : ""}  
  
    ${reply && reply.length > 500 ? `  
      <details class="assistant-details">  
        <summary>全文を表示</summary>  
        <div style="margin-top: 8px; white-space: pre-wrap;">${escapeHtml(reply)}</div>  
      </details>  
    ` : ""}  
  
    ${buildDocsDetailsHtml(docs)}  
  `;  
}  
  
function restoreChatMessagesFromHistory(items = []) {  
  clearChatMessages();  
  
  if (!items || !items.length) {  
    renderChatMessages();  
    return;  
  }  
  
  items.forEach((msg) => {  
    const role = msg.role === "user" ? "user" : "assistant";  
    const content = String(msg.content || "").trim();  
  
    if (!content) return;  
  
    if (role === "user") {  
      pushChatMessage("user", escapeHtml(content).replaceAll("\n", "<br>"));  
    } else {  
      pushChatMessage("assistant", buildAssistantChatHtml(content, [], ""));  
    }  
  });  
}  
  
document.addEventListener("click", async (e) => {  
  const chip = e.target.closest(".followup-chip");  
  if (!chip) return;  
  
  const text = chip.getAttribute("data-followup") || "";  
  if (!text) return;  
  
  chatInput.value = text;  
  chatInput.focus();  
});  
  
function clearDebugInput() {  
  debugTextBox.value = "";  
  debugTextBox.focus();  
}  
  
/* Events */  
if (clearDebugInputBtn) {  
  clearDebugInputBtn.addEventListener("click", clearDebugInput);  
}  
  
if (toggleHistoryBtn) {  
  toggleHistoryBtn.addEventListener("click", toggleHistorySidebar);  
}  
  
if (startConversationBtn) {  
  startConversationBtn.addEventListener("click", startOrResumeConversation);  
}  
  
if (pauseConversationBtn) {  
  pauseConversationBtn.addEventListener("click", pauseConversation);  
}  
  
if (stopConversationBtn) {  
  stopConversationBtn.addEventListener("click", stopSession);  
}  
  
if (commitBtn) {  
  commitBtn.addEventListener("click", commitAudio);  
}  
  
if (refreshTranscriptBtn) {  
  refreshTranscriptBtn.addEventListener("click", () => refreshTranscript(false));  
}  
  
if (addDebugBtn) {  
  addDebugBtn.addEventListener("click", addDebugText);  
}  
  
if (refreshHistoryBtn) {  
  refreshHistoryBtn.addEventListener("click", refreshHistory);  
}  
  
if (backToCurrentBtn) {  
  backToCurrentBtn.addEventListener("click", backToCurrentSessionView);  
}  
  
if (toggleTranscriptBtn) {  
  toggleTranscriptBtn.addEventListener("click", () => {  
    if (transcriptSidebar.classList.contains("collapsed")) {  
      openTranscriptSidebar();  
    } else {  
      closeTranscriptSidebar();  
    }  
  });  
}  
  
if (closeTranscriptBtn) {  
  closeTranscriptBtn.addEventListener("click", closeTranscriptSidebar);  
}  
  
if (chatInput) {  
  chatInput.addEventListener("keydown", async (e) => {  
    if (e.key === "Enter" && !e.shiftKey) {  
      e.preventDefault();  
      await sendChat();  
    }  
  });  
}  
  
window.addEventListener("load", async () => {  
  await refreshHistory();  
  setApiStatus("API: ready");  
  renderChatMessages();  
  updateButtonStates();  
});  