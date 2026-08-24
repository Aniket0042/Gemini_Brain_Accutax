-- =============================================================================
-- Migration: 002_fn_inventory_movement.sql
-- Purpose: Inventory movement across items, warehouse locations, sales invoice units, and delivery notes.
-- Specs: LANGUAGE sql STABLE PARALLEL SAFE, parameter binding, LIMIT 500.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_inventory_movement(
    p_org INT,
    p_from DATE DEFAULT '2020-01-01',
    p_to DATE DEFAULT '2099-12-31'
)
RETURNS TABLE (
    item_id INT,
    item_name VARCHAR,
    sku VARCHAR,
    warehouse_name VARCHAR,
    units_sold_invoices NUMERIC,
    units_dispatched_delivery_notes NUMERIC
)
LANGUAGE sql
STABLE
PARALLEL SAFE
AS $$
    WITH sales_summary AS (
        SELECT
            ii.items_id AS item_id,
            COALESCE(SUM(ii.quantity), 0) AS total_units_sold
        FROM income_items ii
        JOIN income inc ON inc.id = ii.income_id
        WHERE inc.organization_id = p_org
          AND (CAST(inc.invoice_date AS DATE) >= p_from AND CAST(inc.invoice_date AS DATE) <= p_to)
        GROUP BY ii.items_id
    ),
    delivery_summary AS (
        SELECT
            dnl.item_id,
            COALESCE(SUM(dnl.quantity_delivered), 0) AS total_units_dispatched
        FROM delivery_notes dn
        JOIN delivery_note_lines dnl ON dnl.delivery_note_id = dn.id
        WHERE dn.organization_id = p_org
          AND (CAST(dn.created_at AS DATE) >= p_from AND CAST(dn.created_at AS DATE) <= p_to)
        GROUP BY dnl.item_id
    )
    SELECT
        it.id AS item_id,
        COALESCE(it.name, 'Unnamed Item')::VARCHAR AS item_name,
        COALESCE(it.sku, 'N/A')::VARCHAR AS sku,
        COALESCE(w.warehouse_name, 'Default Warehouse')::VARCHAR AS warehouse_name,
        COALESCE(ss.total_units_sold, 0)::NUMERIC AS units_sold_invoices,
        COALESCE(ds.total_units_dispatched, 0)::NUMERIC AS units_dispatched_delivery_notes
    FROM items it
    LEFT JOIN warehouses w ON (w.id = it.default_warehouse_id OR w.id = it.warehouse_id)
    LEFT JOIN sales_summary ss ON ss.item_id = it.id
    LEFT JOIN delivery_summary ds ON ds.item_id = it.id
    WHERE it.organization_id = p_org
    ORDER BY units_sold_invoices DESC, it.name ASC
    LIMIT 500;
$$;
