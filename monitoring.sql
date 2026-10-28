-- Runnable SQLite queries: the demo executes and exports all three.
-- 1. Every applied request must have an independent recorded approval.
SELECT request_id, supplier_id, requester, approver
FROM requests
WHERE status = 'APPLIED'
  AND (approver IS NULL OR approver = requester OR approver = validator
       OR approved_at IS NULL OR approved_at > applied_at);

-- 2. Master/ERP reconciliation: check every field, not only a version number.
-- Uses SQLite JSON1, provided by supported modern Python SQLite builds.
SELECT s.supplier_id, s.version AS master_version, e.version AS erp_version
FROM suppliers s LEFT JOIN erp_snapshot e USING (supplier_id)
WHERE e.supplier_id IS NULL OR s.version != e.version
   OR s.supplier_name IS NOT json_extract(e.payload, '$.supplier_name')
   OR s.country IS NOT json_extract(e.payload, '$.country')
   OR s.tax_id IS NOT json_extract(e.payload, '$.tax_id')
   OR s.payment_terms IS NOT json_extract(e.payload, '$.payment_terms')
   OR s.bank_account IS NOT json_extract(e.payload, '$.bank_account');

-- 3. Open issues remain visible. A cleaner dataset alone does not close them.
SELECT issue_id, request_id, rule_id, owner, status, root_cause
FROM issues WHERE status != 'CLOSED';
