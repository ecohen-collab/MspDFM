---
name: Swagger Artis v2
description: URL de la doc OpenAPI/Swagger Artis v2 — référence pour découvrir endpoints, payloads, codes réponse
type: reference
originSessionId: decc76e7-3fd8-4af4-9cda-e2cefc2f50f1
---
URL Swagger Artis v2 (HTTPS) :
`https://artis.dfm.fr/ArtisWeb/services/api-docs/v2/desc.json?scheme=https:`

Lecture directe :
```bash
curl -s "https://artis.dfm.fr/ArtisWeb/services/api-docs/v2/desc.json?scheme=https:" | jq '.paths | keys'
```

Permet de découvrir les endpoints sans probe destructif. Exhaustif sur v2 (PATCH, POST, DELETE inclus).

Note : certains endpoints visibles dans l'UI Artis Web ne sont **pas** dans le Swagger (ex. modification d'un CRIT existant — l'API n'expose que la création).
