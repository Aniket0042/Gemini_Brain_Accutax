# PII leak report

**144 of 162 model calls (89%) carried personal or customer data.** Chats: 0. Period: 2026-10-17T08:57:20+00:00 to 2026-10-17T09:13:46+00:00.

Counts are occurrences in what was sent. The agent re-sends the conversation on every step, so one value is counted once per call it rides in. *Distinct* counts each value once.

## By call site

| Call site | Calls | With PII | Values sent | Free-text fields | Files (size) |
| --- | ---: | ---: | ---: | ---: | ---: |
| agent | 143 | 143 | 4499 | 230 | 0 |
| agent_law | 19 | 1 | 1 | 0 | 0 |

## By kind

| Kind | Occurrences | Distinct values | Calls |
| --- | ---: | ---: | ---: |
| PARTY_NAME | 849 | 151 | 12 |
| ORG_NAME | 3650 | 11 | 143 |
| EMAIL | 1 | 1 | 1 |

## Where in the payload

| Where | PARTY_NAME | ORG_NAME | EMAIL |
| --- | ---: | ---: | ---: |
| System prompt | 0 | 1305 | 1 |
| User text (question, history, file block) | 0 | 8 | 0 |
| Earlier AI answers | 0 | 184 | 0 |
| Tool results (books) | 849 | 2153 | 0 |

## Masked samples

| Kind | Where | Field | Preview | Call site |
| --- | --- | --- | --- | --- |
| PARTY_NAME | Tool results (books) | purchases.vendor_name | `A•••••` | agent |
| PARTY_NAME | Tool results (books) | purchases.vendor_name | `V••••` | agent |
| PARTY_NAME | Tool results (books) | purchases.vendor_name | `T••••` | agent |
| PARTY_NAME | Tool results (books) | purchases.vendor_name | `S••••••` | agent |
| PARTY_NAME | Tool results (books) | purchases.vendor_name | `M•••••` | agent |
| ORG_NAME | System prompt |  | `A•••••••••• & F•••••••••…` | agent |
| ORG_NAME | System prompt |  | `P••••••••••• & C••••••••…` | agent |
| ORG_NAME | System prompt |  | `P••••••••••• & C••••••••…` | agent |
| ORG_NAME | System prompt |  | `P••••••••••• & C••••••••…` | agent |
| ORG_NAME | System prompt |  | `A•••••••••• & F•••••••••…` | agent |
| EMAIL | System prompt |  | `i•••@t••.g••.a•` | agent_law |

Scan cost per call: median 5.0 ms, p95 15.5 ms, max 26.5 ms.
