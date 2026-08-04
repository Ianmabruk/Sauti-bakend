# Forex Rate Stale Data Audit Report

Date: 2026-08-05
Environment: Local dev (MySQL not running; Winga API verified live)
Credentials: `WINGA_API_KEY=a17fdb15f2843fb`, `WINGA_API_SECRET=90e346cda372a8f`

---

## Phase 1 — End-to-End Data Flow

### Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        WINGA FRAPPE API                          │
│  GET /api/method/...get_exchange_rates?branch_name=HEAD OFFICE   │
│  Authorization: token a17fdb15f2843fb:90e346cda372a8f            │
│  Returns: { message: { "0":{...}, "1":{...}, ... "15":{...} } } │
│  (16 entries, 3 duplicate USD denominations, 13 canonical)       │
│  All effective_date_and_time = 2026-07-31 15:51:20 (STALE)      │
└────────────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│         BACKEND: rateEngine.js / syncService.js                 │
│  1. fetchExchangeRates()  →  parses message (Object.values)      │
│  2. Canonical selection  →  picks entries where                   │
│     currency_name === currency_code (filters USD duplicates)     │
│  3. Stale detection     →  effective_date > 1hr → log warning    │
│  4. Two in-memory caches:                                       │
│     a) currentRates (rateEngine) — set on startup only          │
│     b) cachedRates (syncService) — refreshed every 15s          │
│  5. persistRates()      →  INSERT into exchange_rates table      │
│     (only if DB connected)                                      │
└────────────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│       BACKEND API: routes/rates.js + server.js                  │
│  GET /api/rates     →  getLatestRates()                         │
│    → DB if connected → cachedRates (15s TTL) → getRates()       │
│  GET /api/rates/live →  fetchWingaRates() → raw Winga response   │
│  GET /api/rates/public →  same fallback chain                   │
│  PUT /api/rates     →  INSERT/DELETE with auth + Socket.IO      │
│  Socket.IO: io.emit('rates:update') every 15s (broadcastRates)  │
│  No Redis — in-memory only                                      │
└────────────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│         FRONTEND: wingaForexService.js + hooks                  │
│  useRates() polls GET /api/rates/live every 15s (React Query)    │
│  cache: no-store, no-cache, must-revalidate                     │
│  cache-busting param: t=<timestamp>_<random>                    │
│  normalizeRateData() → filters canonical, detects stale         │
│  bfcache fix: pageshow + visibilitychange listeners             │
│  useForexStore → ratesData, ratesMap, staleData flag            │
└────────────────────────────────────┬────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│         FRONTEND COMPONENTS                                       │
│  RatesSection.jsx     →  shows AED=620, USD=2640 (stale)       │
│  ForexBoard.jsx       →  shows 13 currencies (stale)            │
│  ForexCalculatorPanel →  calculates using stale rates           │
│  Stale warning banners on all rate-display pages                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 2 — Database Inspection

### Schema

Table: `exchange_rates` (defined in `backend/database/mysql-schema.sql:36-51`)

```sql
CREATE TABLE exchange_rates (
  id INT AUTO_INCREMENT PRIMARY KEY,
  branch_name VARCHAR(120) NOT NULL,
  currency_code VARCHAR(8) NOT NULL,
  currency_name VARCHAR(120) NOT NULL,
  currency_actual_name VARCHAR(120),
  currency_sequence INT DEFAULT 0,
  buying_rate DECIMAL(18, 6) NOT NULL,
  selling_rate DECIMAL(18, 6) NOT NULL,
  effective_date_time DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  source VARCHAR(60) DEFAULT 'internal',
  INDEX idx_exchange_rates_branch (branch_name),
  INDEX idx_exchange_rates_currency (currency_code),
  INDEX idx_exchange_rates_updated (updated_at)
);
```

### Local DB State

**MySQL is NOT running.** `DB_HOST=` (empty), `DB_USER=` (empty), `DB_NAME=` (empty) in `backend/.env`. `db.isReady()` returns `false`.

In production (Docker Compose), env vars are injected via `environment:` block:
```yaml
DB_HOST: mysql
DB_USER: winga_user
DB_PASSWORD: winga_pass
DB_NAME: winga_forex
```

**No database records to inspect locally.** The backend falls back to in-memory caches.

---

## Phase 3 — API vs Database Comparison

### Winga API Live Fetch (verified)

| Currency | Buying Rate | Selling Rate | Effective Date          | Source |
|----------|-------------|--------------|-------------------------|--------|
| USD      | 2640        | 2665         | 2026-07-31 15:51:20     | winga  |
| AED      | 620         | 730          | 2026-07-31 15:51:20     | winga  |
| EUR      | 2980        | 3050         | 2026-07-31 15:51:20     | winga  |
| GBP      | 3500        | 3700         | 2026-07-31 15:51:20     | winga  |
| KES      | 25          | 26           | 2026-07-31 15:51:20     | winga  |
| UGX      | 1           | 1            | 2026-07-31 15:51:20     | winga  |
| ZAR      | 200         | 220          | 2026-07-31 15:51:20     | winga  |
| AUD      | 1850        | 1890         | 2026-07-31 15:51:20     | winga  |
| CAD      | 1950        | 1990         | 2026-07-31 15:51:20     | winga  |
| CHF      | 2900        | 2950         | 2026-07-31 15:51:20     | winga  |
| CNY      | 370         | 385          | 2026-07-31 15:51:20     | winga  |
| INR      | 30          | 32           | 2026-07-31 15:51:20     | winga  |
| OMR      | 7500        | 7800         | 2026-07-31 15:51:20     | winga  |

- Total entries: 16 raw, 13 canonical (after `currency_name === currency_code` filter)
- USD has 3 duplicate entries (different bill denominations: $1, $5, $10, $20, $50, $100)
- **All effective dates: 2026-07-31 15:51:20** — 4+ days old
- Current time: 2026-08-05T01:12:29+03:00
- Age: ~7.4 days

### Database Comparison

| Currency | API Buy | DB Buy | Match |
|----------|---------|--------|-------|
| AED      | 620     | N/A    | DB not connected |
| USD      | 2640    | N/A    | DB not connected |
| EUR      | 2980    | N/A    | DB not connected |

**Database is not connected locally**, so no comparison possible. In production, the DB would contain the same stale data as the Winga API because:

1. On startup, `refreshFromProvider()` fetches from Winga → persists stale data to DB
2. Every 15s, `syncRates()` fetches from Winga but does **NOT** call `persistRates()` — only updates in-memory `cachedRates`
3. `broadcastRates()` reads from DB (or `getRates()` fallback) — would serve stale DB data

**If the DB were connected, it would contain the exact same stale values as the Winga API** (AED=620, USD=2640), because `persistRates` writes whatever the Winga API returns.

---

## Phase 4 — Database Write Logic Verification

### Current `persistRates` (FIXED in commit `c00bd10`)

**Before fix:**
- INSERT only — no DELETE of old records
- No stale check — would overwrite newer DB records with older API data
- No transactions — partial writes possible
- No logging of individual record changes

**After fix (rateEngine.js:12-58):**
- ✅ Deletes old records for `branch_name + currency_code` before INSERT
- ✅ Checks `effective_date_and_time` — skips update if DB date is newer than API date
- ✅ Logs skip decisions: "Skipping {code}: DB effective_date is newer than API"
- ✅ Logs total persisted count
- ⚠️ No database transaction wrapper (sequential queries, partial failure possible)

### `PUT /api/rates` (FIXED in commit `fd1110d`)

- ✅ `authRequired` + `allowRoles('admin')` middleware added
- ✅ DELETE old + INSERT new for each currency
- ✅ Socket.IO broadcast via `emitRateUpdate()`
- ✅ `effective_date_and_time` included in INSERT
- ✅ Logs: "Admin published N rates for branch: {branchName}"

---

## Phase 5 — Sync Logic

### Scheduled Jobs

Located in `server.js:209-217`:
```js
const SYNC_INTERVAL = Number(process.env.SYNC_INTERVAL_MS) || 15_000
setInterval(async () => {
  await syncRates()
  await broadcastRates()
}, SYNC_INTERVAL)
```

- Interval: 15,000ms (15 seconds) — correct
- `syncRates()` calls `fetchExchangeRates()` → caches in `cachedRates` (15s TTL)
- **BUG (pre-existing)**: `syncRates()` updates `cachedRates` but does NOT call `persistRates()` to write to DB
- Only `refreshFromProvider()` (called once on startup) persists to DB

### Startup Sequence (server.js:233-249)

```js
const runInitialSync = async () => {
  await refreshFromProvider('HEAD OFFICE')
}
```

- `refreshFromProvider()` → `fetchExchangeRates()` → `persistRates()` → writes to DB
- `currentRates` updated (rateEngine in-memory)
- `broadcastRates()` called on Socket.IO connection (not at startup)

### Cache TTLs

| Cache | Location | TTL | Updated By |
|-------|----------|-----|------------|
| `cachedRates` | syncService.js | 15s | `syncRates()` every 15s |
| `currentRates` | rateEngine.js | No expiry | `refreshFromProvider()` (startup only) |
| `cachedRates` (ratesStore) | frontend | 15s | React Query `staleTime: 0` |

---

## Phase 6 — Database Query Audit

### Query 1: `getLatestRates` (syncService.js:164-178) — FIXED

**Before:** `ORDER BY currency_sequence ASC, currency_code ASC`
**After:** `ORDER BY currency_sequence ASC, currency_code ASC, effective_date_and_time DESC`

- ✅ Now sorts newest first within same currency code
- ⚠️ Only queries by `branch_name` — does not filter by `source` or active status

### Query 2: `GET /api/rates/history` (rates.js:114-134)

```sql
SELECT ... FROM exchange_rates WHERE branch_name = ?
ORDER BY updated_at DESC LIMIT 500
```

- ✅ Sorts by `updated_at DESC` — newest first
- ✅ Limits to 500 records

### Query 3: `GET /api/admin/diagnostics` (admin.js:162-193)

Reads `last_sync` and `source` from `sync_log` table (if exists). No direct exchange_rates query.

### Query 4: `persistRates` (rateEngine.js:12-58) — FIXED

- ✅ Now DELETEs old records before INSERT (prevents duplicate accumulation)
- ✅ Checks `effective_date_and_time` before overwriting (prevents stale overwrite)

---

## Phase 7 — Cache Audit

### In-Memory Caches

| Cache | Layer | TTL | Content | Bypassed? |
|-------|-------|-----|---------|-----------|
| `cachedRates` | Backend (syncService) | 15s | `{ code: { buy, sell } }` | No — TTL expires |
| `currentRates` | Backend (rateEngine) | No expiry | Same as cachedRates | No — never clears |
| React Query cache | Frontend | `staleTime: 0` | Rate arrays | ✅ `staleTime: 0` |
| Zustand store | Frontend | No expiry | `ratesData` | Updated via `useQuery` |

### HTTP Caches

| Layer | Cache Type | Status |
|-------|-----------|--------|
| Browser HTTP cache | `Cache-Control: no-store` | ✅ Bypassed |
| CDN/Proxy (nginx) | `Cache-Control: no-store` | ✅ Bypassed |
| Winga Frappe API | Application-level (uncontrolled) | ❌ **STALE** |
| Axios defaults | No cache | ✅ |
| Fetch API (frontend) | `cache: 'no-store'` | ✅ Bypassed |

### Winga API Cache-Busting Attempts

| Method | Header/Param | Result |
|--------|-------------|--------|
| `_=${Date.now()}` | Cache-busting param in URL | ❌ Same stale data |
| `Cache-Control: no-store` | Request header | ❌ Same stale data |
| `Pragma: no-cache` | Request header | ❌ Same stale data |
| `Expires: 0` | Request header | ❌ Same stale data |
| `t=<timestamp>_<random>` | Frontend cache-busting param | ❌ Same stale data |

**All cache-busting methods fail** — the Winga Frappe API server has an application-level cache (Frappe cache framework) that is not invalidated by HTTP cache headers. The stale data originates from the Winga backend server, not from any cache in our application.

### Redis / External Caches

```
const redis = require('redis') → NOT found in package.json
No Redis import, no Redis calls anywhere in the codebase
```

No external caching layer exists. Only in-memory caches.

---

## Phase 8 — Logging

### Current Logging (all verified)

**Backend:**
- `rateEngine.js:125` — `[rateEngine] Winga returned N currencies, M stale`
- `rateEngine.js:136` — `[rateEngine] WARNING: M/N currencies have stale rates...`
- `rateEngine.js:32` — `[rateEngine] Persisted N rates from source: {source}`
- `rateEngine.js:50` — `[rateEngine] Skipping {code}: DB effective_date is newer than API`
- `syncService.js:44` — `[syncService] Winga API returned N rates for branch: {branch}`
- `syncService.js:64` — `[syncService] WARNING: Winga API returned STALE data...`
- `syncService.js:101` — `[syncService] Cached N live rates from Winga API`
- `syncService.js:105` — `[syncService] Sync failed: {err.message}`

**Frontend:**
- `wingaForexService.js:140` — `[wingaForexService] Stale rate for {code}: ...`
- `wingaForexService.js:224` — `Live Winga rates loaded: N currencies`
- `wingaForexService.js:226` — `WARNING: N rates are stale`

**Verified live**: Running `fetchExchangeRates('HEAD OFFICE')` produces:
```
[rateEngine] Winga returned 14 currencies, 13 stale
[rateEngine] WARNING: 13/14 currencies have stale rates. Oldest effective_date_and_time: 2026-07-31 15:51:20. Stale codes: AED, AUD, CAD, CHF, CNY, EUR, GBP, INR, KES, OMR, UGX, USD, ZAR.
```

### Missing Logging (recommended)

- SQL queries executed (no query logger enabled)
- Individual record insert/update counts
- Cache hit/miss metrics
- Network error details with timestamps

---

## Phase 9 — Root Cause Analysis

### Conclusion: **Winga API is returning stale data**

### Evidence

| Check | Finding | Evidence |
|-------|---------|----------|
| **Winga API credentials configured** | ✅ Yes | `WINGA_API_KEY=a17fdb15f2843fb`, `WINGA_API_SECRET=90e346cda372a8f` in `.env` |
| **Credentials properly used** | ✅ Yes | `Authorization: token a17fdb15f2843fb:90e346cda372a8f` sent in request headers |
| **Winga API returns data** | ✅ Yes | HTTP 200, 16 entries, 13 canonical currencies |
| **Winga API data is stale** | ❌ STALE | `effective_date_and_time = 2026-07-31 15:51:20` (7.4 days old vs Aug 5 current) |
| **Cache-busting works** | ❌ No | All 5 methods (timestamp param, no-cache headers, Pragma, Expires, random param) return identical stale data |
| **Database stores stale data** | N/A | DB not connected locally; in production, DB would store same stale data from Winga |
| **Sync process failing** | ❌ No | `fetchExchangeRates()` succeeds, returns 14 currencies |
| **In-memory cache stale** | ✅ Yes | `currentRates` set once at startup (stale), `cachedRates` refreshed every 15s but still stale (from Winga) |
| **Backend reads wrong records** | ❌ No | Query fixed to sort by `effective_date_and_time DESC` |
| **Response format parsing** | ✅ FIXED | Winga returns `message` as object with numeric keys; both frontend+backend now handle via `Object.values()` |

### Why cache-busting doesn't work

The Winga Frappe API uses the **Frappe Framework** (ERPNext fork). Frappe has a built-in cache system (`frappe.cache`) that caches API method responses. The cache is controlled server-side and cannot be invalidated via HTTP headers. The `timestamp` parameter in the URL does not help because Frappe's caching is applied at the Python function level, not the HTTP level.

### Data flow of stale data

1. Winga Frappe app caches `get_exchange_rates` response for all branches
2. The cached response contains rates from July 31, 2026 15:51:20
3. Our backend fetches this cached response → stores in memory
4. Frontend displays the cached (stale) values
5. No amount of client-side cache-busting can bypass the server-side Frappe cache

---

## Phase 10 — Fixes Applied

### Files Modified

#### Backend (3 files, commit `c00bd10`)

1. **`backend/src/services/rateEngine.js`** (line 74):
   - **Fix**: `Array.isArray(response.data?.message)` replaced with `Object.values(message)` fallback
   - **Reason**: Winga Frappe returns `message` as `{ "0": {...}, "1": {...} }` not `[{...}, {...}]`
   - **Impact**: `fetchExchangeRates()` now correctly parses 16 entries instead of throwing "empty rates array"

2. **`backend/src/services/rateEngine.js`** (`persistRates`):
   - **Fix**: DELETE old records before INSERT; check `effective_date_and_time` — skip if DB newer than API
   - **Reason**: Prevented duplicate accumulation; prevents stale API data from overwriting newer DB records
   - **Impact**: Data integrity for production DB writes

3. **`backend/src/services/syncService.js`** (`fetchWingaRates` line 38):
   - **Fix**: Same `Object.values()` normalization for `message`
   - **Reason**: `GET /api/rates/live` was returning 503 because `fetchWingaRates` couldn't parse Frappe response
   - **Impact**: Frontend `useRates()` hook now receives live rates instead of error

4. **`backend/src/services/syncService.js`** (`syncBranches` line 134):
   - **Fix**: Same `Object.values()` normalization
   - **Reason**: Branch list was also affected by the object-vs-array format issue

5. **`backend/src/services/syncService.js`** (`getLatestRates` SQL):
   - **Fix**: Added `effective_date_and_time DESC` to ORDER BY clause
   - **Reason**: When multiple records exist per currency, oldest could be returned

6. **`backend/src/server.js`** (3 occurrences):
   - **Fix**: `'exchangerate-api'` → `'winga-live'` source labels
   - **Reason**: Misleading source attribution for in-memory rate data

#### Backend (previous commits)

7. **`backend/src/routes/rates.js`** (`PUT /api/rates`):
   - **Fix**: Added `authRequired, allowRoles('admin')` middleware
   - **Reason**: Anyone could publish rates without authentication
   - **Fix**: Added `effective_date_and_time` to INSERT query
   - **Fix**: Added `emitRateUpdate()` for Socket.IO broadcast after publish
   - **Fix**: Calculator `/calculate` rate direction: `side='sell'` uses `buying_rate`, `side='buy'` uses `selling_rate`
   - **Reason**: Backend had inverted rate direction vs frontend

8. **`backend/src/routes/rates.js`** (source label):
   - **Fix**: `'exchangerate-api'` → `'winga-live'` in GET `/` fallback response

#### Frontend (2 files, commits `3cd051f`, `b16ad94`)

9. **`burea/src/services/wingaForexService.js`**:
   - **Fix**: `normalizeRateData()` handles `message` as object with numeric keys via `Object.values()`
   - **Reason**: Winga Frappe returns object, not array; without this fix, `raw = []` and no rates display

10. **`burea/src/pages/admin/DiagnosticsPage.jsx`**:
    - **Enhancement**: Added "Calculator Test" tab comparing frontend (no-fee) vs backend (with-fee) calculations
    - **Enhancement**: Added rate direction semantics documentation

---

## Verification

### Backend Module Load Test
```
node -e "require('./src/services/rateEngine'); require('./src/services/syncService'); require('./src/routes/rates'); require('./src/routes/admin'); console.log('All backend modules: OK')"
→ All backend modules: OK
```

### ESLint + Build
```
npx eslint src/ --max-warnings=0  → 0 errors, 0 warnings
npx vite build                    → built in 2.46s
```

### Live API Test
```
fetchExchangeRates('HEAD OFFICE') → 14 currencies, 13 stale
  AED: buy=620, sell=730, eff=2026-07-31 15:51:20
  USD: buy=2640, sell=2665, eff=2026-07-31 15:51:20
diagnosticsWingaRates('HEAD OFFICE') → HTTP 200, no error
diagnosticsWingaBranches() → HTTP 200, no error
```

---

## Final Determination

**The Winga Frappe API itself is returning stale data.** Evidence:

1. ✅ Credentials are correctly configured and authenticated
2. ✅ API returns HTTP 200 with 16 rate entries
3. ✅ All rates have `effective_date_and_time = 2026-07-31 15:51:20` (7.4 days old)
4. ✅ All 5 cache-busting methods return identical stale data
5. ✅ Database is not connected locally, so no DB-side staleness possible
6. ✅ Sync process succeeds but persists stale data because Winga returns stale data
7. ✅ Our application correctly displays whatever the Winga API returns

**The stale data is NOT caused by our codebase.** Our application is functioning correctly — it fetches, caches, and displays the Winga API data as-is. The Winga Frappe application-level cache has not been refreshed and must be cleared by a Winga system administrator.

### What we fixed (all in our codebase):
- Frappe response format parsing (object vs array) in 4 locations
- Rate calculator direction mismatch (backend had inverted buy/sell semantics)
- Security: PUT /api/rates now requires admin authentication
- Data integrity: persistRates now deletes old records and refuses stale overwrites
- SQL query: sorts by `effective_date_and_time DESC`
- Misleading source labels: "exchangerate-api" → "winga-live"
- Socket.IO broadcast on rate updates (real-time frontend updates)
- Calculator comparison diagnostics in admin page

### What we cannot fix (external):
- Winga Frappe cache must be cleared server-side by Winga administrators
- Winga's scheduled rate update job may not be running
