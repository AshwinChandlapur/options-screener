// =============================================================================
// Event Hubs — Basic SKU, 1 throughput unit.
//
// One topic:
//   - reddit-raw-events  (4 partitions, 1d retention)  ← ingestion publishes
//
// Basic SKU only allows ONE consumer group per topic. The extractor consumes
// reddit-raw-events and writes signals directly to Cosmos DB (ADR-0015).
// Downstream aggregation (Phase 3+) polls Cosmos DB, not Event Hubs.
//
// Removed: ticker-events hub (dead infra — see ADR-0015).
// See ADR-0014 for the cost rationale (~$11/mo vs ~$45/mo for Standard 2 TU).
// =============================================================================

@description('Azure region.')
param location string

@description('Suffix for the Event Hubs namespace name.')
param nameSuffix string

@description('Tags applied to the namespace.')
param tags object

var namespaceName = 'evhns-narrative-${nameSuffix}'

resource ns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: namespaceName
  location: location
  tags: tags
  sku: {
    name: 'Basic'
    tier: 'Basic'
    capacity: 1
  }
  properties: {
    isAutoInflateEnabled: false
    minimumTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled' // Phase 6: tighten if needed
  }
}

resource rawEvents 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ns
  name: 'reddit-raw-events'
  properties: {
    partitionCount: 4
    messageRetentionInDays: 1 // Basic SKU max
  }
}

output namespaceName string = ns.name
output namespaceId string = ns.id
output rawEventsName string = rawEvents.name
