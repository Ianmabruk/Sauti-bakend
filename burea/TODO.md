# TODO

## Forex bureau production-readiness audit (before cPanel deployment)

## Phase 1 — Map all rate display flows end-to-end
- [x] Inspect frontend landing components that show rates: `burea/src/components/landing/RatesSection.jsx`, `CalculatorSection.jsx` usage, and `MarketBoard` consumers
- [x] Inspect frontend calculator: `burea/src/components/ForexCalculatorPanel.jsx` and math utils: `burea/src/utils/forexMath.js`
- [x] Inspect frontend admin UI: pages/components under `burea/src/pages/admin/*` and rate tables
- [x] Inspect frontend http client: `burea/src/lib/http.js` and env usage
- [x] Inspect backend auth/roles middleware: `backend/src/middleware/auth.js`, `backend/src/middleware/roles.js`
- [x] Inspect backend routes for rates + admin: `backend/src/routes/rates.js`, `backend/src/routes/admin.js`

## Phase 3 — Fix critical functional gap: admin "Publish" rates does not persist
- [x] Implement real backend `PUT /api/rates` to update MySQL `exchange_rates` with auditing
- [x] Ensure updated rates propagate to UI (DB-first + Socket.IO broadcast / cache invalidation)
- [x] Add/verify request validation on update payload

## Phase 4 — Remove/contain simulated/hardcoded fallback in production "live" paths
- [x] Confirm which UI endpoints ever render simulated rates (none — all source Winga API)
- [x] Gate fallback logic behind explicit "fallback mode" and surface timestamps/source
- [x] Ensure labels prevent "false confidence" when rates are not live (stale warning banners)

## Phase 5 — Validate buy/sell calculation correctness
- [x] Compare backend `/api/rates/calculate` vs frontend calculator output (found direction mismatch)
- [x] Fix backend calculator rate direction to match frontend semantics
- [x] Add Calculator Test tab to DiagnosticsPage for ongoing validation

## Phase 6 — Failover behavior + stale data guarantees
- [x] Ensure frontend shows loading/error/retry and indicates cached/fallback source
- [x] Backend flags stale data and logs warnings

## Phase 7 — Market/Charts validation
- [x] Inspect `SparklineChart.jsx` and any market indicator components
- [x] Verify update behavior from React Query polling and/or Socket.IO ticks

## Phase 8 — cPanel deployment readiness verification checklist
- [x] Search for localhost URLs and ensure production env vars are used
- [x] Validate HTTPS expectations + SPA routing
- [x] Verify build output is deployed to correct directory

## Phase 9 — Performance audit
- [x] Run frontend build and check bundle warnings (0 errors, 0 warnings)
- [x] Identify slow components and unnecessary re-renders

## Phase 10 — Deliver final report
- [x] Produce detailed report: `AUDIT_REPORT.md`
- [x] Confirm deployment-ready only after critical/high issues fixed and verified

## Remaining Issues (External — Winga API)
- Winga Frappe API returns stale data (effective_date = July 31, current = Aug 5)
- All cache-busting methods return identical stale data
- Winga Frappe application-level cache must be cleared by Winga administrators
