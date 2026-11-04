function createProcessingStatus(item) {
  const labels = {
    queued: "待機中",
    processing: "処理中",
    completed: "完了",
    failed: "失敗",
    unknown: "状態不明",
  };
  const status = Object.hasOwn(labels, item.status) ? item.status : "unknown";
  const field = document.createElement("div");
  field.className = "processing-status-field";
  const badge = document.createElement("span");
  badge.className = `processing-status processing-status-${status}`;
  badge.textContent = `ステータス: ${labels[status]}`;
  field.appendChild(badge);
  if (item.error) {
    const error = document.createElement("div");
    error.className = "processing-error";
    error.textContent = item.error;
    field.appendChild(error);
  }
  return field;
}

function pollProcessingStatus(refresh, onError) {
  let busy = false;
  const timer = setInterval(async () => {
    if (busy || document.hidden) return;
    busy = true;
    try {
      await refresh();
    } catch (error) {
      onError(error);
    } finally {
      busy = false;
    }
  }, 5000);
  window.addEventListener("pagehide", () => clearInterval(timer), {once: true});
}
