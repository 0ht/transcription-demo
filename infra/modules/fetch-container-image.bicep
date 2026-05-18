// ==============================================================================
// Fetch existing Container App image (Microsoft 公式パターン)
// 既存 Container App が存在する場合、その image URL を取得して返す。
// 初回デプロイ時 (exists=false) は空配列を返す。
// 出典: https://github.com/microsoft/azure-container-apps/tree/main/templates/bicep/ruleBasedRouting
// ==============================================================================
param exists bool
param name string

resource existingApp 'Microsoft.App/containerApps@2024-03-01' existing = if (exists) {
  name: name
}

output containers array = exists ? existingApp!.properties.template.containers : []
